from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

btnUrlChannel = InlineKeyboardButton(text="Підписатись", url="https://t.me/+tod0WSFEpEQ2ODcy")
btnDoneSub = InlineKeyboardButton(text="Перевірити підписку", callback_data="subchanneldone")

checkSubMenu = InlineKeyboardMarkup(inline_keyboard=[
    [btnUrlChannel],
    [btnDoneSub]
])
