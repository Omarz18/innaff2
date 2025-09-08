import os
import re
import requests
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)

# ===============================
# ===== قسمك الأصلي كما هو =====
# ===============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ASK_USERNAME = 1

def _livecounts_headers():
    return {
        'Host': 'api.livecounts.io',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/111.0',
        'Accept': '*/*',
        'Accept-Language': 'ar,en-US;q=0.7,en;q=0.3',
        'Accept-Encoding': 'gzip, deflate',
        'Origin': 'https://livecounts.io'
    }

def _storiesig_headers():
    return {
        'Host': 'storiesig.info',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/111.0',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'ar,en-US;q=0.7,en;q=0.3',
        'Accept-Encoding': 'gzip, deflate',
        'Referer': 'https://storiesig.info/en/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'Te': 'trailers'
    }

def fetch_from_livecounts(username: str) -> str:
    h = _livecounts_headers()
    parts = []
    try:
        r1 = requests.get(f'https://api.livecounts.io/instagram-live-follower-counter/data/{username}', headers=h, timeout=15)
        if '"success":true' in r1.text:
            jd = r1.json()
            parts += [
                f"- Name: {jd.get('name')}",
                f"- Verified: {jd.get('verified')}",
                f"- Bio: {jd.get('description')}",
            ]
            if jd.get('avatar'):
                parts.append(f"- Profile Pic URL: {jd.get('avatar')}")
        else:
            r2 = requests.get(f'https://api.livecounts.io/instagram-live-follower-counter/search/{username}', headers=h, timeout=15)
            if '"success":true' in r2.text:
                m = re.findall(r"(.*?),(.*?),(.*?),(.*?)]", str(r2.json().get("userData")))
                if m:
                    t = m[0]
                    name = str(t[2]).replace("'username': '", '').replace("'", "")
                    verified = str(t[3]).replace("'verified':", '').replace('}', '')
                    parts.append(f"- Name: {name}")
                    parts.append(f"- Verified: {verified}")
                    maybe_pic = str(t[0]).replace("'", '').replace("avatar", '').replace("[{:", '')
                    if maybe_pic.strip():
                        parts.append(f"- Profile Pic URL: {maybe_pic}")
        r3 = requests.get(f'https://api.livecounts.io/instagram-live-follower-counter/stats/{username}', headers=h, timeout=15)
        if '"success":true' in r3.text:
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
    except Exception:
        pass
    return "\n".join(parts).strip()

def fetch_from_storiesig(username: str) -> str | None:
    try:
        r = requests.get(f'https://storiesig.info/api/ig/profile/{username}', headers=_storiesig_headers(), timeout=15)
        if username in r.text:
            res = r.json().get("result", {})
            return "\n".join([
                f"- Name: {res.get('full_name')}",
                f"- Bio: {res.get('biography')}",
                f"- userID: {res.get('id')}",
                f"- Private: {res.get('is_private')}",
                f"- Followers Count: {res.get('edge_followed_by',{}).get('count')}",
                f"- Following: {res.get('edge_follow',{}).get('count')}",
                f"- Posts: {res.get('edge_owner_to_timeline_media',{}).get('count')}",
                f"- Profile Pic URL: {res.get('profile_pic_url')}",
            ])
    except Exception:
        return None
    return None

def fetch_from_private_api(username: str) -> str | None:
    try:
        r = requests.post(
            "https://i.instagram.com:443/api/v1/users/lookup/",
            headers={
                "Connection": "close", "X-IG-Connection-Type": "WIFI",
                "mid": "XOSINgABAAG1IDmaral3noOozrK0rrNSbPuSbzHq",
                "X-IG-Capabilities": "3R4=",
                "Accept-Language": "ar-sa",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "User-Agent": "Instagram 99.4.0 vv1ck_TweakPY (TweakPY_vv1ck)",
                "Accept-Encoding": "gzip, deflate"
            },
            data={"signed_body": f"35a2d547d3b6ff400f713948cdffe0b789a903f86117eb6e2f3e573079b2f038.{{\"q\":\"{username}\"}}"},
            timeout=15
        )
        if 'No users found' in r.text or '"spam":true' in r.text:
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
        return "\n".join(lines)
    except Exception:
        return None

def instagram_info(username: str) -> str:
    for fn in (fetch_from_private_api, fetch_from_storiesig, fetch_from_livecounts):
        res = fn(username)
        if res: return res
    return "تعذّر الحصول على معلومات هذا الحساب حالياً."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلًا 👋\nاكتب /ig للبحث عن حساب إنستغرام.\nواكتب /phone لاستخراج اسم مالك الرقم.")

async def ig_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل اسم المستخدم في إنستغرام (بدون @).")
    return ASK_USERNAME

async def ig_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = (update.message.text or "").strip().lstrip("@")
    await update.message.reply_text("لحظة... جارِ جلب البيانات 🔎")
    text = instagram_info(username)
    if len(text) > 4000:
        text = text[:4000] + "\n...\n(النتيجة طويلة فتم قصّها)"
    await update.message.reply_text(text)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END

# ============================================
# ===== إضافة بسيطة: /phone يستهلك سكربتك ====
# ============================================

from subprocess import Popen, PIPE, CalledProcessError, TimeoutExpired

PHONE_STEP = 100

# قابلة للتعديل بمتغيرات بيئة لكن افتراضياً على مجلدك واسم سكربتك
OWNER_INTERACTIVE_SCRIPT = os.getenv("OWNER_INTERACTIVE_SCRIPT", "who-is-this.py")
OWNER_INTERACTIVE_CWD = os.getenv("OWNER_INTERACTIVE_CWD", "Who-is-this-main")
OWNER_STDIN_TEMPLATE = os.getenv("OWNER_STDIN_TEMPLATE", "2\n{number}\n99\n")
OWNER_OUTPUT_REGEX = os.getenv("OWNER_OUTPUT_REGEX", r"\[\+\]\s*name\s*:\s*(.+)")
OWNER_TIMEOUT = int(os.getenv("OWNER_TIMEOUT", "25"))

async def phone_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل رقم الجوال بصيغة دولية مثل: +9665xxxxxxxx")
    return PHONE_STEP

def _run_who_is_this_interactive(e164: str) -> str | None:
    if not e164:
        return None
    try:
        cmd = ["python", OWNER_INTERACTIVE_SCRIPT]
        p = Popen(cmd, cwd=OWNER_INTERACTIVE_CWD or None, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        stdin_data = (OWNER_STDIN_TEMPLATE.replace("{number}", e164)).encode("utf-8")
        out, err = p.communicate(input=stdin_data, timeout=OWNER_TIMEOUT)
        text = (out or b"").decode("utf-8", "ignore")
        if not text:
            text = (err or b"").decode("utf-8", "ignore")
        if text:
            m = re.search(OWNER_OUTPUT_REGEX, text, re.IGNORECASE)
            if m and m.group(1).strip():
                return m.group(1).strip()
    except (CalledProcessError, TimeoutExpired) as e:
        pass
    return None

async def phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    # نمرره كما هو إلى سكربتك (سكربتك تتعامل معه)
    owner = _run_who_is_this_interactive(raw)
    if owner:
        await update.message.reply_text(f"[+] Owner Name: {owner}")
    else:
        await update.message.reply_text("تعذر استخراج اسم المالك من السكربت.")
    return ConversationHandler.END

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("يرجى ضبط متغير البيئة TELEGRAM_TOKEN")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_ig = ConversationHandler(
        entry_points=[CommandHandler("ig", ig_entry)],
        states={ASK_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ig_username)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        name="ig_conversation",
        persistent=False,
    )

    conv_phone = ConversationHandler(
        entry_points=[CommandHandler("phone", phone_entry)],
        states={PHONE_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_number)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        name="phone_conversation",
        persistent=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_ig)
    app.add_handler(conv_phone)

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()