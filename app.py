from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import BotCommand, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from fit_builder import convert_csv_to_fit, create_sample_csv

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO)
logger = logging.getLogger("tg-fit-bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
WEBHOOK_SECRET_PATH = os.environ.get("WEBHOOK_SECRET_PATH", "telegram-fit-webhook")
WEBHOOK_SECRET_TOKEN = os.environ.get("WEBHOOK_SECRET_TOKEN", "")
DEFAULT_TOLERANCE_SEC = int(os.environ.get("DEFAULT_TOLERANCE_SEC", "5"))
WEBHOOK_ROUTE = f"/telegram/{WEBHOOK_SECRET_PATH}"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_ROUTE}" if BASE_URL else None

application = Application.builder().token(BOT_TOKEN).build()
app = FastAPI(title="Telegram FIT Bot", version="1.0.0")

INPUT_MODE_FILE = "file"
INPUT_MODE_TEXT = "text"
INPUT_MODE_KEY = "input_mode"
MODE_FILE_LABEL = "Загрузить CSV файлом"
MODE_TEXT_LABEL = "Вставить CSV текстом"


def build_mode_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[MODE_FILE_LABEL], [MODE_TEXT_LABEL]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def get_input_mode(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get(INPUT_MODE_KEY, INPUT_MODE_FILE)


def set_input_mode(context: ContextTypes.DEFAULT_TYPE, mode: str) -> None:
    context.user_data[INPUT_MODE_KEY] = mode

HELP_TEXT = """Я превращаю CSV с описанием беговой тренировки в .fit файл.

Сначала выбери режим загрузки:
- /mode_file — прислать CSV-файл
- /mode_text — вставить CSV как текст сообщения

Колонки:
step_name, step_type, duration_sec, pace_min, pace_max, avg_pace, hr_min, hr_max, avg_hr, repeats

Правила:
- pace_min = самый быстрый темп, например 5:40
- pace_max = самый медленный темп, например 5:55
- avg_pace можно использовать вместо pace_min/pace_max
- hr_min и hr_max = целевой диапазон пульса в bpm
- avg_hr можно использовать вместо hr_min/hr_max
- в одной строке можно задать либо pace, либо heart rate
- repeats = сколько раз повторить строку

Допустимые step_type:
warmup, interval, recovery, cooldown, active, rest

Команды:
/start — краткая инструкция
/help — подробная справка
/mode — показать выбор режима загрузки
/mode_file — режим загрузки CSV-файлом
/mode_text — режим вставки CSV текстом
/sample — пример CSV
"""


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        set_input_mode(context, INPUT_MODE_FILE)
        await update.message.reply_text(
            "Выбери, как загрузить тренировку: CSV-файлом или текстом в сообщении.\n\n"
            "По умолчанию включен режим CSV-файла.\n"
            "Для примера используй /sample\n"
            "Для справки используй /help",
            reply_markup=build_mode_keyboard(),
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(HELP_TEXT, reply_markup=build_mode_keyboard())


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    current_mode = get_input_mode(context)
    mode_text = "CSV-файл" if current_mode == INPUT_MODE_FILE else "CSV-текст"
    await update.message.reply_text(
        f"Текущий режим загрузки: {mode_text}.\nВыбери другой режим кнопкой или командой.",
        reply_markup=build_mode_keyboard(),
    )


async def mode_file_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    set_input_mode(context, INPUT_MODE_FILE)
    await update.message.reply_text(
        "Режим переключен на CSV-файл. Теперь пришли документ `.csv`.",
        reply_markup=build_mode_keyboard(),
    )


async def mode_text_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    set_input_mode(context, INPUT_MODE_TEXT)
    await update.message.reply_text(
        "Режим переключен на CSV-текст. Теперь вставь CSV прямо в сообщение.",
        reply_markup=build_mode_keyboard(),
    )


async def sample_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if get_input_mode(context) == INPUT_MODE_TEXT:
        sample_text = "\n".join([
            "step_name,step_type,duration_sec,pace_min,pace_max,avg_pace,hr_min,hr_max,avg_hr,repeats",
            "Разминка,warmup,720,7:20,7:35,,,,,1",
            "Ускорение,interval,20,,,,165,175,,4",
            "Восстановление,recovery,40,,,,130,145,,4",
            "Основной интервал,interval,120,6:00,6:10,,,,,6",
            "Восстановление,recovery,120,,,,,,140,6",
            "Заминка,cooldown,600,7:20,7:50,,,,,1",
        ])
        await update.message.reply_text(
            "Вот пример CSV-текста. Скопируй, отредактируй и отправь одним сообщением:\n\n"
            f"<pre>{sample_text}</pre>",
            parse_mode="HTML",
            reply_markup=build_mode_keyboard(),
        )
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        sample_path = Path(tmpdir) / "sample_workout.csv"
        create_sample_csv(sample_path)
        await update.message.reply_document(
            document=sample_path.open("rb"),
            filename="sample_workout.csv",
            caption="Вот пример CSV для загрузки.",
            reply_markup=build_mode_keyboard(),
        )


async def convert_and_reply(update: Update, csv_content: str, source_name: str) -> None:
    if not update.message:
        return
    await update.message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        csv_path = tmpdir_path / f"{source_name}.csv"
        fit_name = f"{source_name}.fit"
        fit_path = tmpdir_path / fit_name
        csv_path.write_text(csv_content, encoding="utf-8")
        convert_csv_to_fit(
            csv_path=csv_path,
            fit_path=fit_path,
            workout_name=source_name[:64],
            default_tolerance_sec=DEFAULT_TOLERANCE_SEC,
            verbose=False,
        )
        await update.message.reply_document(
            document=fit_path.open("rb"),
            filename=fit_name,
            caption="Готово. Вот твой FIT-файл.",
            reply_markup=build_mode_keyboard(),
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.document:
        return
    if get_input_mode(context) != INPUT_MODE_FILE:
        await update.message.reply_text(
            "Сейчас включен режим CSV-текста. Если хочешь загрузить файл, переключись через /mode_file.",
            reply_markup=build_mode_keyboard(),
        )
        return
    doc = update.message.document
    filename = Path(doc.file_name or "workout.csv").name
    if not filename.lower().endswith(".csv"):
        await update.message.reply_text("Нужен именно CSV-файл.")
        return
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("CSV слишком большой для обработки через Bot API. Нужен файл до 20 МБ.")
        return
    await update.message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        csv_path = tmpdir_path / filename
        try:
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(custom_path=str(csv_path))
            await convert_and_reply(update, csv_path.read_text(encoding="utf-8-sig"), Path(filename).stem)
        except Exception as e:
            logger.exception("Failed to convert CSV")
            await update.message.reply_text(
                "Не смог обработать CSV.\n\n"
                f"Ошибка: {e}\n\n"
                "Проверь колонки и формат темпа. Для примера используй /sample"
            )


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text == MODE_FILE_LABEL:
        await mode_file_command(update, context)
        return
    if text == MODE_TEXT_LABEL:
        await mode_text_command(update, context)
        return
    if get_input_mode(context) != INPUT_MODE_TEXT:
        await update.message.reply_text(
            "Сейчас включен режим CSV-файла. Пришли `.csv` документ или переключись через /mode_text.",
            reply_markup=build_mode_keyboard(),
        )
        return
    try:
        await convert_and_reply(update, text, "workout_message")
    except Exception as e:
        logger.exception("Failed to convert CSV from text message")
        await update.message.reply_text(
            "Не смог обработать CSV из текста сообщения.\n\n"
            f"Ошибка: {e}\n\n"
            "Проверь формат CSV. Для примера используй /sample",
            reply_markup=build_mode_keyboard(),
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)


def register_handlers() -> None:
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("mode", mode_command))
    application.add_handler(CommandHandler("mode_file", mode_file_command))
    application.add_handler(CommandHandler("mode_text", mode_text_command))
    application.add_handler(CommandHandler("sample", sample_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    application.add_error_handler(error_handler)


@app.on_event("startup")
async def on_startup() -> None:
    register_handlers()
    await application.initialize()
    await application.start()
    await application.bot.set_my_commands([
        BotCommand("start", "Краткая инструкция"),
        BotCommand("help", "Подробная справка"),
        BotCommand("mode", "Выбрать режим загрузки"),
        BotCommand("mode_file", "Режим загрузки CSV-файлом"),
        BotCommand("mode_text", "Режим вставки CSV текстом"),
        BotCommand("sample", "Скачать пример CSV"),
    ])
    if WEBHOOK_URL:
        await application.bot.set_webhook(url=WEBHOOK_URL, secret_token=WEBHOOK_SECRET_TOKEN or None, drop_pending_updates=True)
        logger.info("Webhook set to %s", WEBHOOK_URL)
    else:
        logger.warning("BASE_URL is empty. Webhook was not registered.")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await application.stop()
    await application.shutdown()


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.post(WEBHOOK_ROUTE)
async def telegram_webhook(request: Request) -> JSONResponse:
    if WEBHOOK_SECRET_TOKEN:
        incoming_secret = request.headers.get("x-telegram-bot-api-secret-token")
        if incoming_secret != WEBHOOK_SECRET_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid secret token")
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return JSONResponse({"ok": True})
