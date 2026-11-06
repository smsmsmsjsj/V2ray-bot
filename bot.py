import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone

import httpx
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup)
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ============== تنظیمات (از Environment Variables خونده می‌شه) ==============
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

# ============== تعرفه‌ها (حجم برای کل مدت، نه ماهانه) ==============
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

# ============== متن‌ها ==============
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
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu")])
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


# ============== بررسی عضویت اجباری کانال ==============
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
                return True, sub_url or "کاربر ساخته شد ولی لینک برنگشت. از پنل چک کن."
            else:
                logger.error(f"خطای پنل: {resp.status_code} - {resp.text}")
                return False, f"خطای پنل ({resp.status_code}): {resp.text[:300]}"
    except Exception as e:
        logger.exception("خطا در اتصال به پنل")
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
                logger.warning(f"گروه '{PANEL_GROUP_NAME}' پیدا نشد؛ لیست گروه‌ها: {items}")
    except Exception as e:
        logger.warning(f"خطا در گرفتن گروه‌ها: {e}")

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

    # بررسی لینک دعوت (ref_<id>)
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
        await update.message.reply_text(
            f"💰 اعتبار حساب شما: 0 تومان\n\n"
            f"جهت افزایش اعتبار یا خرید سرویس جدید از منوی اصلی اقدام کنید.",
            reply_markup=main_keyboard()
        )
        return

    if text == BTN_HELP:
        await update.message.reply_text(
            "💡 آموزش استفاده از سرویس‌ها:\n\n"
            "• اندروید: اپلیکیشن v2rayNG\n"
            "• آیفون: اپلیکیشن FoXray یا Napstermark\n"
            "• ویندوز: اپلیکیشن v2rayN\n\n"
            f"پس از خرید، لینک اشتراک خود را کپی کرده و در نرم‌افزار وارد کنید. در صورت نیاز به راهنمایی بیشتر با {SUPPORT} در ارتباط باشید.",
            reply_markup=main_keyboard()
        )
        return

    if text == BTN_SUPPORT:
        await update.message.reply_text(f"برای ارتباط با پشتیبانی پیام بده: {SUPPORT}")
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

    text = "📲 سرویس‌های شما:\n\n"
    for i, line in enumerate(my_services, 1):
        parts = line.split("|")
        # ساختار: user_id|username|title|gb|sub_link
        _, uname, title, gb, sub_link = parts[0], parts[1], parts[2], parts[3], parts[4]
        text += f"{i}. {title} | {gb} گیگ\n🔗 {sub_link}\n\n"

    await update.message.reply_text(text, reply_markup=main_keyboard())


async def handle_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"

    referrals = load_lines(REFERRALS_FILE)
    count = sum(1 for line in referrals if line.split("|")[0] == str(user_id))

    await update.message.reply_text(
        f"👥 لینک دعوت اختصاصی شما:\n\n{link}\n\n"
        f"تعداد دوستان دعوت‌شده: {count} نفر\n\n"
        f"این لینک را برای دوستانتان ارسال کنید.",
        reply_markup=main_keyboard())


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

    await update.message.reply_text(
        "🤝 درخواست نمایندگی شما ثبت شد. به‌زودی ادمین با شما تماس می‌گیرد.",
        reply_markup=main_keyboard())


async def handle_test_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) in TEST_USED:
        await update.message.reply_text("❌ شما قبلاً اکانت تست گرفته‌اید. هر کاربر فقط یک بار می‌تواند تست بگیرد.")
        return

    await update.message.reply_text("⏳ در حال ساخت اکانت تست (۱۰۰ مگ، ۱ روزه)...")
    username = f"test_{user_id}_{int(datetime.now().timestamp())}"
    success, result = await panel_create_user(username, gb=0.1, days=1)

    if success:
        TEST_USED.add(str(user_id))
        append_line(TEST_USED_FILE, str(user_id))
        append_line(SERVICES_FILE, f"{user_id}|{username}|اکانت تست|0.1|{result}")
        await update.message.reply_text(
            f"✅ اکانت تست شما ساخته شد!\n\n🔗 لینک اشتراک:\n{result}\n\n"
            f"این اکانت ۱۰۰ مگابایت حجم و ۱ روز اعتبار دارد.")
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
        await query.edit_message_text(WELCOME)
        await context.bot.send_message(chat_id=query.from_user.id, text="منوی اصلی:", reply_markup=main_keyboard())
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
        await context.bot.send_message(
            chat_id=user_id,
            text=(f"🎉 پرداخت شما تایید شد!\n\n"
                 f"⏳ مدت: {title}\n"
                 f"📦 حجم: {gb} گیگ\n\n"
                 f"🔗 لینک اشتراک شما:\n{result}"))
    else:
        await query.edit_message_caption(caption=query.message.caption + f"\n\n❌ خطا در ساخت کاربر:\n{result}")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"پرداخت شما تایید شد ولی در ساخت سرویس مشکلی پیش اومد. پشتیبانی به‌زودی بهتون سرویس رو می‌ده: {SUPPORT}")


# ============== سرور کوچیک برای زنده نگه داشتن سرویس (UptimeRobot) ==============
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - bot is running")

    def log_message(self, format, *args):
        pass  # لاگ‌های اضافه رو خاموش می‌کنیم


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
