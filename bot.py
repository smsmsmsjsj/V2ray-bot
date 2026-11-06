import os
import logging
import threading
from io import BytesIO
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

import httpx
import qrcode
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, InputFile)
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ============== تنظیمات (از Environment Variables) ==============
TOKEN = os.environ.get("BOT_TOKEN", "")
SUPPORT = os.environ.get("SUPPORT_USERNAME", "@Dudgeuvsuvduv")

CARD_NUMBER = os.environ.get("CARD_NUMBER", "5041-7210-4718-7167")
CARD_OWNER = os.environ.get("CARD_OWNER", "افروز خادمی")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "5821799233"))

CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "@speedv2raymaeket")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@speedv2raymaeket")

PANEL_URL = os.environ.get("PANEL_URL", "https://www.speedur.org:2096").rstrip("/")
PANEL_API_KEY = os.environ.get("PANEL_API_KEY", "")
PANEL_GROUP_NAME = os.environ.get("PANEL_GROUP_ID", "group1")

PORT = int(os.environ.get("PORT", "10000"))

# ============== تعرفه‌ها ==============
DURATIONS = {
    "d1": ("1 ماهه", 30),
    "d2": ("2 ماهه", 60),
    "d3": ("3 ماهه", 90),
    "d5": ("5 ماهه", 150),
    "d12": ("1 ساله", 365),
}

VOLUMES = {
    "v10":  (10,  {"d1": 50000,  "d2": 55000,  "d3": 65000,  "d5": 65000,   "d12": 70000}),
    "v20":  (20,  {"d1": 95000,  "d2": 110000, "d3": 125000, "d5": 145000,  "d12": 160000}),
    "v30":  (30,  {"d1": 120000, "d2": 135000, "d3": 150000, "d5": 170000,  "d12": 190000}),
    "v50":  (50,  {"d1": 205000, "d2": 225000, "d3": 250000, "d5": 280000,  "d12": 310000}),
    "v80":  (80,  {"d1": 320000, "d2": 360000, "d3": 400000, "d5": 430000,  "d12": 460000}),
    "v100": (100, {"d1": 400000, "d2": 450000, "d3": 500000, "d5": 550000,  "d12": 600000}),
    "v150": (150, {"d1": 520000, "d2": 550000, "d3": 580000, "d5": 630000,  "d12": 700000}),
    "v200": (200, {"d1": 600000, "d2": 650000, "d3": 780000, "d5": 1000000, "d12": 1200000}),
}

# ============== متن‌ها و دکمه‌ها ==============
WELCOME = "🚀 به فروشگاه V2Ray خوش آمدید\n\nیکی از گزینه‌ها رو انتخاب کن:"

BTN_NEW = "🚀 خرید سرویس جدید"
BTN_TEST = "🧪 اکانت تست"
BTN_RENEW = "♻️ تمدید سرویس"
BTN_MY = "📲 سرویس های من"
BTN_HELP = "💡 آموزش استفاده"
BTN_CREDIT = "💰 اعتبار من"
BTN_INVITE = "👥 معرفی به دوستان"
BTN_SUPPORT = "🧑‍💻 تماس با پشتیبانی"
BTN_AGENT = "🤝 درخواست نمایندگی"

TEST_USED_FILE = "test_used.txt"
SERVICES_FILE = "user_services.txt"
REFERRALS_FILE = "referrals.txt"
AGENT_REQUESTS_FILE = "agent_requests.txt"
USERS_JOIN_FILE = "users_join_date.txt"


def load_lines(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []


def append_line(path, line):
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


TEST_USED = set(load_lines(TEST_USED_FILE))


def fmt(n):
    return f"{n:,}"


def generate_qr_buffer(url: str) -> BytesIO:
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio


# ============== کیبوردها ==============
def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [BTN_NEW],
            [BTN_TEST, BTN_RENEW],
            [BTN_MY],
            [BTN_HELP, BTN_CREDIT],
            [BTN_INVITE, BTN_SUPPORT],
            [BTN_AGENT],
        ],
        resize_keyboard=True)


def duration_menu():
    rows = [[InlineKeyboardButton(t, callback_data=f"dur_{k}")]
            for k, (t, _) in DURATIONS.items()]
    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def volume_menu(dk):
    rows = []
    for vk, (gb, prices) in VOLUMES.items():
        rows.append([InlineKeyboardButton(
            f"{gb} گیگ | {fmt(prices[dk])} تومان",
            callback_data=f"vol_{dk}_{vk}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت به مدت‌ها", callback_data="new")])
    return InlineKeyboardMarkup(rows)


def confirm_menu(dk, vk):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید و پرداخت", callback_data=f"pay_{dk}_{vk}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"dur_{dk}")],
    ])


def help_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("V2rayNG", callback_data="hlp_v2rayng"), InlineKeyboardButton("Streisand", callback_data="hlp_streisand")],
        [InlineKeyboardButton("Happ", callback_data="hlp_happ"), InlineKeyboardButton("V2rayTun", callback_data="hlp_v2raytun")],
        [InlineKeyboardButton("Android", callback_data="hlp_android"), InlineKeyboardButton("iOS", callback_data="hlp_ios")],
        [InlineKeyboardButton("Windows", callback_data="hlp_windows")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")],
    ])


def admin_decision_keyboard(user_id, dk, vk):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید", callback_data=f"approve_{user_id}_{dk}_{vk}"),
            InlineKeyboardButton("❌ رد", callback_data=f"reject_{user_id}_{dk}_{vk}"),
        ]
    ])


def order_text(dk, vk):
    title, days = DURATIONS[dk]
    gb, prices = VOLUMES[vk]
    return (f"⏳ مدت: {title} ({days} روز)\n"
            f"📦 حجم: {gb} گیگ (کل مدت)\n"
            f"💰 قیمت: {fmt(prices[dk])} تومان")


# ============== بررسی عضویت کانال ==============
async def is_member(bot, user_id):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"خطا در بررسی عضویت: {e}")
        return False


def join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")],
    ])


async def require_join(update_or_query, context, user_id):
    bot = context.bot
    if await is_member(bot, user_id):
        return False
    text = "برای استفاده از بات، اول باید تو کانال ما عضو بشی 👇"
    if hasattr(update_or_query, "message") and update_or_query.message:
        await update_or_query.message.reply_text(text, reply_markup=join_keyboard())
    else:
        await update_or_query.edit_message_text(text, reply_markup=join_keyboard())
    return True


# ============== پنل پاسارگاد (API) ==============
async def panel_create_user(username: str, gb: float, days: int):
    data_limit_bytes = int(gb * 1024 * 1024 * 1024)
    expire_timestamp = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())

    headers = {
        "X-Api-Key": PANEL_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "username": username,
        "status": "active",
        "data_limit": data_limit_bytes,
        "data_limit_reset_strategy": "no_reset",
        "expire": expire_timestamp,
        "group_ids": await get_group_ids(),
        "note": "ساخته‌شده توسط بات تلگرام",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0, verify=True) as client:
            resp = await client.post(f"{PANEL_URL}/api/user", headers=headers, json=payload)
            if resp.status_code in (200, 201):
                user_data = resp.json()
                sub_url = (user_data.get("subscription_url")
                          or user_data.get("links")
                          or "")
                if isinstance(sub_url, list):
                    sub_url = "\n".join(sub_url)
                return True, sub_url or "کاربر ساخته شد ولی لینک برنگشت."
            else:
                return False, f"خطای پنل ({resp.status_code}): {resp.text[:300]}"
    except Exception as e:
        return False, f"خطا در اتصال به پنل: {e}"


_GROUP_CACHE = {"ids": None}


async def get_group_ids():
    if _GROUP_CACHE["ids"] is not None:
        return _GROUP_CACHE["ids"]

    headers = {"X-Api-Key": PANEL_API_KEY}
    try:
        async with httpx.AsyncClient(timeout=20.0, verify=True) as client:
            resp = await client.get(f"{PANEL_URL}/api/groups", headers=headers)
            if resp.status_code == 200:
                groups = resp.json()
                items = groups.get("groups", groups) if isinstance(groups, dict) else groups
                for g in items:
                    if str(g.get("name", "")).lower() == str(PANEL_GROUP_NAME).lower():
                        _GROUP_CACHE["ids"] = [g["id"]]
                        return _GROUP_CACHE["ids"]
    except Exception:
        pass

    try:
        gid = int(PANEL_GROUP_NAME)
        _GROUP_CACHE["ids"] = [gid]
        return _GROUP_CACHE["ids"]
    except ValueError:
        pass

    _GROUP_CACHE["ids"] = [1]
    return _GROUP_CACHE["ids"]


# ============== هندلرها ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("order", None)
    user_id = update.effective_user.id

    # ثبت تاریخ ورود کاربر
    users_data = load_lines(USERS_JOIN_FILE)
    if not any(line.startswith(f"{user_id}|") for line in users_data):
        now_str = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        append_line(USERS_JOIN_FILE, f"{user_id}|{now_str}")

    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg[4:])
            except ValueError:
                referrer_id = None
            if referrer_id and referrer_id != user_id:
                existing = load_lines(REFERRALS_FILE)
                already_referred = any(line.split("|")[1] == str(user_id) for line in existing if "|" in line)
                if not already_referred:
                    append_line(REFERRALS_FILE, f"{referrer_id}|{user_id}")

    if await require_join(update, context, user_id):
        return
    await update.message.reply_text(WELCOME, reply_markup=main_keyboard())


async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if await is_member(context.bot, user_id):
        await query.message.delete()
        await context.bot.send_message(chat_id=user_id, text=WELCOME, reply_markup=main_keyboard())
    else:
        await query.answer("هنوز عضو نشدی! اول عضو کانال شو.", show_alert=True)


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await require_join(update, context, user_id):
        return

    text = update.message.text

    if text in (BTN_NEW, BTN_RENEW):
        await update.message.reply_text("⏳ انتخاب مدت زمان:", reply_markup=duration_menu())
        return

    if text == BTN_TEST:
        await handle_test_request(update, context)
        return

    if text == BTN_MY:
        await handle_my_services(update, context)
        return

    if text == BTN_CREDIT:
        lines = load_lines(SERVICES_FILE)
        my_services = [line for line in lines if line.split("|")[0] == str(user_id)]
        
        users_data = load_lines(USERS_JOIN_FILE)
        join_date = "نامشخص"
        for u in users_data:
            parts = u.split("|")
            if len(parts) >= 2 and parts[0] == str(user_id):
                join_date = parts[1]

        credit_text = (
            f"👤 **اطلاعات حساب کاربری شما:**\n\n"
            f"💰 موجودی: 0 تومان\n"
            f"🆔 آیدی عددی: `{user_id}`\n"
            f"📦 تعداد سرویس‌ها: {len(my_services)}\n"
            f"📅 تاریخ ورود به بات: {join_date}"
        )
        credit_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 افزایش موجودی", callback_data="add_credit")],
            [InlineKeyboardButton("🎁 اعمال کد هدیه", callback_data="gift_code")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]
        ])
        await update.message.reply_text(credit_text, parse_mode="Markdown", reply_markup=credit_markup)
        return

    if text == BTN_HELP:
        await update.message.reply_text("لطفا یک گزینه را انتخاب نمایید:", reply_markup=help_menu())
        return

    if text == BTN_SUPPORT:
        support_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 ارسال پیام به پشتیبانی", url=f"https://t.me/{SUPPORT.lstrip('@')}")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]
        ])
        await update.message.reply_text(
            "لطفا در قالب یک پیام درخواست و نکات قابل ملاحظه درباره خود را ارسال کرده و سپس تا تماس از طرف ادمین منتظر بمانید",
            reply_markup=support_markup
        )
        return

    if text == BTN_INVITE:
        await handle_invite(update, context)
        return

    if text == BTN_AGENT:
        await handle_agent_request(update, context)
        return

    await update.message.reply_text("یکی از گزینه‌های منو رو انتخاب کن 👇", reply_markup=main_keyboard())


async def handle_my_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lines = load_lines(SERVICES_FILE)
    my_services = [line for line in lines if line.split("|")[0] == str(user_id)]

    if not my_services:
        await update.message.reply_text(
            "📲 شما هنوز سرویسی خریداری نکرده‌اید.\n\nبرای خرید از منوی اصلی گزینه «🚀 خرید سرویس جدید» را بزنید.",
            reply_markup=main_keyboard())
        return

    for line in my_services:
        parts = line.split("|")
        _, uname, title, gb, sub_link = parts[0], parts[1], parts[2], parts[3], parts[4]
        
        msg_text = (
            f"📲 **اطلاعات سرویس شما**\n\n"
            f"👤 نام کاربری: `{uname}`\n"
            f"📦 حجم سرویس: {gb} گیگابایت\n"
            f"⏳ نوع اشتراک: {title}\n\n"
            f"🔗 **لینک اشتراک:**\n`{sub_link}`"
        )
        qr_buf = generate_qr_buffer(sub_link)
        
        service_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 دریافت QR Code", callback_data=f"qr_{uname}")],
            [InlineKeyboardButton("♻️ تمدید سرویس", callback_data="new")],
            [InlineKeyboardButton("❌ حذف کامل اکانت", callback_data=f"del_{uname}")]
        ])
        await update.message.reply_photo(photo=InputFile(qr_buf, filename="qr.png"), caption=msg_text, parse_mode="Markdown", reply_markup=service_markup)


async def handle_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"

    referrals = load_lines(REFERRALS_FILE)
    my_refs = [line for line in referrals if line.split("|")[0] == str(user_id)]
    count = len(my_refs)

    invite_text = (
        f"✨ دریافت سرویس‌های ایمن و پر سرعت بدون معطلی از طریق ربات\n\n"
        f"🔗 برای معرفی ربات به دوستان خود می‌توانید پیام بالا را برای دوستان خود ارسال کنید.\n\n"
        f"🎁 در صورت معرفی دوستان خود با لینک بالا، با هر خرید موفق هر کدام از آن‌ها 5% به عنوان هدیه به کیف پول شما واریز می‌گردد.\n\n"
        f"📊 **آمار دعوت شما**\n"
        f"• افراد وارد شده با لینک: {count}\n"
        f"• تعداد خرید ها: 0\n"
        f"• مبلغ خرید ها در یک ماه گذشته: 0 تومان\n\n"
        f"👇 **لینک اختصاصی دعوت شما:**\n`{link}`"
    )
    back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]])
    await update.message.reply_text(invite_text, parse_mode="Markdown", reply_markup=back_markup)


async def handle_agent_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name
    username = update.effective_user.username or "ندارد"

    append_line(AGENT_REQUESTS_FILE, f"{user_id}|{full_name}|{username}")

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(f"🤝 درخواست نمایندگی جدید\n\n"
             f"👤 نام: {full_name}\n"
             f"🆔 یوزرنیم: @{username}\n"
             f"🔢 آیدی: {user_id}"))

    agent_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]])
    await update.message.reply_text(
        "لطفا در قالب یک پیام درخواست و نکات قابل ملاحظه درباره خود را ارسال کرده و سپس تا تماس از طرف ادمین منتظر بمانید",
        reply_markup=agent_markup)


async def handle_test_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) in TEST_USED:
        await update.message.reply_text("❌ شما قبلاً اکانت تست گرفته‌اید. هر کاربر فقط یک بار می‌تواند تست بگیرد.")
        return

    await update.message.reply_text("⏳ در حال ساخت اکانت تست...")
    username = f"test_{user_id}_{int(datetime.now().timestamp())}"
    success, result = await panel_create_user(username, gb=0.1, days=1)

    if success:
        TEST_USED.add(str(user_id))
        append_line(TEST_USED_FILE, str(user_id))
        append_line(SERVICES_FILE, f"{user_id}|{username}|اکانت تست|0.1|{result}")
        
        caption_text = (
            f"🟢 اشتراک شما با موفقیت ساخته شد.\n"
            f"👤 نام کاربری: `{username}`\n\n"
            f"🔗 **لینک اشتراک شما:**\n`{result}`\n\n"
            f"📲 برای کپی کردن لینک بالا فقط کافیست آدرس لینک را یک بار لمس کنید!"
        )
        qr_buf = generate_qr_buffer(result)
        test_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]])
        await update.message.reply_photo(photo=InputFile(qr_buf, filename="test_qr.png"), caption=caption_text, parse_mode="Markdown", reply_markup=test_markup)
    else:
        await update.message.reply_text(f"❌ متاسفانه مشکلی پیش اومد:\n{result}\n\nبا پشتیبانی تماس بگیر: {SUPPORT}")


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "check_join":
        await check_join_callback(update, context)
        return

    await query.answer()

    if data == "menu":
        await query.message.delete()
        await context.bot.send_message(chat_id=query.from_user.id, text=WELCOME, reply_markup=main_keyboard())
        return

    if data == "add_credit" or data == "gift_code":
        await query.message.reply_text("این بخش به‌زودی فعال خواهد شد.", reply_markup=main_keyboard())
        return

    if data.startswith("hlp_"):
        app_key = data[4:]
        help_texts = {
            "v2rayng": "📱 **آموزش استفاده از V2rayNG (اندروید):**\n\n1. اپلیکیشن V2rayNG را دانلود و نصب کنید.\n2. لینک اشتراک خود را از ربات کپی کنید.\n3. وارد برنامه شده، روی دکمه + یا گوشه بالا بزنید و گزینه Import config from clipboard را انتخاب کنید.\n4. روی دکمه اتصال در پایین صفحه بزنید.",
            "streisand": "🍎 **آموزش استفاده از Streisand (آیفون):**\n\n1. اپلیکیشن Streisand را از اپ‌استور دانلود کنید.\n2. لینک اشتراک را کپی کرده و داخل برنامه وارد نمایید.",
            "happ": "📱 **آموزش استفاده از Happ:**\n\n1. برنامه را نصب و باز کنید.\n2. لینک اشتراک را ایمپورت کنید.",
            "v2raytun": "🚀 **آموزش استفاده از V2rayTun:**\n\n1. اپلیکیشن را باز کرده و لینک اشتراک را اضافه کنید.",
            "android": "🤖 **راهنمای اندروید:** استفاده از V2rayNG بهترین گزینه است.",
            "ios": "🍏 **راهنمای آیفون:** استفاده از Streisand یا Foxray.",
            "windows": "💻 **راهنمای ویندوز:** استفاده از نرم‌افزار v2rayN."
        }
        text = help_texts.get(app_key, "آموزش یافت نشد.")
        back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به آموزش‌ها", callback_data="back_to_help")]])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back_markup)
        return

    if data == "back_to_help":
        await query.edit_message_text("لطفا یک گزینه را انتخاب نمایید:", reply_markup=help_menu())
        return

    if data == "new":
        await query.edit_message_text("⏳ انتخاب مدت زمان:", reply_markup=duration_menu())
        return

    if data.startswith("dur_"):
        dk = data[4:]
        await query.edit_message_text(f"مدت انتخابی: {DURATIONS[dk][0]}\n\n📦 حالا حجم رو انتخاب کن:",
                                      reply_markup=volume_menu(dk))
        return

    if data.startswith("vol_"):
        _, dk, vk = data.split("_", 2)
        text = order_text(dk, vk) + "\n\nآیا تایید می‌کنی؟"
        await query.edit_message_text(text, reply_markup=confirm_menu(dk, vk))
        return

    if data.startswith("pay_"):
        _, dk, vk = data.split("_", 2)
        context.user_data["order"] = {"dk": dk, "vk": vk}
        title, days = DURATIONS[dk]
        gb, prices = VOLUMES[vk]
        price = prices[dk]
        text = (f"💳 لطفاً مبلغ {fmt(price)} تومان را به کارت زیر واریز کنید:\n\n"
               f"شماره کارت: `{CARD_NUMBER}`\n"
               f"به نام: {CARD_OWNER}\n\n"
               f"سپس عکس رسید پرداخت را همینجا برای من ارسال کنید. ✅")
        await query.edit_message_text(text, parse_mode="Markdown")
        return

    if data.startswith("approve_") or data.startswith("reject_"):
        await handle_admin_decision(update, context, data)
        return


async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    order = context.user_data.get("order")

    if not order:
        await update.message.reply_text("لطفاً اول از منو سرویس مورد نظرتون رو انتخاب کنید.", reply_markup=main_keyboard())
        return

    dk, vk = order["dk"], order["vk"]
    title, days = DURATIONS[dk]
    gb, prices = VOLUMES[vk]
    price = prices[dk]

    caption = (f"🧾 رسید جدید دریافت شد\n\n"
              f"👤 کاربر: {update.effective_user.full_name} (@{update.effective_user.username})\n"
              f"🆔 آیدی: {user_id}\n"
              f"⏳ مدت: {title}\n"
              f"📦 حجم: {gb} گیگ\n"
              f"💰 مبلغ: {fmt(price)} تومان")

    photo = update.message.photo[-1].file_id
    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=photo,
        caption=caption,
        reply_markup=admin_decision_keyboard(user_id, dk, vk))

    await update.message.reply_text("✅ رسید شما برای بررسی ارسال شد. لطفاً منتظر تایید بمانید.")


async def handle_admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("فقط ادمین می‌تونه این کارو بکنه.", show_alert=True)
        return

    action, user_id_str, dk, vk = data.split("_", 3)
    user_id = int(user_id_str)
    title, days = DURATIONS[dk]
    gb, prices = VOLUMES[vk]

    if action == "reject":
        await query.edit_message_caption(caption=query.message.caption + "\n\n❌ رد شد.")
        await context.bot.send_message(chat_id=user_id,
                                       text=f"❌ متاسفانه رسید شما تایید نشد. لطفاً با پشتیبانی تماس بگیرید: {SUPPORT}")
        return

    await query.edit_message_caption(caption=query.message.caption + "\n\n⏳ در حال ساخت کاربر در پنل...")
    username = f"user_{user_id}_{int(datetime.now().timestamp())}"
    success, result = await panel_create_user(username, gb=gb, days=days)

    if success:
        append_line(SERVICES_FILE, f"{user_id}|{username}|{title}|{gb}|{result}")
        await query.edit_message_caption(caption=query.message.caption + "\n\n✅ تایید شد و کاربر ساخته شد.")
        
        caption_text = (
            f"🎉 پرداخت شما تایید شد!\n\n"
            f"⏳ مدت: {title}\n"
            f"📦 حجم: {gb} گیگ\n\n"
            f"🔗 **لینک اشتراک شما:**\n`{result}`"
        )
        qr_buf = generate_qr_buffer(result)
        await context.bot.send_photo(chat_id=user_id, photo=InputFile(qr_buf, filename="sub_qr.png"), caption=caption_text, parse_mode="Markdown")
    else:
        await query.edit_message_caption(caption=query.message.caption + f"\n\n❌ خطا در ساخت کاربر:\n{result}")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"پرداخت شما تایید شد ولی در ساخت سرویس مشکلی پیش اومد. پشتیبانی به‌زودی بهتون سرویس رو می‌ده: {SUPPORT}")


# ============== سرور سلامت برای UptimeRobot ==============
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - bot is running")

    def log_message(self, format, *args):
        pass


def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info(f"سرور سلامت روی پورت {PORT} فعال شد")
    server.serve_forever()


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده!")
    if not PANEL_API_KEY:
        logger.warning("PANEL_API_KEY تنظیم نشده - ساخت کاربر در پنل کار نخواهد کرد.")

    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.PHOTO, receipt_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logger.info("بات در حال اجراست...")
    app.run_polling()


if __name__ == "__main__":
    main()
