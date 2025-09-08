import os
import re
import json
import logging
import requests
import phonenumbers
from typing import Optional
from subprocess import Popen, PIPE, CalledProcessError, TimeoutExpired
from phonenumbers import carrier, geocoder, number_type, PhoneNumberType

from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)

# ===== إعداد اللوجينج =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("app")

# ===== متغيرات البيئة =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# مسار اسم المالك القابل للتهيئة:
# 1) موديول + دالة (اختياري)
OWNER_MODULE = os.getenv("OWNER_MODULE", "").strip()
OWNER_FUNC = os.getenv("OWNER_FUNC", "").strip()

# 2) سكربت عادي بوسائط (اختياري) — تم الإبقاء عليه لو عندك سكربت يقبل args
OWNER_SCRIPT = os.getenv("OWNER_SCRIPT", "").strip()
OWNER_ARGS = os.getenv("OWNER_ARGS", "--number {number} --json").strip()
OWNER_SCRIPT_JSON_KEY = os.getenv("OWNER_SCRIPT_JSON_KEY", "name").strip()

# 3) سكربت تفاعلي عبر stdin (الحالة اللي عندك)
OWNER_INTERACTIVE_SCRIPT = os.getenv("OWNER_INTERACTIVE_SCRIPT", "").strip()  # مثال: who-is-this.py
OWNER_INTERACTIVE_CWD = os.getenv("OWNER_INTERACTIVE_CWD", "").strip()        # مثال: Who-is-this-main
# سيتم استبدال {number} بالرقم بصيغة +E164
OWNER_STDIN_TEMPLATE = os.getenv("OWNER_STDIN_TEMPLATE", "2\\n{number}\\n99\\n")
# نمط لإخراج الاسم من stdout. افتراضي يلتقط أي سطر يحتوي [+] name : <الاسم>
OWNER_OUTPUT_REGEX = os.getenv("OWNER_OUTPUT_REGEX", r"\\[\\+\\]\\s*name\\s*:\\s*(.+)")
OWNER_TIMEOUT = int(os.getenv("OWNER_TIMEOUT", "25"))

# ===== حالات المحادثة =====
ASK_USERNAME = 1      # /ig
PHONE_STEP = 10       # /phone

# =========================== إنستغرام (/ig) ===========================
def _livecounts_headers():
    return {
        'Host': 'api.livecounts.io',
        'User-Agent': 'Mozilla/5.0',
        'Accept': '*/*',
        'Origin': 'https://livecounts.io',
    }

def _storiesig_headers():
    return {
        'Host': 'storiesig.info',
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://storiesig.info/en/',
    }

def fetch_from_livecounts(username: str) -> str:
    h = _livecounts_headers()
    parts = []
    try:
        r1 = requests.get(
            f'https://api.livecounts.io/instagram-live-follower-counter/data/{username}',
            headers=h, timeout=15
        )
        if r1.ok and '"success":true' in r1.text:
            jd = r1.json()
            parts += [
                f"- Name: {jd.get('name')}",
                f"- Verified: {jd.get('verified')}",
                f"- Bio: {jd.get('description')}",
            ]
            if jd.get('avatar'):
                parts.append(f"- Profile Pic URL: {jd.get('avatar')}")
        else:
            r2 = requests.get(
                f'https://api.livecounts.io/instagram-live-follower-counter/search/{username}',
                headers=h, timeout=15
            )
            if r2.ok and '"success":true' in r2.text:
                import re as _re
                m = _re.findall(r"(.*?),(.*?),(.*?),(.*?)]", str(r2.json().get("userData")))
                if m:
                    t = m[0]
                    name = str(t[2]).replace("'username': '", '').replace("'", "")
                    verified = str(t[3]).replace("'verified':", '').replace('}', '')
                    parts.append(f"- Name: {name}")
                    parts.append(f"- Verified: {verified}")
                    maybe_pic = str(t[0]).replace("'", '').replace("avatar", '').replace("[{:", '')
                    if maybe_pic.strip():
                        parts.append(f"- Profile Pic URL: {maybe_pic}")
        r3 = requests.get(
            f'https://api.livecounts.io/instagram-live-follower-counter/stats/{username}',
            headers=h, timeout=15
        )
        if r3.ok and '"success":true' in r3.text:
            jd3 = r3.json()
            followers = jd3.get('followerCount')
            bottom = str(jd3.get("bottomOdos"))
            m = re.findall(r"(.*?),(.*?)]", bottom)
            following = posts = None
            if m:
                following = str(m[0][0]).replace('[', '')
                posts = m[0][1]
            if followers is not None: parts.append(f"- Followers Count: {followers}")
            if following is not None: parts.append(f"- Following: {following}")
            if posts is not None:     parts.append(f"- Posts: {posts}")
    except Exception as e:
        log.warning("livecounts error: %s", e)
    return "\\n".join(parts).strip()

def fetch_from_storiesig(username: str) -> Optional[str]:
    try:
        r = requests.get(
            f'https://storiesig.info/api/ig/profile/{username}',
            headers=_storiesig_headers(), timeout=15
        )
        if r.ok and (username in r.text):
            res = r.json().get("result", {})
            return "\\n".join([
                f"- Name: {res.get('full_name')}",
                f"- Bio: {res.get('biography')}",
                f"- userID: {res.get('id')}",
                f"- Private: {res.get('is_private')}",
                f"- Followers Count: {res.get('edge_followed_by',{}).get('count')}",
                f"- Following: {res.get('edge_follow',{}).get('count')}",
                f"- Posts: {res.get('edge_owner_to_timeline_media',{}).get('count')}",
                f"- Profile Pic URL: {res.get('profile_pic_url')}",
            ])
    except Exception as e:
        log.warning("storiesig error: %s", e)
    return None

def fetch_from_private_api(username: str) -> Optional[str]:
    try:
        r = requests.post(
            "https://i.instagram.com:443/api/v1/users/lookup/",
            headers={
                "Connection": "close",
                "X-IG-Connection-Type":"WIFI",
                "X-IG-Capabilities":"3R4=",
                "Accept-Language":"ar-sa",
                "Content-Type":"application/x-www-form-urlencoded; charset=UTF-8",
                "User-Agent":"Instagram 99.4.0",
                "Accept-Encoding":"gzip, deflate"
            },
            data={"signed_body": f"sig.{{\"q\":\"{username}\"}}"},
            timeout=15
        )
        if (not r.ok) or 'No users found' in r.text or '"spam":true' in r.text:
            return None
        jd = r.json()
        u = jd.get('user', {})
        lines = [
            f"- Name: {u.get('full_name')}",
            f"- userID: {jd.get('user_id')}",
            f"- Email: {jd.get('obfuscated_email')}",
            f"- Phone Number: {jd.get('obfuscated_phone')}",
            f"- Verified: {u.get('is_verified')}",
            f"- Private: {u.get('is_private')}",
            f"- Has Valid Phone Number: {jd.get('has_valid_phone')}",
            f"- Can Email Reset: {jd.get('can_email_reset')}",
            f"- Can Sms Reset: {jd.get('can_sms_reset')}",
            f"- Profile Pic URL: {u.get('profile_pic_url')}",
        ]
        return "\\n".join(lines)
    except Exception as e:
        log.warning("private api error: %s", e)
        return None

def instagram_info(username: str) -> str:
    for fn in (fetch_from_private_api, fetch_from_storiesig, fetch_from_livecounts):
        res = fn(username)
        if res:
            return res
    return "تعذّر الحصول على معلومات هذا الحساب حالياً."

# =========================== الجوال (/phone) ===========================
def to_e164(raw: str, default_region: Optional[str] = None) -> Optional[str]:
    s = (raw or "").strip().replace("−", "-")
    try:
        if s.startswith("+"):
            pn = phonenumbers.parse(s, None)
        else:
            pn = phonenumbers.parse(s, default_region)
        if phonenumbers.is_possible_number(pn):
            return phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        return None
    return None

def lookup_owner_from_module(e164: str) -> Optional[str]:
    if not OWNER_MODULE or not OWNER_FUNC:
        return None
    try:
        mod = __import__(OWNER_MODULE)
        fn = getattr(mod, OWNER_FUNC, None)
        if callable(fn):
            name = fn(e164)
            if isinstance(name, str) and name.strip():
                log.info("Owner via module %s.%s", OWNER_MODULE, OWNER_FUNC)
                return name.strip()
    except Exception as e:
        log.warning("module lookup failed: %s", e)
    return None

def lookup_owner_from_script(e164: str) -> Optional[str]:
    if not OWNER_SCRIPT:
        return None
    try:
        args = OWNER_ARGS.replace("{number}", e164).strip()
        cmd = ["python", OWNER_SCRIPT] + [a for a in args.split() if a]
        p = Popen(cmd, stdout=PIPE, stderr=PIPE)
        out, _ = p.communicate(timeout=OWNER_TIMEOUT)
        out = out.decode("utf-8", "ignore").strip()
        try:
            j = json.loads(out)
            val = j.get(OWNER_SCRIPT_JSON_KEY) or j.get("owner") or j.get("caller_name") or j.get("name")
            if isinstance(val, str) and val.strip():
                log.info("Owner via script JSON (%s)", OWNER_SCRIPT)
                return val.strip()
        except Exception:
            if out:
                line = out.splitlines()[-1].strip()
                if line:
                    log.info("Owner via script raw text (%s)", OWNER_SCRIPT)
                    return line
    except (CalledProcessError, TimeoutExpired) as e:
        log.warning("script lookup failed: %s", e)
    return None

def lookup_owner_from_interactive(e164: str) -> Optional[str]:
    if not OWNER_INTERACTIVE_SCRIPT:
        return None
    try:
        cmd = ["python", OWNER_INTERACTIVE_SCRIPT]
        cwd = OWNER_INTERACTIVE_CWD or None
        p = Popen(cmd, cwd=cwd, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        stdin_data = (OWNER_STDIN_TEMPLATE.replace("{number}", e164)).encode("utf-8")
        out, err = p.communicate(input=stdin_data, timeout=OWNER_TIMEOUT)
        text = out.decode("utf-8", "ignore")
        if not text:
            text = err.decode("utf-8", "ignore")
        if text:
            m = re.search(OWNER_OUTPUT_REGEX, text, re.IGNORECASE)
            if m and m.group(1).strip():
                name = m.group(1).strip()
                log.info("Owner via interactive script (%s)", OWNER_INTERACTIVE_SCRIPT)
                return name
        log.warning("interactive script produced no match. Regex used: %s", OWNER_OUTPUT_REGEX)
    except (CalledProcessError, TimeoutExpired) as e:
        log.warning("interactive lookup failed: %s", e)
    return None

def lookup_owner_name(e164: Optional[str]) -> Optional[str]:
    if not e164:
        return None
    # الأول: موديول
    name = lookup_owner_from_module(e164)
    if name:
        return name
    # الثاني: سكربت بوسائط
    name = lookup_owner_from_script(e164)
    if name:
        return name
    # الثالث: سكربت تفاعلي عبر stdin
    name = lookup_owner_from_interactive(e164)
    if name:
        return name
    log.info("Owner name not found (no configured provider matched).")
    return None

def phone_summary(raw: str, default_region: Optional[str] = None) -> str:
    s = (raw or "").strip().replace("−", "-")
    try:
        if s.startswith("+"):
            pn = phonenumbers.parse(s, None)
        else:
            pn = phonenumbers.parse(s, default_region)
    except phonenumbers.NumberParseException as e:
        return f"رقم غير صالح: {e}"
    valid = phonenumbers.is_valid_number(pn)
    possible = phonenumbers.is_possible_number(pn)
    e164 = phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.E164) if possible else "غير متاح"
    intl = phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.INTERNATIONAL) if possible else "غير متاح"
    natl = phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.NATIONAL) if possible else "غير متاح"
    reg = geocoder.description_for_number(pn, "en") or "Unknown"
    carr = carrier.name_for_number(pn, "en") or "Unknown"
    typ  = number_type(pn)
    typ_name = {
        PhoneNumberType.MOBILE: "Mobile",
        PhoneNumberType.FIXED_LINE: "Fixed line",
        PhoneNumberType.FIXED_LINE_OR_MOBILE: "Fixed/Mobile",
        PhoneNumberType.TOLL_FREE: "Toll-free",
        PhoneNumberType.PREMIUM_RATE: "Premium-rate",
        PhoneNumberType.SHARED_COST: "Shared-cost",
        PhoneNumberType.VOIP: "VoIP",
        PhoneNumberType.PERSONAL_NUMBER: "Personal",
        PhoneNumberType.PAGER: "Pager",
        PhoneNumberType.UAN: "UAN",
        PhoneNumberType.VOICEMAIL: "Voicemail",
        PhoneNumberType.UNKNOWN: "Unknown",
    }.get(typ, "Unknown")
    lines = [
        f"- Valid: {valid}",
        f"- Possible: {possible}",
        f"- E164: {e164}",
        f"- International: {intl}",
        f"- National: {natl}",
        f"- Region: {reg}",
        f"- Type: {typ_name}",
        f"- Carrier: {carr}",
    ]
    return "\\n".join(lines)

# =========================== Handlers ===========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلًا 👋\\n"
        "الأوامر المتاحة:\\n"
        "/phone — استخراج تفاصيل رقم جوال (+ اسم المالك لو تم ضبطه)\\n"
        "/ig — معلومات إنستغرام\\n"
        "/cancel — إلغاء العملية الحالية"
    )

# /phone
async def phone_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل رقم الجوال.\\nأمثلة:\\n- +966512345678\\n- 966 512345678\\n- 0512345678"
    )
    return PHONE_STEP

async def phone_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = (update.message.text or "").strip()
    e164 = to_e164(user_input, default_region="SA")
    result = phone_summary(user_input, default_region="SA")

    owner = lookup_owner_name(e164)
    if owner:
        result += f"\\n- Owner Name: {owner}"

    if len(result) > 4000:
        result = result[:4000] + "\\n...\\n(تم قص النتيجة لطولها)"
    await update.message.reply_text(result)
    return ConversationHandler.END

# /ig
async def ig_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل اسم المستخدم في إنستغرام (بدون @).")
    return ASK_USERNAME

async def ig_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = (update.message.text or "").strip().lstrip("@")
    await update.message.reply_text("لحظة... جارِ جلب البيانات 🔎")
    text = instagram_info(username)
    if len(text) > 4000:
        text = text[:4000] + "\\n...\\n(النتيجة طويلة فتم قصّها)"
    await update.message.reply_text(text)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "تعليمات وأوامر"),
        BotCommand("phone", "تحليل رقم جوال (+اسم)"),
        BotCommand("ig", "معلومات إنستغرام"),
        BotCommand("cancel", "إلغاء العملية الحالية"),
    ])

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("يرجى ضبط متغير البيئة TELEGRAM_TOKEN")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    conv_phone = ConversationHandler(
        entry_points=[CommandHandler("phone", phone_entry)],
        states={PHONE_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_run)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        name="phone_conversation",
        persistent=False,
    )

    conv_ig = ConversationHandler(
        entry_points=[CommandHandler("ig", ig_entry)],
        states={ASK_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ig_username)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        name="ig_conversation",
        persistent=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_phone)
    app.add_handler(conv_ig)

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()