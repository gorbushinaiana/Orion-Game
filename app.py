import os
import asyncio
import logging
import random
import uuid
from datetime import datetime

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request, jsonify, render_template_string
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import threading

# -------------------- НАСТРОЙКИ --------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEB_APP_URL = os.environ.get("WEB_APP_URL")
GOOGLE_SHEETS_CREDS = os.environ.get("GOOGLE_SHEETS_CREDS")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "").split(",") if id]

# -------------------- ИНИЦИАЛИЗАЦИЯ GOOGLE SHEETS --------------------
def get_gsheet_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        eval(GOOGLE_SHEETS_CREDS), scope
    )
    return gspread.authorize(creds)

client = get_gsheet_client()
sheet = client.open_by_key(SPREADSHEET_ID)

prizes_sheet = sheet.worksheet("Призы")
users_sheet = sheet.worksheet("Пользователи")
tickets_sheet = sheet.worksheet("Билеты")
codes_sheet = sheet.worksheet("Коды")
usages_sheet = sheet.worksheet("Использование кодов")

# -------------------- ИНИЦИАЛИЗАЦИЯ БОТА --------------------
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# -------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------
def get_user_id(telegram_id: int):
    try:
        col = users_sheet.col_values(2)
        for i, val in enumerate(col):
            if val == str(telegram_id):
                return i + 1
    except:
        pass
    return None

def create_user(telegram_id: int, username: str, first_name: str):
    new_row = [str(uuid.uuid4()), str(telegram_id), username or "", first_name or "", str(datetime.now())]
    users_sheet.append_row(new_row)
    return len(users_sheet.col_values(1))

def get_or_create_user(telegram_id: int, username: str, first_name: str):
    row = get_user_id(telegram_id)
    if row is None:
        row = create_user(telegram_id, username, first_name)
    return row

def check_code_usage(user_row: int, code: str) -> bool:
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
    if chosen['total_quantity'] not in [None, '']:
        try:
            cell = prizes_sheet.find(str(chosen['id']), in_column=1)
            row = cell.row
            remain = int(chosen['remaining_quantity']) - 1
            prizes_sheet.update(f'E{row}', [[remain]])
        except:
            pass
    return chosen['id']

def generate_tickets(user_row: int, count: int):
    available = get_available_prizes()
    if not available:
        return []
    ticket_ids = []
    for _ in range(count):
        prize_id = select_random_prize(available)
        ticket_id = str(uuid.uuid4())
        tickets_sheet.append_row([ticket_id, str(user_row), prize_id, 'unopened', str(datetime.now()), ''])
        ticket_ids.append(ticket_id)
        available = get_available_prizes()
    return ticket_ids

# -------------------- КОМАНДЫ БОТА --------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🍫 <b>ДОБРО ПОЖАЛОВАТЬ НА ФАБРИКУ ОРИОН!</b> 👑\n\n"
        "Чтобы открыть свои шоколадки, введите ниже код активации, который сообщил Вам администратор:"
    )

@dp.message(F.text)
async def handle_activation_code(message: types.Message):
    code = message.text.strip().upper()
    if code not in ["ШОКОЛАД1", "ШОКОЛАД2", "ШОКОЛАД3"]:
        await message.reply("Пожалуйста, введите корректный код активации (ШОКОЛАД1, ШОКОЛАД2 или ШОКОЛАД3).")
        return
    try:
        cell = codes_sheet.find(code, in_column=1)
        row = codes_sheet.row_values(cell.row)
        tickets_count = int(row[2])
    except:
        await message.reply("❌ Код не найден. Обратитесь к администратору.")
        return
    user_row = get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if check_code_usage(user_row, code):
        await message.reply("❌ Вы уже использовали этот код. Для повторной активации обратитесь к менеджеру.")
        return
    generate_tickets(user_row, tickets_count)
    record_code_usage(user_row, code)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🍫 Открыть мои билеты", web_app=WebAppInfo(url=WEB_APP_URL))]],
        resize_keyboard=True
    )
    await message.answer(
        f"✅ Код активирован! Вам начислено {tickets_count} золотых фантиков.\nНажмите кнопку ниже, чтобы открыть их.",
        reply_markup=kb
    )

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

# -------------------- FLASK API --------------------
app = Flask(__name__)

@app.route('/')
def index():
    return render_template_string("""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>🍫 Золотой билет Орион</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/gsap.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/canvas-confetti@1"></script>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background: linear-gradient(145deg, #2b0b3f 0%, #4a1a5e 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: 'Georgia', 'Times New Roman', serif;
            padding: 16px;
            color: #f5e6d3;
        }
        .container {
            max-width: 400px;
            width: 100%;
            background: rgba(20, 5, 30, 0.7);
            backdrop-filter: blur(10px);
            border-radius: 40px;
            padding: 30px 20px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.5), 0 0 0 2px #b8860b inset;
            border: 1px solid #e4c580;
            text-align: center;
        }
        h1 {
            font-size: 28px;
            margin-bottom: 8px;
            text-shadow: 0 0 10px #ffd700;
            letter-spacing: 2px;
            color: #f3d779;
        }
        .subtitle {
            font-style: italic;
            margin-bottom: 30px;
            opacity: 0.9;
            font-size: 16px;
        }
        .shelf {
            display: flex;
            flex-direction: column;
            gap: 20px;
            margin-bottom: 30px;
        }
        .ticket-wrapper {
            perspective: 800px;
            cursor: pointer;
        }
        .ticket {
            background: #d4af37;
            background: linear-gradient(135deg, #f9e076 0%, #d4af37 40%, #aa7c11 100%);
            border-radius: 16px;
            padding: 20px;
            box-shadow: 0 8px 0 #6b4c0a, 0 10px 20px rgba(0,0,0,0.3);
            border: 2px solid #ffe9a7;
            position: relative;
            transition: transform 0.1s;
            min-height: 120px;
            display: flex;
            align-items: center;
            justify-content: center;
            transform-style: preserve-3d;
        }
        .ticket.opened {
            background: #e5d5b0;
            background: linear-gradient(135deg, #f5ecd9 0%, #dac292 100%);
            box-shadow: 0 4px 0 #7a5c2e, 0 6px 12px rgba(0,0,0,0.2);
            border-color: #b5975b;
        }
        .ticket-content {
            font-weight: bold;
            font-size: 22px;
            color: #2c1b0d;
            text-shadow: 1px 1px 0 #ffecb3;
            transition: opacity 0.3s;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            backface-visibility: hidden;
        }
        .ticket-icon { font-size: 36px; margin-bottom: 8px; }
        .prize-text { font-size: 18px; color: #2e1b0e; }
        .small-note { font-size: 12px; color: #5d4a2e; margin-top: 6px; }
        .golden-shine {
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            pointer-events: none; z-index: 999;
            background: radial-gradient(circle at center, rgba(255,215,0,0.4) 0%, transparent 70%);
            animation: goldenPulse 1.5s ease-out;
        }
        @keyframes goldenPulse {
            0% { opacity: 0; } 50% { opacity: 1; } 100% { opacity: 0; }
        }
        .footer { margin-top: 20px; font-size: 12px; opacity: 0.7; }
        .message-area { min-height: 40px; margin: 15px 0 5px; font-style: italic; color: #f0ddaa; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🍫 ОРИОН</h1>
        <div class="subtitle">Золотые билеты ждут</div>
        <div id="message" class="message-area"></div>
        <div class="shelf" id="shelf"></div>
        <div class="footer">Нажми на фантик — узнай приз</div>
    </div>

    <script>
        const API_BASE = '';
        const tg = window.Telegram.WebApp;
        tg.expand();
        tg.ready();

        let tickets = [];
        let openedState = {};
        const messageDiv = document.getElementById('message');
        const shelf = document.getElementById('shelf');

        function getUserId() {
            if (tg.initDataUnsafe?.user?.id) return tg.initDataUnsafe.user.id;
            const urlParams = new URLSearchParams(window.location.search);
            return urlParams.get('user_id');
        }

        async function loadTickets() {
            const userId = getUserId();
            if (!userId) {
                messageDiv.textContent = 'Ошибка: Откройте Mini App через кнопку в боте.';
                return;
            }
            try {
                const response = await fetch(`/api/my-tickets`, {
                    headers: { 'X-Telegram-Id': userId }
                });
                const data = await response.json();
                tickets = data.tickets || [];
                renderTickets();
                messageDiv.textContent = tickets.length 
                    ? `У вас ${tickets.length} билетов. Выбирайте!` 
                    : 'У вас пока нет билетов. Активируйте код в боте!';
            } catch (e) {
                messageDiv.textContent = 'Ошибка загрузки билетов.';
                console.error(e);
            }
        }

        function renderTickets() {
            shelf.innerHTML = '';
            tickets.forEach((ticket, index) => {
                const wrapper = document.createElement('div');
                wrapper.className = 'ticket-wrapper';
                wrapper.dataset.index = index;
                wrapper.dataset.ticketId = ticket.id;
                
                const ticketDiv = document.createElement('div');
                ticketDiv.className = 'ticket' + (openedState[ticket.id] ? ' opened' : '');
                
                const content = document.createElement('div');
                content.className = 'ticket-content';
                
                if (openedState[ticket.id]) {
                    const p = openedState[ticket.id];
                    content.innerHTML = `<div class="ticket-icon">${p.emoji}</div>
                                        <div class="prize-text">${p.name}</div>
                                        <div class="small-note">${p.description}</div>`;
                } else {
                    content.innerHTML = `<div class="ticket-icon">🍫❓</div>
                                        <div class="prize-text">Золотой фантик</div>
                                        <div class="small-note">Нажми, чтобы развернуть</div>`;
                }
                
                ticketDiv.appendChild(content);
                wrapper.appendChild(ticketDiv);
                wrapper.addEventListener('click', (e) => openTicket(e, ticket.id, index));
                shelf.appendChild(wrapper);
            });
        }

        async function openTicket(event, ticketId, index) {
            const wrapper = event.currentTarget;
            const ticketDiv = wrapper.querySelector('.ticket');
            
            if (openedState[ticketId]) {
                messageDiv.textContent = '✨ Этот билет уже принёс удачу!';
                gsap.fromTo(ticketDiv, { x: -3 }, { x: 3, duration: 0.1, yoyo: true, repeat: 2 });
                return;
            }

            const userId = getUserId();
            if (!userId) return;

            try {
                const response = await fetch(`/api/open-ticket`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Telegram-Id': userId },
                    body: JSON.stringify({ ticket_id: ticketId })
                });
                const data = await response.json();
                if (data.error) {
                    messageDiv.textContent = 'Ошибка: ' + data.error;
                    return;
                }

                const prize = data.prize;
                
                gsap.timeline()
                    .to(ticketDiv, { scale: 1.05, duration: 0.15, ease: "back.out(1.7)" })
                    .to(ticketDiv, { 
                        rotationY: 180, duration: 0.6, ease: "power2.inOut",
                        onComplete: () => {
                            openedState[ticketId] = prize;
                            ticketDiv.classList.add('opened');
                            const content = ticketDiv.querySelector('.ticket-content');
                            content.innerHTML = `<div class="ticket-icon">${prize.emoji}</div>
                                                <div class="prize-text">${prize.name}</div>
                                                <div class="small-note">${prize.description}</div>`;
                            gsap.set(ticketDiv, { rotationY: 0 });
                            
                            confetti({ particleCount: 100, spread: 70, origin: { y: 0.6 } });
                            if (prize.is_golden) {
                                const shine = document.createElement('div');
                                shine.className = 'golden-shine';
                                document.body.appendChild(shine);
                                setTimeout(() => shine.remove(), 1500);
                                messageDiv.textContent = '🎉 НЕВЕРОЯТНО! ВЫ НАШЛИ ЗОЛОТОЙ БИЛЕТ! 🎉';
                                setTimeout(() => {
                                    confetti({ particleCount: 200, spread: 100 });
                                }, 200);
                            } else {
                                messageDiv.textContent = `🎁 Поздравляем! Вы выиграли: ${prize.name}`;
                            }
                        }
                    });
            } catch (e) {
                messageDiv.textContent = 'Ошибка соединения с сервером.';
                console.error(e);
            }
        }

        loadTickets();
    </script>
</body>
</html>
""")

@app.route('/api/my-tickets', methods=['GET'])
def my_tickets():
    telegram_id = request.headers.get('X-Telegram-Id')
    if not telegram_id:
        return jsonify({'error': 'Missing Telegram ID'}), 400
    try:
        telegram_id = int(telegram_id)
    except:
        return jsonify({'error': 'Invalid Telegram ID'}), 400
    user_row = get_user_id(telegram_id)
    if not user_row:
        return jsonify({'tickets': []})
    all_tickets = tickets_sheet.get_all_values()
    unopened = []
    for row in all_tickets[1:]:
        if len(row) >= 4 and row[1] == str(user_row) and row[3] == 'unopened':
            unopened.append({'id': row[0]})
    return jsonify({'tickets': unopened})

@app.route('/api/open-ticket', methods=['POST'])
def open_ticket():
    data = request.get_json()
    ticket_id = data.get('ticket_id')
    telegram_id = request.headers.get('X-Telegram-Id')
    if not telegram_id or not ticket_id:
        return jsonify({'error': 'Missing data'}), 400
    try:
        telegram_id = int(telegram_id)
    except:
        return jsonify({'error': 'Invalid Telegram ID'}), 400
    user_row = get_user_id(telegram_id)
    if not user_row:
        return jsonify({'error': 'User not found'}), 404

    all_tickets = tickets_sheet.get_all_values()
    for i, row in enumerate(all_tickets[1:], start=2):
        if row[0] == ticket_id and row[1] == str(user_row):
            if row[3] == 'opened':
                prize_id = row[2]
                prize_info = get_prize_by_id(prize_id)
                return jsonify({'prize': prize_info, 'already_opened': True})
            prize_id = row[2]
            tickets_sheet.update(f'D{i}', [['opened']])
            tickets_sheet.update(f'F{i}', [[str(datetime.now())]])
            prize_info = get_prize_by_id(prize_id)
            return jsonify({'prize': prize_info, 'already_opened': False})
    return jsonify({'error': 'Ticket not found'}), 404

def get_prize_by_id(prize_id: str):
    try:
        cell = prizes_sheet.find(prize_id, in_column=1)
        row = prizes_sheet.row_values(cell.row)
        return {
            'name': row[1],
            'description': row[2],
            'emoji': row[3],
            'is_golden': row[7] == '1' or row[7].lower() == 'true'
        }
    except:
        return None

# -------------------- ЗАПУСК --------------------
async def main():
    existing = codes_sheet.col_values(1)
    for i, code in enumerate(["ШОКОЛАД1", "ШОКОЛАД2", "ШОКОЛАД3"], start=1):
        if code not in existing:
            codes_sheet.append_row([code, f"{i} билет(а)", i])
    await dp.start_polling(bot)

def run_bot():
    asyncio.run(main())

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
