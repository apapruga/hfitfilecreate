from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import BotCommand, Update
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

HELP_TEXT = """Я превращаю CSV с описанием беговой тренировки в .fit файл.

Что прислать:
1. CSV-файл
2. Колонки:
step_name, step_type, duration_sec, pace_min, pace_max, avg_pace, repeats

Правила:
- pace_min = самый быстрый темп, например 5:40
- pace_max = самый медленный темп, например 5:55
- avg_pace можно использовать вместо pace_min/pace_max
- repeats = сколько раз повторить строку

Допустимые step_type:
warmup, interval, recovery, cooldown, active, rest

Команды:
/start — краткая инструкция
/help — подробная справка
/sample — пример CSV
"""


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Привет. Отправь мне CSV с описанием тренировки, а я верну .fit файл.\n\n"
            "Для примера используй /sample\n"
            "Для справки используй /help"
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(HELP_TEXT)


async def sample_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        sample_path = Path(tmpdir) / "sample_workout.csv"
        create_sample_csv(sample_path)
        await update.message.reply_document(
            document=sample_path.open("rb"),
            filename="sample_workout.csv",
            caption="Вот пример CSV для загрузки.",
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.document:
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
        fit_name = f"{Path(filename).stem}.fit"
        fit_path = tmpdir_path / fit_name
        try:
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(custom_path=str(csv_path))
            convert_csv_to_fit(
                csv_path=csv_path,
                fit_path=fit_path,
                workout_name=Path(filename).stem[:64],
                default_tolerance_sec=DEFAULT_TOLERANCE_SEC,
                verbose=False,
            )
            await update.message.reply_document(
                document=fit_path.open("rb"),
                filename=fit_name,
                caption="Готово. Вот твой FIT-файл.",
            )
        except Exception as e:
            logger.exception("Failed to convert CSV")
            await update.message.reply_text(
                "Не смог обработать CSV.\n\n"
                f"Ошибка: {e}\n\n"
                "Проверь колонки и формат темпа. Для примера используй /sample"
            )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)


def register_handlers() -> None:
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("sample", sample_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_error_handler(error_handler)


@app.on_event("startup")
async def on_startup() -> None:
    register_handlers()
    await application.initialize()
    await application.start()
    await application.bot.set_my_commands([
        BotCommand("start", "Краткая инструкция"),
        BotCommand("help", "Подробная справка"),
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
