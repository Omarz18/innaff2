import os, re, logging
from pathlib import Path
from subprocess import Popen, PIPE, CalledProcessError, TimeoutExpired
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ConversationHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

OWNER_TIMEOUT = int(os.getenv("OWNER_TIMEOUT", "60"))
OWNER_OUTPUT_REGEX = re.compile(os.getenv("OWNER_OUTPUT_REGEX", r"(?:\[\+\]\s*)?name\s*:\s*(.+)"), re.I)
OWNER_INTERACTIVE_SCRIPT = os.getenv("OWNER_INTERACTIVE_SCRIPT", "who-is-this.py")
OWNER_INTERACTIVE_CWD = os.getenv("OWNER_INTERACTIVE_CWD")  # اختياري الآن

def _auto_extract_zip_if_needed():
    root = Path(".").resolve()
    # أي ملف zip في الجذر قد يحتوي السكربت
    for z in root.glob("*.zip"):
        try:
            with zipfile.ZipFile(z, "r") as f:
                names = f.namelist()
                if any(n.endswith("/"+OWNER_INTERACTIVE_SCRIPT) or n.endswith(OWNER_INTERACTIVE_SCRIPT) for n in names):
                    target_dir = root / (z.stem)
                    if not target_dir.exists():
                        log.info(f"[auto-extract] Extracting {z.name} -> {target_dir}")
                        f.extractall(target_dir)
        except Exception:
            pass

def _resolve_script_path():
    root = Path(".").resolve()
    # أولاً: فك أي zip محتمل
    _auto_extract_zip_if_needed()

    # 1) بيئة محددة
    if OWNER_INTERACTIVE_CWD:
        p = root / OWNER_INTERACTIVE_CWD / OWNER_INTERACTIVE_SCRIPT
        if p.exists():
            log.info(f"[auto-path] Using env path: {p}")
            return str(p.parent), OWNER_INTERACTIVE_SCRIPT

    # 2) أسماء شائعة للمجلد
    candidates = [
        "WHO-IS-THIS-MAIN", "Who-is-this-main", "who-is-this-main",
        "Who-Is-This-Main", "WHO_is_THIS_main"
    ]
    for c in candidates:
        p = root / c / OWNER_INTERACTIVE_SCRIPT
        if p.exists():
            log.info(f"[auto-path] Found script at: {p}")
            return str(p.parent), OWNER_INTERACTIVE_SCRIPT

    # 3) بحث شامل
    for p in root.rglob(OWNER_INTERACTIVE_SCRIPT):
        if ".venv" in p.parts or ".git" in p.parts:
            continue
        log.info(f"[auto-path] Found by rglob: {p}")
        return str(p.parent), OWNER_INTERACTIVE_SCRIPT

    log.error("[auto-path] Script not found")
    return None, None

def _run_interactive(mode: str, value: str):
    cwd, script = _resolve_script_path()
    if not cwd:
        return None

    try:
        cmd = ["python", script]
        p = Popen(cmd, cwd=cwd, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        # مهم: مدخلات السكربت كما يتوقعها الأصل
        stdin_data = f"{mode}\n{value}\n99\n".encode("utf-8")
        out, err = p.communicate(input=stdin_data, timeout=OWNER_TIMEOUT)
        text_out = (out or b"").decode("utf-8", "ignore")
        text_err = (err or b"").decode("utf-8", "ignore")
        log.info("[who-output]\\n" + text_out)
        if text_err.strip():
            log.error("[who-error]\\n" + text_err)

        for line in text_out.splitlines():
            m = OWNER_OUTPUT_REGEX.search(line)
            if m:
                # أول مجموعة غير فاضية
                for g in m.groups():
                    if g and g.strip():
                        return g.strip()
        return None
    except (CalledProcessError, TimeoutExpired) as e:
        log.exception(f"[who-exec] {e}")
        return None

# ===== تيليجرام أوامر =====
ASK_MODE, ASK_VALUE, ASK_PHONE = 1, 2, 3

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل /who لاختيار 1=Email أو 2=Phone\nأو /phone لإرسال رقم الجوال مباشرة.")
    return ConversationHandler.END

async def who_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("اختر الوضع:\n1) Email\n2) Phone")
    return ASK_MODE

async def who_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = (update.message.text or "").strip()
    if mode not in {"1","2"}:
        await update.message.reply_text("أرسل 1 أو 2")
        return ASK_MODE
    context.user_data["who_mode"] = mode
    await update.message.reply_text("أرسل القيمة الآن:")
    return ASK_VALUE

async def who_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("who_mode", "2")
    value = (update.message.text or "").strip()
    res = _run_interactive(mode, value)
    if res:
        await update.message.reply_text(f"[+] Result: {res}")
    else:
        await update.message.reply_text("تعذر استخراج النتيجة من إخراج السكربت.")
    return ConversationHandler.END

async def phone_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل الرقم بالصيغة التي يتوقعها السكربت (مثال: 966 5xxxxxxx)")
    return ASK_PHONE

async def phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    res = _run_interactive("2", raw)
    if res:
        await update.message.reply_text(f"[+] Owner Name: {res}")
    else:
        await update.message.reply_text("تعذر استخراج اسم المالك من إخراج السكربت.")
    return ConversationHandler.END

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Set TELEGRAM_TOKEN env var.")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    conv_who = ConversationHandler(
        entry_points=[CommandHandler("who", who_start)],
        states={
            ASK_MODE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, who_mode)],
            ASK_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, who_value)],
        },
        fallbacks=[],
    )
    conv_phone = ConversationHandler(
        entry_points=[CommandHandler("phone", phone_entry)],
        states={
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_number)]
        },
        fallbacks=[]
    )

    app.add_handler(conv_who)
    app.add_handler(conv_phone)

    log.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()