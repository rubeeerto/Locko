from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

btnUrlChannel = InlineKeyboardButton(text="Подписаться 😌", url="https://t.me/+QoyBfdu4Q7U5ZmEy")
btnDoneSub = InlineKeyboardButton(text="Проверить подписку! ✅", callback_data="subchanneldone")

checkSubMenu = InlineKeyboardMarkup(inline_keyboard=[
    [btnUrlChannel],
    [btnDoneSub]
])
