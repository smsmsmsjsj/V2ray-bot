import os
import json
import logging
import threading
from io import BytesIO
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

import httpx
import qrcode
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, InputFile, CopyTextButton)
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
CARD_DIGITS = "".join(c for c in CARD_NUMBER if c.isdigit())
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
BTN_CANCEL_RECEIPT = "❌ لغو ارسال رسید و بازگشت"

MENU_BUTTONS = {BTN_NEW, BTN_TEST, BTN_RENEW, BTN_MY, BTN_HELP, BTN_CREDIT,
                BTN_INVITE, BTN_SUPPORT, BTN_AGENT, BTN_CANCEL_RECEIPT}

TEST_USED_FILE = "test_used.txt"
SERVICES_FILE = "user_services.txt"
REFERRALS_FILE = "referrals.txt"
USERS_JOIN_FILE = "users_join_date.txt"
ORDERS_FILE = "orders.json"
BALANCES_FILE = "balances.json"


# ============== فایل‌ها ==============
def load_lines(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []


def append_line(path, line):
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def get_balance(user_id):
    return int(load_json(BALANCES_FILE).get(str(user_id), 0))


def add_balance(user_id, amount):
    b = load_json(BALANCES_FILE)
    b[str(user_id)] = int(b.get(str(user_id), 0)) + int(amount)
    save_json(BALANCES_FILE, b)


def create_order(data):
    orders = load_json(ORDERS_FILE)
    oid = str(int(datetime.now().timestamp() * 1000))
    data["status"] = "pending"
    orders[oid] = data
    save_json(ORDERS_FILE, orders)
    return oid


def set_order_status(oid, status):
    orders = load_json(ORDERS_FILE)
    if oid in orders:
        orders[oid]["status"] = status
        save_json(ORDERS_FILE, orders)


TEST_USED = set(load_lines(TEST_USED_FILE))


def fmt(n):
    return f"{int(n):,}"


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


def cancel_receipt_keyboard():
    return ReplyKeyboardMarkup([[BTN_CANCEL_RECEIPT]], resize_keyboard=True)


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
        [InlineKeyboardButton("🎁 اعمال کد تخفیف", callback_data="apply_discount")],
        [InlineKeyboardButton("💳 پرداخت ریالی", callback_data=f"pay_{dk}_{vk}")],
        [InlineKeyboardButton("🏠 بازگشت به منوی اصلی", callback_data="menu")],
    ])


def help_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Android", callback_data="hlp_android")],
        [InlineKeyboardButton("iOS", callback_data="hlp_ios")],
        [InlineKeyboardButton("Windows", callback_data="hlp_windows")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")],
    ])


def admin_keyboard(oid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تایید", callback_data=f"approve_{oid}"),
        InlineKeyboardButton("❌ رد", callback_data=f"reject_{oid}"),
    ]])


def pay_text(amount):
    return (
        f"لطفاً مبلغ *{fmt(amount)} تومان* ({fmt(amount * 10)} ریال) به کارت زیر واریز نمایید.\n\n"
        f"====================\n"
        f"`{CARD_NUMBER}`\n"
        f"{CARD_OWNER}\n"
        f"====================\n\n"
        f"📷 عکس رسید خود را در این مرحله ارسال نمایید.\n\n"
        f"⚠️ از اپلیکیشن آپ و تاپ با توجه به محدودیت روزانه برای انتقال استفاده نکنید.\n"
        f"⚠️ مبلغ را دقیق واریز کنید؛ و از رند کردن آن خودداری فرمایید.\n"
        f"⚠️ مبلغی که کپی میکنید ریالی توجه کنید.\n\n"
        f"👇 بعد از واریز دکمه ارسال رسید رو بزنید"
    )


def pay_markup(amount):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 کپی مبلغ", copy_text=CopyTextButton(text=str(amount * 10))),
         InlineKeyboardButton("📋 کپی شماره کارت", copy_text=CopyTextButton(text=CARD_DIGITS))],
        [InlineKeyboardButton("📤 ارسال رسید پرداخت", callback_data="send_receipt")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")],
    ])


def order_text(dk, vk, username, factor_id):
    title, days = DURATIONS[dk]
    gb, prices = VOLUMES[vk]
    return (
        f"پیش فاکتور شماره {factor_id}\n\n"
        f"👤 نام کاربری: `{username}`\n"
        f"🌐 سرور: SpeedUR\n"
        f"📦 دسته محصول: {title}\n"
        f"🏷 نوع سرویس: {gb} گیگ | {days} روزه\n"
        f"📅 مدت اعتبار: {days} روز\n"
        f"📦 حجم بسته: {gb} گیگابایت\n\n"
        f"💳 مبلغ قابل پرداخت: {fmt(prices[dk])} تومان\n\n"
        f"سفارش شما آماده پرداخت است"
    )


def clear_flow(context):
    for k in ("state", "pending", "order"):
        context.user_data.pop(k, None)


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


async def require_join(update, context, user_id):
    if user_id == ADMIN_ID:
        return False
    if await is_member(context.bot, user_id):
        return False
    text = "برای استفاده از بات، اول باید تو کانال ما عضو بشی 👇"
    if update.message:
        await update.message.reply_text(text, reply_markup=join_keyboard())
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=join_keyboard())
    return True


# ============== پنل پاسارگاد (API) ==============
def headers():
    return {"X-Api-Key": PANEL_API_KEY, "Content-Type": "application/json"}


def fix_sub(url):
    if isinstance(url, list):
        url = "\n".join(url)
    if url and url.startswith("/"):
        url = PANEL_URL + url
    return url


_GROUP_CACHE = {"ids": None}


async def get_group_ids():
    if _GROUP_CACHE["ids"] is not None:
        return _GROUP_CACHE["ids"]
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{PANEL_URL}/api/groups", headers=headers())
            if resp.status_code == 200:
                groups = resp.json()
                items = groups.get("groups", groups) if isinstance(groups, dict) else groups
                for g in items:
                    if str(g.get("name", "")).lower() == str(PANEL_GROUP_NAME).lower():
                        _GROUP_CACHE["ids"] = [g["id"]]
                        return _GROUP_CACHE["ids"]
    except Exception as e:
        logger.warning(f"خطا در گرفتن گروه‌ها: {e}")
    try:
        _GROUP_CACHE["ids"] = [int(PANEL_GROUP_NAME)]
    except ValueError:
        _GROUP_CACHE["ids"] = [1]
    return _GROUP_CACHE["ids"]


async def panel_create_user(username: str, gb: float, days: int):
    expire_ts = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())
    payload = {
        "username": username,
        "status": "active",
        "data_limit": int(gb * 1024 ** 3),
        "data_limit_reset_strategy": "no_reset",
        "expire": expire_ts,
        "group_ids": await get_group_ids(),
        "note": "ساخته‌شده توسط بات تلگرام",
    }
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(f"{PANEL_URL}/api/user", headers=headers(), json=payload)
            if resp.status_code in (200, 201):
                d = resp.json()
                sub = fix_sub(d.get("subscription_url") or d.get("links") or "")
                return True, sub or "کاربر ساخته شد ولی لینک برنگشت."
            return False, f"خطای پنل ({resp.status_code}): {resp.text[:300]}"
    except Exception as e:
        return False, f"خطا در اتصال به پنل: {e}"


async def panel_change_status(username: str, status: str):
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.put(f"{PANEL_URL}/api/user/{username}",
                                    headers=headers(), json={"status": status})
            return resp.status_code in (200, 201)
    except Exception:
        return False


async def panel_reset_sub(username: str):
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(f"{PANEL_URL}/api/user/{username}/reset", headers=headers())
            if resp.status_code in (200, 201):
                d = resp.json()
                return fix_sub(d.get("subscription_url") or d.get("links") or "")
    except Exception:
        pass
    return ""


# ============== ارسال سرویس به مشتری (مثل اسپید یور) ==============
async def send_service_to_user(bot, user_id, username, link, days, gb):
    caption = (
        f"🟢 اشتراک شما با موفقیت ساخته شد.\n"
        f"👤 نام کاربری شما :\n`{username}`\n\n"
        f"🌐 لینک اشتراک شما:\n\n"
        f"`{link}`\n\n"
        f"📲 برای کپی کردن لینک بالا فقط کافیست آدرس لینک را یک بار لمس کنید!"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏳ زمان اشتراک", callback_data="none"),
         InlineKeyboardButton(f"{days} روز", callback_data="none")],
        [InlineKeyboardButton("🌐 حجم سرویس", callback_data="none"),
         InlineKeyboardButton(f"{gb} گیگابایت", callback_data="none")],
        [InlineKeyboardButton("📥 دریافت QR Code", callback_data=f"qr_{username}")],
        [InlineKeyboardButton("📚 آموزش نصب", callback_data="hlp_android")],
    ])
    await bot.send_photo(chat_id=user_id,
                         photo=InputFile(generate_qr_buffer(link), filename="sub_qr.png"),
                         caption=caption, parse_mode="Markdown", reply_markup=markup)


def service_view(uname, gb, title, link, active=True):
    status = "🟢 وضعیت: فعال" if active else "🔴 وضعیت: غیرفعال"
    text = (
        f"📌 **اطلاعات سرویس شما**\n\n"
        f"👤 نام کاربری: `{uname}`\n"
        f"{status}\n"
        f"🌐 نام پنل: SpeedUR\n\n"
        f"📦 حجم سرویس: {gb} گیگابایت\n"
        f"⏳ نوع اشتراک: {title}\n\n"
        f"🔗 **لینک اشتراک:**\n`{link}`"
    )
    toggle = (InlineKeyboardButton("⏹ توقف سرویس", callback_data=f"stop_{uname}") if active
              else InlineKeyboardButton("▶️ فعالسازی سرویس", callback_data=f"active_{uname}"))
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 دریافت QR Code", callback_data=f"qr_{uname}")],
        [toggle, InlineKeyboardButton("♻️ تمدید سرویس", callback_data="new")],
        [InlineKeyboardButton("❌ حذف لینک فعلی و دریافت لینک جدید", callback_data=f"reset_confirm_{uname}")],
        [InlineKeyboardButton("🔙 بازگشت به لیست سرویس ها", callback_data="back_to_services")],
    ])
    return text, markup


def find_service(uname):
    for line in load_lines(SERVICES_FILE):
        parts = line.split("|")
        if len(parts) >= 5 and parts[1] == uname:
            return parts
    return None


# ============== هندلرها ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_flow(context)
    user_id = update.effective_user.id

    users_data = load_lines(USERS_JOIN_FILE)
    if not any(line.startswith(f"{user_id}|") for line in users_data):
        append_line(USERS_JOIN_FILE, f"{user_id}|{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}")

    if context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0][4:])
        except ValueError:
            referrer_id = None
        if referrer_id and referrer_id != user_id:
            existing = load_lines(REFERRALS_FILE)
            if not any(l.split("|")[1] == str(user_id) for l in existing if "|" in l):
                append_line(REFERRALS_FILE, f"{referrer_id}|{user_id}")

    if await require_join(update, context, user_id):
        return
    await update.message.reply_text(WELCOME, reply_markup=main_keyboard())


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await require_join(update, context, user_id):
        return

    text = update.message.text

    # زدن هر دکمه منو، مرحله قبلی را لغو می‌کند
    if text in MENU_BUTTONS:
        clear_flow(context)
        if text == BTN_CANCEL_RECEIPT:
            await update.message.reply_text("به منوی اصلی بازگشتید!", reply_markup=main_keyboard())
            return

    state = context.user_data.get("state")

    if state == "waiting_custom_credit":
        context.user_data.pop("state", None)
        try:
            amount = int(text.replace(",", "").replace("٬", "").strip())
            if amount < 10000:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ مبلغ نامعتبر است (حداقل 10,000 تومان)",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="add_credit")]]))
            return
        context.user_data["pending"] = {"kind": "credit", "amount": amount}
        await update.message.reply_text(pay_text(amount), parse_mode="Markdown",
                                        reply_markup=pay_markup(amount))
        return

    if state == "waiting_gift_code":
        context.user_data.pop("state", None)
        await update.message.reply_text(
            "❌ کد هدیه یافت نشد.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]]))
        return

    if state == "waiting_agent_msg":
        context.user_data.pop("state", None)
        u = update.effective_user
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"🤝 درخواست نمایندگی جدید\n\n👤 نام: {u.full_name}\n"
                  f"🆔 یوزرنیم: @{u.username or 'ندارد'}\n🔢 آیدی: {user_id}\n\n💬 متن پیام:\n{text}"))
        await update.message.reply_text("🚀 پیام شما ارسال شد منتظر پاسخ مدیریت باشید.",
                                        reply_markup=main_keyboard())
        return

    if state == "waiting_username":
        username = text.strip()
        if len(username) < 3 or len(username) > 20 or not username.replace("_", "").isalnum() \
                or not username.isascii():
            await update.message.reply_text(
                "❌ نام کاربری نامعتبر است. لطفاً نام کاربری با حروف لاتین و حداکثر 20 کاراکتر وارد نمایید:")
            return
        order = context.user_data.get("order")
        if not order:
            context.user_data.pop("state", None)
            await update.message.reply_text("نشست منقضی شد. دوباره از منو شروع کن.", reply_markup=main_keyboard())
            return
        context.user_data.pop("state", None)
        order["username"] = username.lower()
        factor_id = int(datetime.now().timestamp()) % 1000000
        await update.message.reply_text(
            order_text(order["dk"], order["vk"], order["username"], factor_id),
            parse_mode="Markdown", reply_markup=confirm_menu(order["dk"], order["vk"]))
        return

    if state == "waiting_receipt":
        await update.message.reply_text("📷 لطفاً تصویر رسید پرداخت را به صورت عکس ارسال نمایید.",
                                        reply_markup=cancel_receipt_keyboard())
        return

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
        my_services = [l for l in load_lines(SERVICES_FILE) if l.split("|")[0] == str(user_id)]
        join_date = "نامشخص"
        for u in load_lines(USERS_JOIN_FILE):
            p = u.split("|")
            if len(p) >= 2 and p[0] == str(user_id):
                join_date = p[1]
        credit_text = (
            f"👤 **اطلاعات حساب کاربری شما:**\n\n"
            f"💰 موجودی: {fmt(get_balance(user_id))} تومان\n"
            f"🆔 آیدی عددی: `{user_id}`\n"
            f"📦 تعداد سرویس‌ها: {len(my_services)}\n"
            f"📅 تاریخ ورود به بات: {join_date}\n\n"
            f"🕒 {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}"
        )
        await update.message.reply_text(
            credit_text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 افزایش موجودی", callback_data="add_credit")],
                [InlineKeyboardButton("🎁 اعمال کد هدیه", callback_data="gift_code")],
                [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]]))
        return

    if text == BTN_HELP:
        await update.message.reply_text("لطفا یک گزینه را انتخاب نمایید:", reply_markup=help_menu())
        return

    if text == BTN_SUPPORT:
        await update.message.reply_text(
            "لطفا در قالب یک پیام درخواست و نکات قابل ملاحظه درباره خود را ارسال کرده و سپس تا تماس از طرف ادمین منتظر بمانید",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 ارسال پیام به پشتیبانی", url=f"https://t.me/{SUPPORT.lstrip('@')}")],
                [InlineKeyboardButton("❓ سوالات متداول", callback_data="faq")],
                [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]]))
        return

    if text == BTN_INVITE:
        await handle_invite(update, context)
        return

    if text == BTN_AGENT:
        context.user_data["state"] = "waiting_agent_msg"
        await update.message.reply_text(
            "🤝 لطفا در قالب یک پیام درخواست و نکات قابل ملاحظه درباره خود را ارسال کرده و سپس تا تماس از طرف ادمین منتظر بمانید",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]]))
        return

    await update.message.reply_text("یکی از گزینه‌های منو رو انتخاب کن 👇", reply_markup=main_keyboard())


async def handle_my_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mine = [l for l in load_lines(SERVICES_FILE) if l.split("|")[0] == str(user_id)]
    if not mine:
        await update.message.reply_text(
            "📲 شما هنوز سرویسی خریداری نکرده‌اید.\n\nبرای خرید از منوی اصلی گزینه «🚀 خرید سرویس جدید» را بزنید.",
            reply_markup=main_keyboard())
        return
    kb = [[InlineKeyboardButton(f"🟢 {l.split('|')[1]}", callback_data=f"serv_{l.split('|')[1]}")] for l in mine]
    kb.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")])
    await update.message.reply_text("🔹 برای مشاهده اطلاعات روی محصول کلیک کنید:",
                                    reply_markup=InlineKeyboardMarkup(kb))


async def handle_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    link = f"https://t.me/{context.bot.username}?start=ref_{user_id}"
    count = len([l for l in load_lines(REFERRALS_FILE) if l.split("|")[0] == str(user_id)])
    text = (
        f"✨ دریافت سرویس‌های ایمن و پر سرعت بدون معطلی از طریق ربات\n\n"
        f"🔗 برای معرفی ربات به دوستان خود می‌توانید پیام بالا را برای دوستان خود ارسال کنید.\n\n"
        f"🎁 در صورت معرفی دوستان خود با لینک بالا، با هر خرید موفق هر کدام از آن‌ها 5% به عنوان هدیه به کیف پول شما واریز می‌گردد.\n\n"
        f"📊 **آمار دعوت شما**\n"
        f"• افراد وارد شده با لینک: {count}\n\n"
        f"👇 **لینک اختصاصی دعوت شما:**\n`{link}`"
    )
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]]))


async def handle_test_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) in TEST_USED:
        await update.message.reply_text("❌ شما قبلاً اکانت تست گرفته‌اید. هر کاربر فقط یک بار می‌تواند تست بگیرد.")
        return
    await update.message.reply_text("⏳ در حال ساخت اکانت تست...")
    username = f"test_{user_id}_{int(datetime.now().timestamp()) % 100000}"
    success, result = await panel_create_user(username, gb=0.1, days=1)
    if success:
        TEST_USED.add(str(user_id))
        append_line(TEST_USED_FILE, str(user_id))
        append_line(SERVICES_FILE, f"{user_id}|{username}|اکانت تست|0.1|{result}")
        await send_service_to_user(context.bot, user_id, username, result, 1, 0.1)
    else:
        await update.message.reply_text(
            f"❌ متاسفانه مشکلی پیش اومد. چند دقیقه دیگه دوباره امتحان کن یا با پشتیبانی تماس بگیر: {SUPPORT}")
        logger.error(f"خطای ساخت تست: {result}")
        await context.bot.send_message(ADMIN_ID, f"⚠️ خطا در ساخت تست برای {user_id}:\n{result}")


FAQ_TEXT = (
    "❓ **سوالات متداول:**\n\n"
    "1️⃣ **فیلترشکن شما آیپی ثابته؟ میتونم برای صرافی های ارز دیجیتال استفاده کنم؟**\n"
    "✅ به دلیل وضعیت نت و محدودیت های کشور سرویس ما مناسب ترید نیست و فقط لوکیشن‌ ثابته.\n\n"
    "2️⃣ **اگه قبل از منقضی شدن اکانت، تمدید کنم روزها و حجم باقی مانده می سوزد؟**\n"
    "✅ خیر، بسته تمدید شده برای شما رزرو می گردد و به محض پایان حجم یا زمان باقیمانده جایگزین می گردد.\n\n"
    "3️⃣ **سرویسها چند کاربره هستند؟**\n"
    "✅ تمامی سرویسهای ما سه کاربره هستند و در صورت سوء استفاده و پخش شدن لینک توسط سیستم تشخیص داده شده و مسدود میگردد.\n\n"
    "4️⃣ **فیلترشکن شما از چه نوعی هست؟**\n"
    "✅ فیلترشکن های ما v2ray است و پروتکل‌های مختلفی را ساپورت میکنیم تا با سخت تر شدن حلقه فیلترینگ بتوانیم بهترین سرویس ممکن را ارائه دهیم.\n\n"
    "5️⃣ **چطور باید از این فیلترشکن استفاده کنم؟**\n"
    "✅ برای آموزش نصب و استفاده از برنامه، روی دکمه «💡 آموزش استفاده» بات بزنید.\n\n"
    "6️⃣ **فیلترشکن وصل نمیشه، چیکار کنم؟**\n"
    "✅ ابتدا سرویس خود را بروزرسانی کنید تا آخرین سرورها را دریافت نمایید. در صورتی که با همه سرورها تست کردید و همه قطع بودند با پشتیبانی تماس بگیرید.\n\n"
    "7️⃣ **فیلترشکن شما تضمینی هست که همیشه مواقع متصل بشه؟**\n"
    "✅ به دلیل تغییرات روزانه وضعیت اینترنت کشور، امکان دادن تضمین نیست. ولی ما تمام تلاشمان را برای ارائه جدیدترین روشهای دور زدن فیلترینگ انجام می دهیم.\n\n"
    "8️⃣ **امکان بازگشت وجه دارید؟**\n"
    "✅ امکان بازگشت وجه در صورت حل نشدن مشکل از سمت ما وجود دارد.\n\n"
    "📬 در صورتی که نتوانستید پاسخ سوالات و مشکلات خود را در بخش بالا پیدا کنید، با دکمه زیر به پشتیبانی پیام بدید."
)

HELP_TEXTS = {
    "android": "📱 **آموزش استفاده از V2rayNG (اندروید):**\n\n1. اپلیکیشن V2rayNG را دانلود و نصب کنید.\n2. لینک اشتراک خود را از ربات کپی کنید.\n3. وارد برنامه شده، روی دکمه + بزنید و گزینه Import config from clipboard را انتخاب کنید.\n4. روی دکمه اتصال بزنید.",
    "ios": "🍏 **آموزش استفاده از Happ و Streisand (آیفون):**\n\n1. برنامه Streisand یا Happ را از اپ‌استور دانلود کنید.\n2. لینک اشتراک را کپی کرده و داخل برنامه وارد نمایید.",
    "windows": "💻 **آموزش استفاده از v2rayN (ویندوز):**\n\n1. نرم‌افزار را دانلود کنید.\n2. لینک اشتراک را در برنامه ایمپورت کرده و متصل شوید.",
}


async def handle_admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("فقط ادمین می‌تونه این کارو بکنه.", show_alert=True)
        return

    action, oid = data.split("_", 1)
    orders = load_json(ORDERS_FILE)
    order = orders.get(oid)
    if not order or order.get("status") != "pending":
        await query.answer("این سفارش قبلاً بررسی شده.", show_alert=True)
        return

    caption = query.message.caption or ""
    user_id = order["user_id"]

    if action == "reject":
        set_order_status(oid, "rejected")
        await query.answer("رد شد")
        await query.edit_message_caption(caption=caption + "\n\n❌ رد شد.")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ متاسفانه رسید شما تایید نشد. لطفاً با پشتیبانی تماس بگیرید: {SUPPORT}")
        return

    # ---- تایید ----
    if order["kind"] == "credit":
        set_order_status(oid, "done")
        add_balance(user_id, order["amount"])
        await query.answer("تایید شد")
        await query.edit_message_caption(caption=caption + "\n\n✅ تایید شد و موجودی اضافه شد.")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ پرداخت شما تایید شد و {fmt(order['amount'])} تومان به موجودی حساب شما اضافه شد.\n"
                 f"💰 موجودی فعلی: {fmt(get_balance(user_id))} تومان")
        return

    await query.answer("⏳ در حال ساخت سرویس در پنل...")
    title, days = DURATIONS[order["dk"]]
    gb = VOLUMES[order["vk"]][0]
    username = order["username"]
    success, result = await panel_create_user(username, gb=gb, days=days)

    if not success:
        # سفارش pending می‌ماند و دکمه‌ها فعال هستند تا بعد از رفع مشکل دوباره بزنی
        await context.bot.send_message(
            ADMIN_ID, f"⚠️ ساخت سرویس برای سفارش {username} انجام نشد:\n{result}\n\n"
                      f"بعد از رفع مشکل دوباره روی ✅ همان رسید بزن.")
        return

    set_order_status(oid, "done")
    append_line(SERVICES_FILE, f"{user_id}|{username}|{title}|{gb}|{result}")
    await query.edit_message_caption(caption=caption + "\n\n✅ تایید شد و سرویس ساخته و ارسال شد.")
    await context.bot.send_message(chat_id=user_id, text="✅ پرداخت شما تایید شد.")
    await send_service_to_user(context.bot, user_id, username, result, days, gb)


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    # تایید/رد ادمین (قبل از answer عمومی)
    if data.startswith("approve_") or data.startswith("reject_"):
        await handle_admin_decision(update, context, data)
        return

    if data == "check_join":
        if await is_member(context.bot, user_id):
            await query.answer()
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=user_id, text=WELCOME, reply_markup=main_keyboard())
        else:
            await query.answer("هنوز عضو نشدی! اول عضو کانال شو.", show_alert=True)
        return

    if data == "send_receipt":
        if not context.user_data.get("pending"):
            await query.answer("سفارش منقضی شد. دوباره از منو شروع کن.", show_alert=True)
            return
        await query.answer()
        context.user_data["state"] = "waiting_receipt"
        await context.bot.send_message(
            chat_id=user_id,
            text="👇 لطفا تصویر رسید پرداخت خود را ارسال نمایید:",
            reply_markup=cancel_receipt_keyboard())
        return

    await query.answer()

    if data == "none":
        return

    if data == "menu":
        clear_flow(context)
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=user_id, text=WELCOME, reply_markup=main_keyboard())
        return

    if data == "faq":
        await query.edit_message_text(
            FAQ_TEXT, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 ارسال پیام به پشتیبانی", url=f"https://t.me/{SUPPORT.lstrip('@')}")],
                [InlineKeyboardButton("🔙 بازگشت به پشتیبانی", callback_data="back_to_support")]]))
        return

    if data == "back_to_support":
        await query.edit_message_text(
            "لطفا در قالب یک پیام درخواست و نکات قابل ملاحظه درباره خود را ارسال کرده و سپس تا تماس از طرف ادمین منتظر بمانید",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 ارسال پیام به پشتیبانی", url=f"https://t.me/{SUPPORT.lstrip('@')}")],
                [InlineKeyboardButton("❓ سوالات متداول", callback_data="faq")],
                [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]]))
        return

    if data == "add_credit":
        amts = [(1000000, 750000), (500000, 250000), (200000, 150000), (125000, 100000), (75000, 50000)]
        rows = [[InlineKeyboardButton(f"{fmt(a)} تومان", callback_data=f"pay_amt_{a}"),
                 InlineKeyboardButton(f"{fmt(b)} تومان", callback_data=f"pay_amt_{b}")] for a, b in amts]
        rows.append([InlineKeyboardButton("✍️ رقم دلخواه", callback_data="pay_amt_custom")])
        rows.append([InlineKeyboardButton("❌ انصراف", callback_data="menu")])
        await query.edit_message_text("💰 مبلغ مورد نظر را برای شارژ کیف پول انتخاب کنید:",
                                      reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("pay_amt_"):
        key = data[8:]
        if key == "custom":
            context.user_data["state"] = "waiting_custom_credit"
            await query.edit_message_text(
                "💸 مبلغ را به تومان وارد کنید (حداقل 10,000):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="add_credit")]]))
            return
        try:
            amount = int(key)
        except ValueError:
            amount = 50000
        context.user_data["pending"] = {"kind": "credit", "amount": amount}
        await query.edit_message_text(pay_text(amount), parse_mode="Markdown",
                                      reply_markup=pay_markup(amount))
        return

    if data == "gift_code":
        context.user_data["state"] = "waiting_gift_code"
        await query.message.reply_text(
            "🎁 کد هدیه خود را وارد کنید:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="menu")]]))
        return

    if data == "apply_discount":
        await query.answer("❌ کد هدیه یافت نشد.", show_alert=True)
        return

    if data.startswith("serv_"):
        uname = data[5:]
        s = find_service(uname)
        if not s:
            await query.answer("سرویس مورد نظر پیدا نشد!", show_alert=True)
            return
        text, markup = service_view(uname, s[3], s[2], s[4], True)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        return

    if data.startswith("stop_") or data.startswith("active_"):
        make_active = data.startswith("active_")
        uname = data.split("_", 1)[1]
        s = find_service(uname)
        if not s:
            await query.answer("سرویس پیدا نشد!", show_alert=True)
            return
        if await panel_change_status(uname, "active" if make_active else "disabled"):
            text, markup = service_view(uname, s[3], s[2], s[4], make_active)
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
            await query.answer("سرویس فعال شد!" if make_active
                               else "امکان اتصال به این اشتراک به طور موقت غیر فعال شد!", show_alert=True)
        else:
            await query.answer("❌ خطا در تغییر وضعیت سرویس.", show_alert=True)
        return

    if data.startswith("reset_confirm_"):
        uname = data[14:]
        await query.edit_message_text(
            "⚠️ هشدار: درصورت تایید، فیلترشکن فعلی شما بلافاصله از کار می افتد و برای استفاده از باقیمانده سرویس خود باید لینک جدید که در ادامه ارسال می گردد را از نو وارد نرم افزار کنید. آیا مطمئن هستید؟",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ تایید", callback_data=f"reset_do_{uname}"),
                InlineKeyboardButton("❌ انصراف", callback_data=f"serv_{uname}")]]))
        return

    if data.startswith("reset_do_"):
        uname = data[9:]
        new_sub = await panel_reset_sub(uname)
        if not new_sub:
            await query.answer("❌ خطا در دریافت لینک جدید از پنل.", show_alert=True)
            return
        new_lines = []
        for line in load_lines(SERVICES_FILE):
            p = line.split("|")
            if len(p) >= 5 and p[1] == uname:
                p[4] = new_sub
            new_lines.append("|".join(p))
        with open(SERVICES_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=user_id,
                                       text=f"✅ لینک جدید شما با موفقیت ساخته شد:\n\n`{new_sub}`",
                                       parse_mode="Markdown")
        await context.bot.send_photo(chat_id=user_id,
                                     photo=InputFile(generate_qr_buffer(new_sub), filename="new_qr.png"),
                                     caption=f"📥 QR Code لینک جدید برای کاربر: `{uname}`",
                                     parse_mode="Markdown")
        return

    if data == "back_to_services":
        mine = [l for l in load_lines(SERVICES_FILE) if l.split("|")[0] == str(user_id)]
        kb = [[InlineKeyboardButton(f"🟢 {l.split('|')[1]}", callback_data=f"serv_{l.split('|')[1]}")] for l in mine]
        kb.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")])
        await query.edit_message_text("🔹 برای مشاهده اطلاعات روی محصول کلیک کنید:",
                                      reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("qr_"):
        uname = data[3:]
        s = find_service(uname)
        if s:
            await query.message.reply_photo(
                photo=InputFile(generate_qr_buffer(s[4]), filename="qr.png"),
                caption=f"📥 QR Code مربوط به نام کاربری: `{uname}`", parse_mode="Markdown")
        return

    if data.startswith("hlp_"):
        text = HELP_TEXTS.get(data[4:], "آموزش یافت نشد.")
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به آموزش‌ها", callback_data="back_to_help")]])
        if query.message.photo:
            await query.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
        else:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        return

    if data == "back_to_help":
        await query.edit_message_text("لطفا یک گزینه را انتخاب نمایید:", reply_markup=help_menu())
        return

    if data == "new":
        context.user_data.pop("pending", None)
        await query.edit_message_text("⏳ انتخاب مدت زمان:", reply_markup=duration_menu())
        return

    if data.startswith("dur_"):
        dk = data[4:]
        await query.edit_message_text(
            f"مدت انتخابی: {DURATIONS[dk][0]}\n\n📦 حالا حجم رو انتخاب کن:",
            reply_markup=volume_menu(dk))
        return

    if data.startswith("vol_"):
        _, dk, vk = data.split("_", 2)
        context.user_data["order"] = {"dk": dk, "vk": vk}
        context.user_data["state"] = "waiting_username"
        await query.edit_message_text(
            "✍️ سرویس مورد نظر انتخاب شد. لطفا یک نام کاربری با حروف لاتین به طول حداکثر 20 کاراکتر وارد نمایید.")
        return

    if data.startswith("pay_"):
        _, dk, vk = data.split("_", 2)
        order = context.user_data.get("order")
        if not order or not order.get("username"):
            await query.edit_message_text("نشست منقضی شد. دوباره از منو شروع کن.")
            return
        amount = VOLUMES[vk][1][dk]
        context.user_data["pending"] = {"kind": "service", "dk": dk, "vk": vk,
                                        "username": order["username"], "amount": amount}
        await query.edit_message_text(pay_text(amount), parse_mode="Markdown",
                                      reply_markup=pay_markup(amount))
        return


async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    pending = context.user_data.get("pending")

    if context.user_data.get("state") != "waiting_receipt" or not pending:
        await update.message.reply_text(
            "لطفاً اول از منو سرویس مورد نظرتون رو انتخاب کنید و بعد از پرداخت، دکمه «ارسال رسید پرداخت» رو بزنید.",
            reply_markup=main_keyboard())
        return

    photo = update.message.photo[-1].file_id
    order = {"user_id": user.id, "kind": pending["kind"], "amount": pending["amount"]}
    if pending["kind"] == "service":
        order.update({"dk": pending["dk"], "vk": pending["vk"], "username": pending["username"]})
    oid = create_order(order)

    if pending["kind"] == "credit":
        caption = (f"🧾 رسید شارژ حساب جدید\n\n"
                   f"👤 کاربر: {user.full_name} (@{user.username})\n"
                   f"🆔 آیدی: {user.id}\n"
                   f"💰 مبلغ شارژ: {fmt(pending['amount'])} تومان")
    else:
        title, days = DURATIONS[pending["dk"]]
        gb = VOLUMES[pending["vk"]][0]
        caption = (f"🧾 رسید جدید دریافت شد\n\n"
                   f"👤 کاربر: {user.full_name} (@{user.username})\n"
                   f"🆔 آیدی: {user.id}\n"
                   f"👤 نام کاربری: {pending['username']}\n"
                   f"⏳ مدت: {title}\n"
                   f"📦 حجم: {gb} گیگ\n"
                   f"💰 مبلغ: {fmt(pending['amount'])} تومان")

    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo, caption=caption,
                                 reply_markup=admin_keyboard(oid))
    clear_flow(context)
    await update.message.reply_text(
        "✅ رسید پرداخت شما دریافت شد. پس از بررسی توسط ادمین به درخواست شما رسیدگی می گردد. لطفا از ارسال مجدد رسید خودداری نمایید.",
        reply_markup=main_keyboard())


# ============== سرور سلامت (اختیاری) ==============
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - bot is running")

    def log_message(self, format, *args):
        pass


def run_health_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        logger.info(f"سرور سلامت روی پورت {PORT} فعال شد")
        server.serve_forever()
    except OSError as e:
        logger.warning(f"سرور سلامت اجرا نشد: {e}")


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("خطا:", exc_info=context.error)


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
    app.add_error_handler(on_error)

    logger.info("بات در حال اجراست...")
    app.run_polling()


if __name__ == "__main__":
    main()
