import gspread
from oauth2client.service_account import ServiceAccountCredentials
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
import random
import string
import uuid
from datetime import datetime
import asyncio
import logging
from aiohttp import web

# -------------------- НАСТРОЙКИ (ЗАМЕНИТЕ НА СВОИ) --------------------
BOT_TOKEN = "8619051531:AAHBhCdVzttYcqGkQOKVC3tVx9xKjo4AxdQ"  # получите у @BotFather
WEB_APP_URL = "https://orion-game.vercel.app"  # ваш URL на Vercel
GOOGLE_SHEET_KEY_FILE = "service_account_key.json"  # имя файла с ключом
SPREADSHEET_ID = "1-NQwAAv9p23x0imAG7LGfZDJMHDj3xzS2bc4fQL6K6M"  # ID вашей Google Таблицы
ADMIN_IDS = [6381468157]  # ваш Telegram ID (администратор)

# -------------------- ИНИЦИАЛИЗАЦИЯ GOOGLE SHEETS --------------------
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_SHEET_KEY_FILE, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SPREADSHEET_ID)

# Листы
prizes_sheet = sheet.worksheet("Призы")
users_sheet = sheet.worksheet("Пользователи")
tickets_sheet = sheet.worksheet("Билеты")
codes_sheet = sheet.worksheet("Коды")
usages_sheet = sheet.worksheet("Использование кодов")

# -------------------- ИНИЦИАЛИЗАЦИЯ БОТА --------------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# -------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------
def get_user_id(telegram_id: int):
    """Ищет пользователя по Telegram ID, возвращает ID строки или None."""
    try:
        # столбец B (индекс 2) содержит telegram_id
        col_values = users_sheet.col_values(2)
        for i, val in enumerate(col_values):
            if val == str(telegram_id):
                return i + 1  # строки нумеруются с 1
    except:
        pass
    return None

def create_user(telegram_id: int, username: str, first_name: str):
    """Создаёт пользователя и возвращает его ID (номер строки)."""
    new_row = [str(uuid.uuid4()), str(telegram_id), username or "", first_name or "", str(datetime.now())]
    users_sheet.append_row(new_row)
    return len(users_sheet.col_values(1))  # номер последней строки

def get_or_create_user(telegram_id: int, username: str, first_name: str):
    row = get_user_id(telegram_id)
    if row is None:
        row = create_user(telegram_id, username, first_name)
    return row

def check_code_usage(user_row: int, code: str) -> bool:
    """Проверяет, использовал ли пользователь код."""
    try:
        records = usages_sheet.get_all_values()
        for r in records:
            if r and r[0] == str(user_row) and r[1] == code:
                return True
    except:
        pass
    return False

def record_code_usage(user_row: int, code: str):
    usages_sheet.append_row([str(user_row), code, str(datetime.now())])

def get_available_prizes():
    """Возвращает список доступных призов в виде словарей."""
    data = prizes_sheet.get_all_records()
    available = []
    for p in data:
        total = p.get('total_quantity')
        remain = p.get('remaining_quantity')
        if total == "" or total is None:
            available.append(p)
        elif remain and int(remain) > 0:
            available.append(p)
    return available

def select_random_prize(available_prizes):
    """Выбирает приз по весам и обновляет остаток в таблице."""
    total_weight = sum(int(p['weight']) for p in available_prizes)
    r = random.randint(1, total_weight)
    accum = 0
    chosen = None
    for p in available_prizes:
        accum += int(p['weight'])
        if r <= accum:
            chosen = p
            break
    if not chosen:
        chosen = available_prizes[-1]

    # Обновляем остаток, если приз не бесконечный
    if chosen['total_quantity'] not in [None, '']:
        # Найти строку приза по id (предполагаем, что id в столбце A)
        prize_id = str(chosen['id'])
        try:
            cell = prizes_sheet.find(prize_id, in_column=1)
            row = cell.row
            remain = int(chosen['remaining_quantity']) - 1
            prizes_sheet.update(f'E{row}', [[remain]])  # столбец E - remaining_quantity
        except:
            pass
    return chosen['id']

def generate_tickets(user_row: int, count: int):
    """Создаёт билеты для пользователя и возвращает список их ID."""
    available = get_available_prizes()
    if not available:
        return []
    ticket_ids = []
    for _ in range(count):
        prize_id = select_random_prize(available)
        ticket_id = str(uuid.uuid4())
        row_data = [ticket_id, str(user_row), prize_id, 'unopened', str(datetime.now()), '']
        tickets_sheet.append_row(row_data)
        ticket_ids.append(ticket_id)
        # после выбора приза нужно обновить список доступных
        available = get_available_prizes()
    return ticket_ids

def get_unopened_tickets(user_row: int):
    """Возвращает список неоткрытых билетов пользователя."""
    all_tickets = tickets_sheet.get_all_values()
    unopened = []
    for row in all_tickets[1:]:  # пропускаем заголовок
        if len(row) >= 4 and row[1] == str(user_row) and row[3] == 'unopened':
            unopened.append({'id': row[0]})
    return unopened

def open_ticket_in_sheet(ticket_id: str, user_row: int):
    """Открывает билет и возвращает информацию о призе."""
    all_tickets = tickets_sheet.get_all_values()
    for i, row in enumerate(all_tickets[1:], start=2):
        if row[0] == ticket_id and row[1] == str(user_row):
            if row[3] == 'opened':
                # уже открыт
                prize_id = row[2]
                prize_info = get_prize_by_id(prize_id)
                return prize_info, True
            # открываем
            prize_id = row[2]
            tickets_sheet.update(f'D{i}', [['opened']])
            tickets_sheet.update(f'F{i}', [[str(datetime.now())]])
            prize_info = get_prize_by_id(prize_id)
            return prize_info, False
    return None, False

def get_prize_by_id(prize_id: str):
    """Получает данные приза по ID."""
    try:
        cell = prizes_sheet.find(prize_id, in_column=1)
        row = prizes_sheet.row_values(cell.row)
        # предполагаем структуру: id, name, description, emoji, total, remain, weight, is_golden
        return {
            'name': row[1],
            'description': row[2],
            'emoji': row[3],
            'is_golden': row[7] == '1' or row[7].lower() == 'true'
        }
    except:
        return None

# -------------------- КОМАНДЫ БОТА --------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🍫 <b>ДОБРО ПОЖАЛОВАТЬ НА ФАБРИКУ ОРИОН!</b> 👑\n\n"
        "Чтобы открыть свои шоколадки, введите ниже код активации, который сообщил Вам администратор:",
        parse_mode="HTML"
    )

@dp.message(F.text)
async def handle_activation_code(message: types.Message):
    code = message.text.strip().upper()
    if code not in ["ШОКОЛАД1", "ШОКОЛАД2", "ШОКОЛАД3"]:
        # Возможно, это не код активации, просто игнорируем или подсказываем
        await message.reply("Пожалуйста, введите корректный код активации (ШОКОЛАД1, ШОКОЛАД2 или ШОКОЛАД3).")
        return

    # Проверяем существование кода в таблице "Коды"
    try:
        cell = codes_sheet.find(code, in_column=1)  # код в столбце A
        row = codes_sheet.row_values(cell.row)
        tickets_count = int(row[2])  # столбец C - tickets_count
    except:
        await message.reply("❌ Код не найден. Обратитесь к администратору.")
        return

    user_row = get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)

    if check_code_usage(user_row, code):
        await message.reply("❌ Вы уже использовали этот код. Для повторной активации обратитесь к менеджеру за другим кодом.")
        return

    # Генерируем билеты
    ticket_ids = generate_tickets(user_row, tickets_count)
    record_code_usage(user_row, code)

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🍫 Открыть мои шоколадки", web_app=WebAppInfo(url=WEB_APP_URL))]],
        resize_keyboard=True
    )
    await message.answer(
        f"✅ Код активирован! Вам начислено {tickets_count} золотых фантиков.\n"
        "Нажмите кнопку ниже, чтобы открыть их.",
        reply_markup=kb
    )

# Админ-команда /stats
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    prizes = prizes_sheet.get_all_records()
    msg = "📊 Остатки призов:\n\n"
    for p in prizes:
        total = p['total_quantity'] if p['total_quantity'] else '∞'
        remain = p['remaining_quantity'] if p['remaining_quantity'] else '∞'
        msg += f"{p['emoji']} {p['name']}: {remain} / {total}\n"
    await message.reply(msg)

# -------------------- API ДЛЯ MINI APP --------------------
async def api_my_tickets(request):
    telegram_id = request.headers.get('X-Telegram-Id')
    if not telegram_id:
        return web.json_response({'error': 'Missing Telegram ID'}, status=400)
    try:
        telegram_id = int(telegram_id)
    except:
        return web.json_response({'error': 'Invalid Telegram ID'}, status=400)

    user_row = get_user_id(telegram_id)
    if not user_row:
        return web.json_response({'tickets': []})
    tickets = get_unopened_tickets(user_row)
    return web.json_response({'tickets': tickets})

async def api_open_ticket(request):
    data = await request.json()
    ticket_id = data.get('ticket_id')
    telegram_id = request.headers.get('X-Telegram-Id')
    if not telegram_id or not ticket_id:
        return web.json_response({'error': 'Missing data'}, status=400)
    try:
        telegram_id = int(telegram_id)
    except:
        return web.json_response({'error': 'Invalid Telegram ID'}, status=400)

    user_row = get_user_id(telegram_id)
    if not user_row:
        return web.json_response({'error': 'User not found'}, status=404)

    prize_info, already = open_ticket_in_sheet(ticket_id, user_row)
    if not prize_info:
        return web.json_response({'error': 'Ticket not found or access denied'}, status=404)

    return web.json_response({'prize': prize_info, 'already_opened': already})

# -------------------- ЗАПУСК --------------------
async def main():
    # Проверяем наличие трёх кодов в таблице "Коды", если нет - добавляем
    existing_codes = codes_sheet.col_values(1)
    default_codes = ["ШОКОЛАД1", "ШОКОЛАД2", "ШОКОЛАД3"]
    for i, code in enumerate(default_codes, start=1):
        if code not in existing_codes:
            codes_sheet.append_row([code, f"{i} билет(а)", i])

    # Запуск поллинга
    polling_task = asyncio.create_task(dp.start_polling(bot))

    # Запуск веб-сервера для API
    app = web.Application()
    app.router.add_get('/api/my-tickets', api_my_tickets)
    app.router.add_post('/api/open-ticket', api_open_ticket)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logging.info("API server started on port 8080")

    await polling_task

if __name__ == "__main__":
    asyncio.run(main())
