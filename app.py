import os
import re
import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes
from subprocess import Popen, PIPE
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

OWNER_TIMEOUT = int(os.getenv("OWNER_TIMEOUT", "60"))
OWNER_OUTPUT_REGEX = re.compile(os.getenv("OWNER_OUTPUT_REGEX", r"(?:\[\+\]\s*)?name\s*:\s*(.+)"), re.I)

OWNER_INTERACTIVE_SCRIPT = os.getenv("OWNER_INTERACTIVE_SCRIPT", "who-is-this.py")
OWNER_INTERACTIVE_CWD = os.getenv("OWNER_INTERACTIVE_CWD")

CHOOSING, TYPING_PHONE = range(2)

def find_owner_script():
    if OWNER_INTERACTIVE_CWD and Path(OWNER_INTERACTIVE_CWD).exists():
        return Path(OWNER_INTERACTIVE_CWD) / OWNER_INTERACTIVE_SCRIPT

    candidates = [
        Path("Who-is-this-main") / "who-is-this.py",
        Path("WHO-IS-THIS-MAIN") / "who-is-this.py",
        Path("who-is-this-main") / "who-is-this.py",
    ]
    for c in candidates:
        if c.exists():
            return c

    for f in Path(".").rglob("who-is-this.py"):
        return f

    return None

def _run_who_is_this_interactive(phone_number: str):
    script_path = find_owner_script()
    if not script_path:
        logger.error("[auto-path] Script not found")
        return None

    logger.info(f"[auto-path] Found script at: {script_path}")
    try:
        p = Popen(
            ["python", str(script_path)],
            cwd=str(script_path.parent),
            stdin=PIPE, stdout=PIPE, stderr=PIPE,
            text=True
        )
        out, err = p.communicate(input=phone_number, timeout=OWNER_TIMEOUT)
        logger.info("[who-output]\n" + out)
        if err:
            logger.error("[who-error]\n" + err)

        for line in out.splitlines():
            m = OWNER_OUTPUT_REGEX.search(line)
            if m:
                return m.group(1).strip()
        return None
    except Exception as e:
        logger.exception("Error running who-is-this")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("اختر: 1) ايميل 2) جوال")
    return CHOOSING

async def choose_option(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    if choice == "2":
        await update.message.reply_text("ارسل رقم الجوال:")
        return TYPING_PHONE
    else:
        await update.message.reply_text("الخيار غير مدعوم هنا.")
        return ConversationHandler.END

async def phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    owner = _run_who_is_this_interactive(raw)
    if owner:
        await update.message.reply_text(f"اسم المالك: {owner}")
    else:
        await update.message.reply_text("ما قدرت استخرج اسم المالك.")
    return ConversationHandler.END

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("who", start)],
        states={
            CHOOSING: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_option)],
            TYPING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_number)],
        },
        fallbacks=[],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("phone", phone_number))

    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
