import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import WebAppInfo
from aiogram.filters import Command

# Вставьте СВОЙ НОВЫЙ ТОКЕН (не тот, что был в чате!)
BOT_TOKEN = "ВАШ_НОВЫЙ_ТОКЕН_ОТ_BOTFATHER"

# Ссылка на вашу HTML-страницу. Для локального теста используйте ngrok
WEB_APP_URL = "https://your-ngrok-or-vercel-url.ngrok.io"  # замените

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_command(message: types.Message):
    # Создаём кнопку с Web App
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(
                text="🍫 Открыть фабрику чудес",
                web_app=WebAppInfo(url=WEB_APP_URL)
            )]
        ],
        resize_keyboard=True
    )
    await message.answer(
        "🎩 *Добро пожаловать на шоколадную фабрику Вонки!*\n\n"
        "У тебя есть 3 золотых фантика. Нажми на кнопку ниже, чтобы войти в мир чистого воображения...",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
