import os
import logging
from datetime import datetime, timedelta, timezone

import httpx
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup)
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ============== تنظیمات (از فایل .env خونده می‌شه) ==============
TOKEN = os.environ.get("BOT_TOKEN", "")
SUPPORT = os.environ.get("SUPPORT_USERNAME", "@Dudgeuvsuvduv")

CARD_NUMBER = os.environ.get("CARD_NUMBER", "5041-7210-4718-7167")
CARD_OWNER = os.environ.get("CARD_OWNER", "افروز خادمی")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "5821799233"))

CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "@speedv2raymaeket")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@speedv2raymaeket")  # برای getChatMember

PANEL_URL = os.environ.get("PANEL_URL", "https://www.speedur.org:2096").rstrip("/")
PANEL_API_KEY = os.environ.get("PANEL_API_KEY", "")
PANEL_GROUP_NAME = os.environ.get("PANEL_GROUP_ID", "group1")  # اسم گروه تو پنل

# ============== تعرفه‌ها (حجم برای کل مدت، نه ماهانه) ==============
# مدت‌ها: کلید -> (عنوان فارسی, تعداد روز)
DURATIONS = {
    "d1": ("1 ماهه", 30),
    "d2": ("2 ماهه", 60),
    "d3": ("3 ماهه", 90),
    "d5": ("5 ماهه", 150),
    "d12": ("1 ساله", 365),
}

# حجم‌ها: کلید -> (گیگابایت, {کلید مدت: قیمت تومان})
VOLUMES = {
    "v10":  (10,  {"d1": 45000,  "d2": 85000,  "d3": 120000, "d5": 190000, "d12": 420000}),
    "v20":  (20,  {"d1": 75000,  "d2": 140000, "d3": 200000, "d5": 310000, "d12": 680000}),
    "v30":  (30,  {"d1": 100000, "d2": 190000, "d3": 270000, "d5": 420000, "d12": 900000}),
    "v50":  (50,  {"d1": 150000, "d2": 280000, "d3": 400000, "d5": 620000, "d12": 1300000}),
    "v70":  (70,  {"d1": 195000, "d2": 370000, "d3": 520000, "d5": 800000, "d12": 1650000}),
    "v100": (100, {"d1": 260000, "d2": 490000, "d3": 690000, "d5": 1050000, "d12": 2150000}),
    "v150": (150, {"d1": 370000, "d2": 700000, "d3": 980000, "d5": 1480000, "d12": 2950000}),
    "v200": (200, {"d1": 470000, "d2": 890000, "d3": 1250000, "d5": 1880000, "d12": 3700000}),
}

# توجه: قیمت‌های بالا نمونه هستن. برای ویرایش فقط اعداد همین دیکشنری VOLUMES رو عوض کن.

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

SOON = {BTN_RENEW, BTN_MY, BTN_HELP, BTN_CREDIT, BTN_INVITE, BTN_AGENT}

# کاربرهایی که تست گرفتن (برای جلوگیری از تکرار) - در حافظه؛ بعد از ری‌استارت بات پاک می‌شه
# برای دائمی بودن باید تو فایل یا دیتابیس ذخیره بشه (پایین توضیح دادم)
TEST_USED_FILE = "test_used.txt"


def load_test_used():
    try:
        with open(TEST_USED_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()


def save_test_used(user_id):
    with open(TEST_USED_FILE, "a") as f:
        f.write(f"{user_id}\n")


TEST_USED = load_test_used()


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
    """اگه عضو نبود پیام عضویت می‌فرسته و True برمی‌گردونه (یعنی باید متوقف بشه)"""
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
    """
    کاربر جدید تو پنل پاسارگاد می‌سازه.
    gb: حجم به گیگابایت (برای کل مدت، نه ماهانه)
    days: تعداد روز اعتبار
    برمی‌گردونه: (success: bool, subscription_url یا پیام خطا: str)
    """
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
    """آیدی گروه رو بر اساس اسمش از پنل می‌گیره و کش می‌کنه."""
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

    # اگه نشد پیدا کنه، اگه PANEL_GROUP_ID یه عدد باشه مستقیم همونو برمی‌گردونه
    try:
        gid = int(PANEL_GROUP_NAME)
        _GROUP_CACHE["ids"] = [gid]
        return _GROUP_CACHE["ids"]
    except ValueError:
        pass

    _GROUP_CACHE["ids"] = [1]  # fallback پیش‌فرض
    return _GROUP_CACHE["ids"]


# ============== هندلرها ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("order", None)
    user_id = update.effective_user.id
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

    if text == BTN_NEW:
        await update.message.reply_text("⏳ انتخاب مدت زمان:", reply_markup=duration_menu())
        return

    if text == BTN_TEST:
        await handle_test_request(update, context)
        return

    if text == BTN_SUPPORT:
        await update.message.reply_text(f"برای ارتباط با پشتیبانی پیام بده: {SUPPORT}")
        return

    if text in SOON:
        await update.message.reply_text("🚧 این بخش به‌زودی فعال می‌شه.")
        return

    await update.message.reply_text("یکی از گزینه‌های منو رو انتخاب کن 👇", reply_markup=main_keyboard())


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
        save_test_used(user_id)
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
    """وقتی مشتری عکس رسید می‌فرسته"""
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

    # تایید شد -> ساخت کاربر در پنل
    await query.edit_message_caption(caption=query.message.caption + "\n\n⏳ در حال ساخت کاربر در پنل...")
    username = f"user_{user_id}_{int(datetime.now().timestamp())}"
    success, result = await panel_create_user(username, gb=gb, days=days)

    if success:
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


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN تو فایل .env تنظیم نشده!")
    if not PANEL_API_KEY:
        logger.warning("PANEL_API_KEY تنظیم نشده - ساخت کاربر در پنل کار نخواهد کرد.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.PHOTO, receipt_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logger.info("بات در حال اجراست...")
    app.run_polling()


if __name__ == "__main__":
    main()
