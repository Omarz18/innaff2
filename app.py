import os
import re
import logging
import requests
import phonenumbers
from typing import Optional
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

# ===== حالات المحادثة =====
ASK_USERNAME = 1      # /ig
PHONE_STEP = 10       # /phone

# =========================== إنستغرام (/ig) ===========================
def _livecounts_headers():
    return {
        'Host': 'api.livecounts.io',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/111.0',
        'Accept': '*/*',
        'Accept-Language': 'ar,en-US;q=0.7,en;q=0.3',
        'Accept-Encoding': 'gzip, deflate',
        'Origin': 'https://livecounts.io',
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
        'Te': 'trailers',
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
    return "\n".join(parts).strip()

def fetch_from_storiesig(username: str) -> Optional[str]:
    try:
        r = requests.get(
            f'https://storiesig.info/api/ig/profile/{username}',
            headers=_storiesig_headers(), timeout=15
        )
        if r.ok and (username in r.text):
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
    except Exception as e:
        log.warning("storiesig error: %s", e)
    return None

def fetch_from_private_api(username: str) -> Optional[str]:
    try:
        r = requests.post(
            "https://i.instagram.com:443/api/v1/users/lookup/",
            headers={
                "Connection": "close","X-IG-Connection-Type":"WIFI","mid":"XOSINgABAAG1IDmaral3noOozrK0rrNSbPuSbzHq",
                "X-IG-Capabilities":"3R4=","Accept-Language":"ar-sa",
                "Content-Type":"application/x-www-form-urlencoded; charset=UTF-8",
                "User-Agent":"Instagram 99.4.0 vv1ck_TweakPY (TweakPY_vv1ck)",
                "Accept-Encoding":"gzip, deflate"
            },
            data={"signed_body": f"35a2d547d3b6ff400f713948cdffe0b789a903f86117eb6e2f3e573079b2f038.{{\"q\":\"{username}\"}}"},
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
        return "\n".join(lines)
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

def lookup_owner_name_local(e164: Optional[str]) -> Optional[str]:
    """
    يحاول استخدام مزودك المحلي (الموجود داخل نفس الريبو).
    - أولاً: import لموديول بأسماء شائعة.
    - ثانيًا: تشغيل سكربت CLI إن وجد.
    يجب أن يعيد الموديول/السكربت اسم المالك كنص.
    """
    if not e164:
        return None

    # 1) محاولة import لموديولات شائعة
    module_names = [
        "who_is_this", "whoisthis", "who_is_this_main", "who_is_owner", "owner_lookup"
    ]
    for mname in module_names:
        try:
            mod = __import__(mname)
            # جرّب دوال شائعة
            for fn in ("lookup_owner", "get_owner", "owner_name", "lookup"):
                if hasattr(mod, fn):
                    name = getattr(mod, fn)(e164)
                    if isinstance(name, str) and name.strip():
                        return name.strip()
        except Exception:
            pass

    # 2) محاولة تشغيل سكربت CLI داخل الريبو
    # أمثلة أسماء محتملة:
    script_names = [
        "who_is_this.py", "whoisthis.py", "main.py", "lookup.py", "owner_lookup.py"
    ]
    import subprocess, json, shlex
    for sname in script_names:
        if os.path.exists(os.path.join(os.getcwd(), sname)):
            try:
                # نفترض أن السكربت يقبل --number ويرجع JSON فيه name/owner/caller_name
                cmd = ["python", sname, "--number", e164, "--json"]
                out = subprocess.check_output(cmd, timeout=25).decode("utf-8", "ignore")
                # حاول JSON
                try:
                    j = json.loads(out)
                    for key in ("name", "owner", "caller_name"):
                        if key in j and str(j[key]).strip():
                            return str(j[key]).strip()
                except Exception:
                    # جرّب أن الإخراج نصي فقط
                    line = out.strip().splitlines()[-1].strip() if out.strip() else ""
                    if line:
                        return line
            except Exception:
                pass

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
    return "\n".join(lines)

# =========================== Handlers ===========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلًا 👋\n"
        "الأوامر المتاحة:\n"
        "/phone — استخراج تفاصيل رقم جوال (+ اسم المالك من مزودك المحلي إن وُجد)\n"
        "/ig — معلومات إنستغرام\n"
        "/cancel — إلغاء العملية الحالية"
    )

# /phone
async def phone_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل رقم الجوال.\nأمثلة:\n- +966512345678\n- 966 512345678\n- 0512345678"
    )
    return PHONE_STEP

async def phone_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = (update.message.text or "").strip()
    result = phone_summary(user_input, default_region="SA")

    # اسم صاحب الرقم عبر مزودك المحلي (إن وُجد)
    e164 = to_e164(user_input, default_region="SA")
    owner = lookup_owner_name_local(e164)
    if owner:
        result += f"\n- Owner Name: {owner}"

    if len(result) > 4000:
        result = result[:4000] + "\n...\n(تم قص النتيجة لطولها)"
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
        text = text[:4000] + "\n...\n(النتيجة طويلة فتم قصّها)"
    await update.message.reply_text(text)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "تعليمات وأوامر"),
        BotCommand("phone", "تحليل رقم جوال (+اسم محلي)"),
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

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()