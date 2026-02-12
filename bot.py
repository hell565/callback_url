import asyncio
import json
import logging
import os
import time
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)
# Удали или закомментируй старый импорт
# from telegram.ext import PTBUserWarning

# Правильный импорт (из telegram.warnings)
from telegram.warnings import PTBUserWarning

# Теперь фильтрация предупреждений (убирает PTBUserWarning из вывода)
import warnings
warnings.filterwarnings("ignore", category=PTBUserWarning)
# ──────────────────────────────────────────────
BOT_TOKEN = '8518274093:AAGwPAY3k_oBSYoddU6hbSWRg3lba6CrQME'          # ← здесь токен от @BotFather
API_KEY = '2e23e6b159ed8c5ca2b4e1cc032f3dfc'   # твой Suno ключ
BASE_URL = 'https://api.sunoapi.org/api/v1'
CALLBACK_URL = 'http://твой-vps-ip/callback/callback.py'  # ← обязательно!

SONGS_DIR = Path('/var/www/html/songs')
NOTIFY_QUEUE = Path('/var/www/cgi-bin/notify_queue.jsonl')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния conversation
MODE, CUSTOM_LYRICS, STYLE, TITLE, INSTRUMENTAL, MODEL, CONFIRM = range(7)

MODELS = ["V5", "V4_5PLUS", "V4_5ALL", "V4_5", "V4", "V3_5"]

user_data = {}  # {user_id: {task_id, mode, ...}}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Простой режим", callback_data='simple')],
        [InlineKeyboardButton("Продвинутый режим", callback_data='advanced')],
        [InlineKeyboardButton("Мои кредиты", callback_data='credits')],
        [InlineKeyboardButton("Помощь", callback_data='help')]
    ]
    await update.message.reply_text(
        "Привет! Я генерирую музыку через Suno API 🎵\n"
        "Выбери режим:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id

    if data == 'credits':
        credits = await get_credits()
        await query.edit_message_text(f"Остаток кредитов: {credits}")
        return

    if data == 'help':
        await query.edit_message_text(
            "Простой: просто напиши описание песни\n"
            "Продвинутый: свой текст, стиль, модель, инструментал и т.д.\n"
            "/start — меню\n"
            "/cancel — отменить"
        )
        return

    if data == 'simple':
        await query.edit_message_text(
            "Простой режим: напиши описание песни (можно на русском или английском)\n"
            "Пример: энергичная рок-песня в стиле Цоя про город"
        )
        user_data[uid] = {'mode': 'simple'}
        return ConversationHandler.END

    if data == 'advanced':
        keyboard = [[InlineKeyboardButton(m, callback_data=f"model_{m}")] for m in MODELS]
        await query.edit_message_text(
            "Продвинутый режим\nСначала выбери модель:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        user_data[uid] = {'mode': 'advanced'}
        return MODEL

async def model_choice(update: Update, context):
    query = update.callback_query
    await query.answer()
    model = query.data.split('_')[1]
    uid = query.from_user.id
    user_data[uid]['model'] = model

    await query.edit_message_text(
        f"Модель: {model}\nТеперь пришли свои lyrics (или напиши 'авто' для автоматических)"
    )
    return CUSTOM_LYRICS

# ... (дальше аналогично: CUSTOM_LYRICS → STYLE → TITLE → INSTRUMENTAL → CONFIRM)

async def generate_simple(update: Update, context):
    uid = update.effective_user.id
    prompt = update.message.text.strip()
    if not prompt:
        await update.message.reply_text("Пусто. Попробуй снова.")
        return

    task_id = await send_generate(uid, prompt=prompt, custom=False)
    if task_id:
        await update.message.reply_text(f"Генерирую... (task {task_id})\nОжидай, скоро пришлю треки!")
        user_data[uid]['task_id'] = task_id
    else:
        await update.message.reply_text("Ошибка запуска. Проверь /credits")

async def send_generate(uid, **kwargs):
    headers = {'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json'}
    payload = {
        'callBackUrl': CALLBACK_URL,
        'instrumental': kwargs.get('instrumental', False),
        'model': kwargs.get('model', 'V5'),
    }

    if kwargs.get('custom', False):
        payload['customMode'] = True
        payload['prompt'] = kwargs.get('lyrics', '')
        payload['style'] = kwargs.get('style', '')
        payload['title'] = kwargs.get('title', 'Generated')
    else:
        payload['customMode'] = False
        payload['prompt'] = kwargs.get('prompt', '')

    try:
        r = requests.post(f"{BASE_URL}/generate", json=payload, headers=headers, timeout=20)
        data = r.json()
        if data.get('code') == 200:
            return data.get('data', {}).get('taskId')
    except Exception as e:
        logger.error(e)
    return None

async def get_credits():
    try:
        r = requests.get(f"{BASE_URL}/credits", headers={'Authorization': f'Bearer {API_KEY}'})
        data = r.json()
        return data.get('data', 'неизвестно')
    except:
        return 'ошибка'

# Polling очереди уведомлений (фоновая задача)
async def check_notify_queue(context: ContextTypes.DEFAULT_TYPE):
    if not NOTIFY_QUEUE.exists():
        return

    with open(NOTIFY_QUEUE, 'r') as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        try:
            item = json.loads(line.strip())
            task_id = item['task_id']
            tracks = item['tracks']

            # Находим пользователей с этим task_id (можно хранить в redis/sqlite, но пока dict)
            for uid, ud in user_data.items():
                if ud.get('task_id') == task_id:
                    for track in tracks:
                        caption = (
                            f"🎵 {track['title']}\n"
                            f"⏱ {track['duration']} сек\n\n"
                            f"{track['lyrics'][:400]}..." if track['lyrics'] else ""
                        )
                        await context.bot.send_photo(
                            chat_id=uid,
                            photo=track['image_url'],
                            caption=caption,
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("Скачать MP3", url=track['audio_url'])],
                                [InlineKeyboardButton("Extend этот трек", callback_data=f"extend_{task_id}")],
                                [InlineKeyboardButton("Новая песня", callback_data='start')]
                            ])
                        )
                    del user_data[uid]  # очистка
                    break
        except:
            new_lines.append(line)

    with open(NOTIFY_QUEUE, 'w') as f:
        f.writelines(new_lines)

async def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    # Все твои handlers здесь (как было)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # JobQueue уже добавлен в коде, он будет работать
    if application.job_queue:
        application.job_queue.run_repeating(
            check_notify_queue,
            interval=10,
            first=3
        )
    else:
        logger.warning("JobQueue не установлен — уведомления не будут приходить автоматически")

    # ← Вот это главное изменение: вместо ручного start_polling используй run_polling
    await application.run_polling(
        poll_interval=0.0,          # 0 = максимально быстро
        timeout=10,
        drop_pending_updates=True,  # опционально: игнорировать старые сообщения при запуске
        allowed_updates=Update.ALL_TYPES,
        # close_loop=True по умолчанию — ок
    )

if __name__ == '__main__':
    asyncio.run(main())
