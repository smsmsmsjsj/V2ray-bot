# requirements: python-telegram-bot==22.* httpx qrcode[pil]
# فایل .env اختیاری کنار bot.py (KEY=VALUE)
# الزامی: BOT_TOKEN ADMIN_ID CARD_NUMBER SUPPORT_USERNAME CHANNEL_USERNAME PANEL_URL PANEL_API_KEY
# اختیاری: CARD_OWNER PANEL_GROUP_ID BRAND_NAME BRAND_TITLE DATA_DIR PORT SMS_SECRET SMS_SENDERS ORDER_TTL_MIN
import asyncio
import csv
import hashlib
import html
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import urlparse, parse_qs, unquote_plus

import httpx
import qrcode
from telegram import (Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup,
                      ReplyKeyboardMarkup, InputFile, CopyTextButton)
from telegram.constants import ParseMode
from telegram.error import RetryAfter, NetworkError, BadRequest, Conflict
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, PicklePersistence, filters, ContextTypes)


# ============================== .env ==============================
def load_dotenv():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def env_required(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"متغیر محیطی {name} تنظیم نشده است!")
    return v


TOKEN = env_required("BOT_TOKEN")
ADMIN_ID = int(env_required("ADMIN_ID"))
CARD_NUMBER = env_required("CARD_NUMBER")
CARD_OWNER = os.environ.get("CARD_OWNER", "").strip()
SUPPORT = env_required("SUPPORT_USERNAME")
CHANNEL = env_required("CHANNEL_USERNAME")
PANEL_URL = env_required("PANEL_URL").rstrip("/")
PANEL_API_KEY = env_required("PANEL_API_KEY")
PANEL_GROUP_NAME = os.environ.get("PANEL_GROUP_ID", "group1")
BRAND = os.environ.get("BRAND_NAME", "SpeedUR")
BRAND_TITLE = os.environ.get("BRAND_TITLE", "v2ray market")
DATA_DIR = os.environ.get("DATA_DIR", ".")
PORT = os.environ.get("PORT")
SMS_SECRET = os.environ.get("SMS_SECRET", "").strip()
SMS_SENDERS = [s.strip() for s in os.environ.get("SMS_SENDERS", "").split(",") if s.strip()]

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot.db")
PERSIST_PATH = os.path.join(DATA_DIR, "bot_state.pickle")

TZ = timezone(timedelta(hours=3, minutes=30))
ORDER_TTL = int(os.environ.get("ORDER_TTL_MIN", "180")) * 60
LATE_WINDOW = 48 * 3600
REMIND_DAYS = 3
REMIND_USAGE = 0.9
TOPUP_BONUS_MIN = 200_000
MAX_WALLET_TOPUP = 50_000_000

DEFAULT_SETTINGS = {
    "cashback_percent": "3",
    "first_buy_percent": "0",
    "referral_percent": "5",
    "topup_bonus_percent": "5",
    "daily_bonus": "300",
    "test_enabled": "1",
    "test_daily_cap": "100",
    "notify_new": "0",
}
SETTING_LIMITS = {"cashback_percent": (0, 50), "first_buy_percent": (0, 50), "referral_percent": (0, 50),
                  "topup_bonus_percent": (0, 50), "daily_bonus": (0, 100000), "test_enabled": (0, 1),
                  "test_daily_cap": (0, 10000), "notify_new": (0, 1)}

# (حداقل خرید، نام سطح، درصد تخفیف) — فعلاً سطح‌ها فقط نشان هستند و تخفیف ندارند
LEVELS = [(0, "🥉 برنز", 0), (500_000, "🥈 نقره‌ای", 0), (2_000_000, "🥇 طلایی", 0), (5_000_000, "💎 الماسی", 0)]

# ============================== تعرفه ==============================
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

# ============================ متن‌ها و دکمه‌ها ============================
BTN_NEW = "🚀 خرید سرویس جدید"
BTN_TEST = "🧪 اکانت تست"
BTN_RENEW = "♻️ تمدید سرویس"
BTN_MY = "📲 سرویس های من"
BTN_ORDERS = "🧾 سفارش‌های من"
BTN_PRICES = "💎 تعرفه‌ها"
BTN_DAILY = "🎁 هدیه روزانه"
BTN_CREDIT = "💰 اعتبار من"
BTN_INVITE = "👥 معرفی به دوستان"
BTN_HELP = "💡 آموزش استفاده"
BTN_SUPPORT = "🧑‍💻 تماس با پشتیبانی"
BTN_AGENT = "🤝 درخواست نمایندگی"
BTN_CANCEL_RECEIPT = "❌ لغو ارسال رسید و بازگشت"

MENU_BUTTONS = {BTN_NEW, BTN_TEST, BTN_RENEW, BTN_MY, BTN_ORDERS, BTN_PRICES, BTN_DAILY, BTN_CREDIT,
                BTN_INVITE, BTN_HELP, BTN_SUPPORT, BTN_AGENT, BTN_CANCEL_RECEIPT}

SUPPORT_TEXT = ("لطفا در قالب یک پیام درخواست و نکات قابل ملاحظه درباره خود را ارسال کرده "
                "و سپس تا تماس از طرف ادمین منتظر بمانید\n\n"
                "🎫 با «ثبت تیکت» پیام شما مستقیم به مدیریت می‌رسد و پاسخ را همین‌جا در ربات می‌گیرید.")

FAQ_TEXT = (
    "❓ سوالات متداول:\n\n"
    "1️⃣ فیلترشکن شما آیپی ثابته؟ میتونم برای صرافی های ارز دیجیتال استفاده کنم؟\n"
    "✅ به دلیل وضعیت نت و محدودیت های کشور سرویس ما مناسب ترید نیست و فقط لوکیشن‌ ثابته.\n\n"
    "2️⃣ اگه قبل از منقضی شدن اکانت، تمدید کنم روزها و حجم باقی مانده می سوزد؟\n"
    "✅ خیر، اگر سرویس هنوز فعال باشد، مدت و حجم جدید به باقیمانده اضافه می‌شود.\n\n"
    "3️⃣ سرویسها چند کاربره هستند؟\n"
    "✅ تمامی سرویسهای ما سه کاربره هستند و در صورت سوء استفاده و پخش شدن لینک توسط سیستم تشخیص داده شده و مسدود میگردد.\n\n"
    "4️⃣ فیلترشکن شما از چه نوعی هست؟\n"
    "✅ فیلترشکن های ما v2ray است و پروتکل‌های مختلفی را ساپورت میکنیم تا با سخت تر شدن حلقه فیلترینگ بتوانیم بهترین سرویس ممکن را ارائه دهیم.\n\n"
    "5️⃣ چطور باید از این فیلترشکن استفاده کنم؟\n"
    "✅ برای آموزش نصب و استفاده از برنامه، روی دکمه «💡 آموزش استفاده» بات بزنید.\n\n"
    "6️⃣ فیلترشکن وصل نمیشه، چیکار کنم؟\n"
    "✅ ابتدا سرویس خود را بروزرسانی کنید تا آخرین سرورها را دریافت نمایید. در صورتی که با همه سرورها تست کردید و همه قطع بودند با پشتیبانی تماس بگیرید.\n\n"
    "7️⃣ فیلترشکن شما تضمینی هست که همیشه مواقع متصل بشه؟\n"
    "✅ به دلیل تغییرات روزانه وضعیت اینترنت کشور، امکان دادن تضمین نیست. ولی ما تمام تلاشمان را برای ارائه جدیدترین روشهای دور زدن فیلترینگ انجام می دهیم.\n\n"
    "8️⃣ امکان بازگشت وجه دارید؟\n"
    "✅ امکان بازگشت وجه در صورت حل نشدن مشکل از سمت ما وجود دارد.\n\n"
    "📬 در صورتی که نتوانستید پاسخ سوالات و مشکلات خود را در بخش بالا پیدا کنید، با دکمه زیر به پشتیبانی پیام بدید."
)

HELP_TEXTS = {
    "android": "📱 آموزش استفاده از V2rayNG (اندروید):\n\n1. اپلیکیشن V2rayNG را دانلود و نصب کنید.\n2. لینک اشتراک خود را از ربات کپی کنید.\n3. وارد برنامه شده، روی دکمه + بزنید و گزینه Import config from clipboard را انتخاب کنید.\n4. روی دکمه اتصال بزنید.",
    "ios": "🍏 آموزش استفاده از Happ و Streisand (آیفون):\n\n1. برنامه Streisand یا Happ را از اپ‌استور دانلود کنید.\n2. لینک اشتراک را کپی کرده و داخل برنامه وارد نمایید.",
    "windows": "💻 آموزش استفاده از v2rayN (ویندوز):\n\n1. نرم‌افزار را دانلود کنید.\n2. لینک اشتراک را در برنامه ایمپورت کرده و متصل شوید.",
}

STATUS_ICON = {"active": "🟢", "disabled": "🔴", "expired": "⚫️", "limited": "🟠", "on_hold": "🟡"}
STATUS_FA = {"active": "🟢 فعال", "disabled": "🔴 غیرفعال", "expired": "⚫️ منقضی",
             "limited": "🟠 اتمام حجم", "on_hold": "🟡 در انتظار"}
ORDER_STATUS_FA = {"awaiting_receipt": "منتظر پرداخت", "pending": "در انتظار بررسی",
                   "processing": "در حال انجام", "done": "✅ انجام شد", "rejected": "❌ رد شد",
                   "cancelled": "لغو شد", "expired": "منقضی", "failed": "ناموفق"}
KIND_FA = {"credit": "شارژ کیف پول", "new": "خرید سرویس جدید", "renew": "تمدید سرویس"}

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")
HTML = ParseMode.HTML
TEST_TITLE = "اکانت تست"
GIFT_TITLE = "هدیه مدیریت"
SEGMENTS = ("all", "buyers", "nobuyers")


# ============================== ابزارها ==============================
def esc(s) -> str:
    return html.escape(str(s), quote=False)


def fmt(n) -> str:
    return f"{int(n):,}"


def now_str() -> str:
    return datetime.now(TZ).strftime("%Y/%m/%d %H:%M:%S")


_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_digits(s: str) -> str:
    return (s or "").translate(_DIGITS)


_LAST = {}


def throttled(user_id, key, seconds) -> bool:
    now = time.time()
    if len(_LAST) > 5000:
        for k in [k for k, v in _LAST.items() if now - v > 3600]:
            _LAST.pop(k, None)
    k = (user_id, key)
    if now - _LAST.get(k, 0) < seconds:
        return True
    _LAST[k] = now
    return False


def generate_qr_buffer(url: str) -> BytesIO:
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio


def progress_bar(used, limit, n=10) -> str:
    if not limit:
        return ""
    ratio = min(max(used / limit, 0), 1)
    f = int(ratio * n)
    return "▓" * f + "░" * (n - f) + f" {int(ratio * 100)}٪"


async def ack(q, text=None, alert=False):
    try:
        await q.answer(text, show_alert=alert)
    except Exception:
        pass


async def safe_edit(q, text, **kw):
    try:
        await q.edit_message_text(text, **kw)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def safe_send(bot, chat_id, text, **kw) -> bool:
    try:
        await bot.send_message(chat_id=chat_id, text=text, **kw)
        return True
    except Exception as e:
        logger.warning(f"ارسال پیام به {chat_id} ناموفق: {e}")
        return False


async def notify_admin(bot, text):
    await safe_send(bot, ADMIN_ID, text[:4000])


# ============================== دیتابیس ==============================
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY, joined_at TEXT NOT NULL, test_used INTEGER NOT NULL DEFAULT 0,
    banned INTEGER NOT NULL DEFAULT 0, notify INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS balances(user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS wallet_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
    reason TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_wlog_user ON wallet_log(user_id);
CREATE TABLE IF NOT EXISTS daily(user_id INTEGER PRIMARY KEY, last_date TEXT, streak INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS services(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT NOT NULL UNIQUE,
    title TEXT, gb REAL, days INTEGER, link TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_services_user ON services(user_id);
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
    amount INTEGER NOT NULL, dk TEXT, vk TEXT, username TEXT, discount_code TEXT,
    method TEXT NOT NULL DEFAULT 'card', status TEXT NOT NULL, receipt_uid TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE TABLE IF NOT EXISTS referrals(referred_id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS discounts(
    code TEXT PRIMARY KEY, percent INTEGER NOT NULL, max_uses INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS discount_users(code TEXT NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY(code, user_id));
CREATE TABLE IF NOT EXISTS gifts(code TEXT PRIMARY KEY, amount INTEGER NOT NULL, used_by INTEGER);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS notified(username TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY(username, kind));
CREATE TABLE IF NOT EXISTS sms_log(hash TEXT PRIMARY KEY, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tickets(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', created_at TEXT);
"""


@contextmanager
def conn(immediate=False):
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        if immediate:
            c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        cols = [r["name"] for r in c.execute("PRAGMA table_info(users)")]
        if "banned" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN banned INTEGER NOT NULL DEFAULT 0")
        if "notify" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN notify INTEGER NOT NULL DEFAULT 1")


# ---- تنظیمات ----
def get_setting(key, default=""):
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if r:
        return r["value"]
    return DEFAULT_SETTINGS.get(key, default)


def set_setting(key, value):
    with conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def sint(key) -> int:
    try:
        return int(get_setting(key, "0"))
    except ValueError:
        return int(DEFAULT_SETTINGS.get(key, "0"))


def card_number() -> str:
    return get_setting("card_number", "") or CARD_NUMBER


def card_owner() -> str:
    return get_setting("card_owner", "") or CARD_OWNER


def card_digits() -> str:
    return "".join(c for c in card_number() if c.isdigit())


# ---- کاربران ----
def add_user(uid) -> bool:
    with conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO users(user_id, joined_at) VALUES(?,?)", (uid, now_str()))
        return cur.rowcount == 1


def get_join_date(uid) -> str:
    with conn() as c:
        r = c.execute("SELECT joined_at FROM users WHERE user_id=?", (uid,)).fetchone()
    return r["joined_at"] if r else "نامشخص"


def all_user_ids(segment="all"):
    base = "SELECT user_id FROM users WHERE banned=0"
    buyers = "SELECT DISTINCT user_id FROM orders WHERE status='done' AND kind IN ('new','renew')"
    if segment == "buyers":
        q = f"{base} AND user_id IN ({buyers})"
    elif segment == "nobuyers":
        q = f"{base} AND user_id NOT IN ({buyers})"
    else:
        q = base
    with conn() as c:
        return [r["user_id"] for r in c.execute(q)]


def is_banned(uid) -> bool:
    with conn() as c:
        r = c.execute("SELECT banned FROM users WHERE user_id=?", (uid,)).fetchone()
    return bool(r and r["banned"])


def set_ban(uid, val: bool):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id, joined_at) VALUES(?,?)", (uid, now_str()))
        c.execute("UPDATE users SET banned=? WHERE user_id=?", (1 if val else 0, uid))


def notify_on(uid) -> bool:
    with conn() as c:
        r = c.execute("SELECT notify FROM users WHERE user_id=?", (uid,)).fetchone()
    return not r or bool(r["notify"])


def toggle_notify(uid) -> bool:
    with conn() as c:
        c.execute("UPDATE users SET notify=1-notify WHERE user_id=?", (uid,))
        r = c.execute("SELECT notify FROM users WHERE user_id=?", (uid,)).fetchone()
    return bool(r and r["notify"])


def claim_test(uid) -> bool:
    with conn() as c:
        cur = c.execute("UPDATE users SET test_used=1 WHERE user_id=? AND test_used=0", (uid,))
        return cur.rowcount == 1


def unclaim_test(uid):
    with conn() as c:
        c.execute("UPDATE users SET test_used=0 WHERE user_id=?", (uid,))


def tests_today() -> int:
    today = datetime.now(TZ).strftime("%Y/%m/%d")
    with conn() as c:
        return c.execute("SELECT COUNT(*) n FROM services WHERE title=? AND created_at LIKE ?",
                         (TEST_TITLE, today + "%")).fetchone()["n"]


# ---- کیف پول ----
def get_balance(uid) -> int:
    with conn() as c:
        r = c.execute("SELECT balance FROM balances WHERE user_id=?", (uid,)).fetchone()
    return int(r["balance"]) if r else 0


def _log_wallet(c, uid, amount, reason):
    c.execute("INSERT INTO wallet_log(user_id, amount, reason, created_at) VALUES(?,?,?,?)",
              (uid, int(amount), reason, now_str()))


def add_balance(uid, amount, reason=""):
    with conn(True) as c:
        c.execute("INSERT INTO balances(user_id, balance) VALUES(?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET balance=balance+excluded.balance", (uid, int(amount)))
        _log_wallet(c, uid, amount, reason)


def try_deduct(uid, amount, reason="") -> bool:
    with conn(True) as c:
        cur = c.execute("UPDATE balances SET balance=balance-? WHERE user_id=? AND balance>=?",
                        (int(amount), uid, int(amount)))
        if cur.rowcount == 1:
            _log_wallet(c, uid, -int(amount), reason)
            return True
        return False


def wallet_history(uid, limit=10):
    with conn() as c:
        return c.execute("SELECT * FROM wallet_log WHERE user_id=? ORDER BY id DESC LIMIT ?",
                         (uid, limit)).fetchall()


def claim_daily(uid):
    base = sint("daily_bonus")
    if base <= 0:
        return "off", 0, 0
    now = datetime.now(TZ)
    today, yesterday = now.strftime("%Y-%m-%d"), (now - timedelta(days=1)).strftime("%Y-%m-%d")
    with conn(True) as c:
        r = c.execute("SELECT last_date, streak FROM daily WHERE user_id=?", (uid,)).fetchone()
        if r and r["last_date"] == today:
            return "already", 0, r["streak"]
        streak = (r["streak"] + 1) if (r and r["last_date"] == yesterday) else 1
        if streak > 7:
            streak = 1
        bonus = base * 5 if streak == 7 else base
        c.execute("INSERT INTO daily(user_id,last_date,streak) VALUES(?,?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET last_date=excluded.last_date, streak=excluded.streak",
                  (uid, today, streak))
        c.execute("INSERT INTO balances(user_id, balance) VALUES(?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET balance=balance+excluded.balance", (uid, bonus))
        _log_wallet(c, uid, bonus, f"هدیه روزانه (روز {streak})")
    return "ok", bonus, streak


# ---- سرویس‌ها ----
def add_service(uid, username, title, gb, days, link):
    with conn() as c:
        c.execute("INSERT INTO services(user_id, username, title, gb, days, link, created_at) "
                  "VALUES(?,?,?,?,?,?,?)", (uid, username, title, gb, days, link, now_str()))


def user_services(uid):
    with conn() as c:
        return c.execute("SELECT * FROM services WHERE user_id=? ORDER BY id", (uid,)).fetchall()


def all_services():
    with conn() as c:
        return c.execute("SELECT * FROM services WHERE title!=?", (TEST_TITLE,)).fetchall()


def find_service(uname, uid):
    with conn() as c:
        return c.execute("SELECT * FROM services WHERE username=? AND user_id=?", (uname, uid)).fetchone()


def find_service_any(uname):
    with conn() as c:
        return c.execute("SELECT * FROM services WHERE username=?", (uname,)).fetchone()


def username_taken_local(uname) -> bool:
    return find_service_any(uname) is not None


def update_service_link(uname, link):
    with conn() as c:
        c.execute("UPDATE services SET link=? WHERE username=?", (link, uname))


def last_plan(uname, uid):
    with conn() as c:
        return c.execute("SELECT dk, vk FROM orders WHERE username=? AND user_id=? AND status='done' "
                         "AND kind IN ('new','renew') AND dk IS NOT NULL ORDER BY id DESC LIMIT 1",
                         (uname, uid)).fetchone()


def was_notified(uname, kind) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM notified WHERE username=? AND kind=?", (uname, kind)).fetchone() is not None


def mark_notified(uname, kind):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO notified(username, kind) VALUES(?,?)", (uname, kind))


def clear_notified(uname):
    with conn() as c:
        c.execute("DELETE FROM notified WHERE username=?", (uname,))


# ---- سطح وفاداری ----
def total_spent(uid) -> int:
    with conn() as c:
        r = c.execute("SELECT COALESCE(SUM(amount),0) s FROM orders WHERE user_id=? AND status='done' "
                      "AND kind IN ('new','renew')", (uid,)).fetchone()
    return int(r["s"])


def has_purchase(uid) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM orders WHERE user_id=? AND status='done' AND kind IN ('new','renew')",
                         (uid,)).fetchone() is not None


def level_info(uid):
    spent = total_spent(uid)
    idx = 0
    for i, (th, _, _) in enumerate(LEVELS):
        if spent >= th:
            idx = i
    nxt = LEVELS[idx + 1] if idx + 1 < len(LEVELS) else None
    return spent, LEVELS[idx][1], LEVELS[idx][2], nxt


# ---- سفارش‌ها ----
def create_order(uid, kind, amount, dk=None, vk=None, username=None, code=None, method="wallet",
                 status="processing") -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO orders(user_id, kind, amount, dk, vk, username, discount_code, method, status, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)", (uid, kind, int(amount), dk, vk, username, code, method, status, time.time()))
        return cur.lastrowid


def create_card_order(uid, kind, base, dk=None, vk=None, username=None, code=None):
    """مبلغ سفارش‌های باز را ده‌تومان‌ده‌تومان یکتا می‌کند تا تطبیق خودکار بانک دقیق باشد."""
    with conn(True) as c:
        used = {r["amount"] for r in c.execute(
            "SELECT amount FROM orders WHERE method='card' AND status IN ('awaiting_receipt','pending') "
            "AND created_at>=?", (time.time() - LATE_WINDOW,))}
        amount = int(base)
        while amount in used:
            amount += 10
        cur = c.execute(
            "INSERT INTO orders(user_id, kind, amount, dk, vk, username, discount_code, method, status, created_at) "
            "VALUES(?,?,?,?,?,?,?,'card','awaiting_receipt',?)",
            (uid, kind, amount, dk, vk, username, code, time.time()))
        return cur.lastrowid, amount


def get_order(oid):
    with conn() as c:
        return c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()


def user_orders(uid, limit=10):
    with conn() as c:
        return c.execute("SELECT * FROM orders WHERE user_id=? AND status NOT IN ('cancelled','expired') "
                         "ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()


def claim_order(oid, from_status, to_status) -> bool:
    with conn() as c:
        cur = c.execute("UPDATE orders SET status=? WHERE id=? AND status=?", (to_status, oid, from_status))
        return cur.rowcount == 1


def claim_order_any(oid, from_statuses, to_status) -> bool:
    q = ",".join("?" * len(from_statuses))
    with conn() as c:
        cur = c.execute(f"UPDATE orders SET status=? WHERE id=? AND status IN ({q})",
                        (to_status, oid, *from_statuses))
        return cur.rowcount == 1


def set_order_status(oid, status):
    with conn() as c:
        c.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))


def attach_receipt(oid, uid, file_uid) -> bool:
    with conn() as c:
        cur = c.execute("UPDATE orders SET status='pending', receipt_uid=? "
                        "WHERE id=? AND user_id=? AND status='awaiting_receipt'", (file_uid, oid, uid))
        return cur.rowcount == 1


def revert_receipt(oid):
    with conn() as c:
        c.execute("UPDATE orders SET status='awaiting_receipt', receipt_uid=NULL WHERE id=? AND status='pending'",
                  (oid,))


def receipt_used_before(file_uid) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM orders WHERE receipt_uid=?", (file_uid,)).fetchone() is not None


def cancel_order_release(oid):
    o = get_order(oid)
    if o and claim_order(oid, "awaiting_receipt", "cancelled"):
        release_discount(o["discount_code"], o["user_id"])


def trim_open_orders(uid, keep=2):
    with conn() as c:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM orders WHERE user_id=? AND status='awaiting_receipt' ORDER BY id DESC", (uid,))]
    for oid in ids[keep - 1:]:
        cancel_order_release(oid)


def expire_old_orders() -> int:
    cutoff = time.time() - ORDER_TTL
    with conn() as c:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM orders WHERE status='awaiting_receipt' AND created_at<?", (cutoff,))]
    n = 0
    for oid in ids:
        o = get_order(oid)
        if o and claim_order(oid, "awaiting_receipt", "expired"):
            release_discount(o["discount_code"], o["user_id"])
            n += 1
    return n


def find_orders_by_amounts(amounts, statuses):
    if not amounts:
        return []
    qa = ",".join("?" * len(amounts))
    qs = ",".join("?" * len(statuses))
    with conn() as c:
        return c.execute(
            f"SELECT * FROM orders WHERE status IN ({qs}) AND method='card' AND created_at>=? "
            f"AND amount IN ({qa}) ORDER BY id", (*statuses, time.time() - LATE_WINDOW, *amounts)).fetchall()


def stuck_orders(seconds=900):
    with conn() as c:
        return c.execute("SELECT * FROM orders WHERE status='processing' AND created_at<?",
                         (time.time() - seconds,)).fetchall()


def sms_recent(h, window=90) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM sms_log WHERE hash=? AND created_at>?", (h, time.time() - window)).fetchone() is not None


def sms_register(h):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO sms_log(hash, created_at) VALUES(?,?)", (h, time.time()))
        c.execute("DELETE FROM sms_log WHERE created_at<?", (time.time() - 86400,))


# ---- تیکت ----
def create_ticket(uid, text) -> int:
    with conn() as c:
        cur = c.execute("INSERT INTO tickets(user_id, text, status, created_at) VALUES(?,?, 'open', ?)",
                        (uid, text, now_str()))
        return cur.lastrowid


def get_ticket(tid):
    with conn() as c:
        return c.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()


def set_ticket_status(tid, status):
    with conn() as c:
        c.execute("UPDATE tickets SET status=? WHERE id=?", (status, tid))


# ---- دعوت ----
def add_referral(referrer, referred):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO referrals(referred_id, referrer_id) VALUES(?,?)", (referred, referrer))


def referral_count(uid) -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM referrals WHERE referrer_id=?", (uid,)).fetchone()["n"]


def referral_earnings(uid) -> int:
    with conn() as c:
        r = c.execute("SELECT COALESCE(SUM(amount),0) s FROM wallet_log WHERE user_id=? AND reason='پاداش دعوت'",
                      (uid,)).fetchone()
    return int(r["s"])


def get_referrer(uid):
    with conn() as c:
        r = c.execute("SELECT referrer_id FROM referrals WHERE referred_id=?", (uid,)).fetchone()
    return r["referrer_id"] if r else None


# ---- تخفیف و هدیه ----
def lookup_discount(code, uid):
    with conn() as c:
        r = c.execute("SELECT percent, max_uses, used FROM discounts WHERE code=? AND active=1", (code,)).fetchone()
        if not r or (r["max_uses"] and r["used"] >= r["max_uses"]):
            return None
        if c.execute("SELECT 1 FROM discount_users WHERE code=? AND user_id=?", (code, uid)).fetchone():
            return None
    return int(r["percent"])


def consume_discount(code, uid) -> bool:
    with conn(True) as c:
        r = c.execute("SELECT max_uses, used FROM discounts WHERE code=? AND active=1", (code,)).fetchone()
        if not r or (r["max_uses"] and r["used"] >= r["max_uses"]):
            return False
        if c.execute("SELECT 1 FROM discount_users WHERE code=? AND user_id=?", (code, uid)).fetchone():
            return False
        c.execute("INSERT INTO discount_users(code, user_id) VALUES(?,?)", (code, uid))
        c.execute("UPDATE discounts SET used=used+1 WHERE code=?", (code,))
        return True


def release_discount(code, uid):
    if not code:
        return
    with conn(True) as c:
        cur = c.execute("DELETE FROM discount_users WHERE code=? AND user_id=?", (code, uid))
        if cur.rowcount:
            c.execute("UPDATE discounts SET used=MAX(used-1,0) WHERE code=?", (code,))


def redeem_gift(code, uid) -> int:
    with conn(True) as c:
        row = c.execute("SELECT amount FROM gifts WHERE code=?", (code,)).fetchone()
        if not row:
            return 0
        cur = c.execute("UPDATE gifts SET used_by=? WHERE code=? AND used_by IS NULL", (uid, code))
        if cur.rowcount != 1:
            return 0
        c.execute("INSERT INTO balances(user_id, balance) VALUES(?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET balance=balance+excluded.balance", (uid, row["amount"]))
        _log_wallet(c, uid, row["amount"], "کد هدیه")
        return int(row["amount"])


def redeem_gift_safe(code: str, uid: int) -> int:
    try:
        return redeem_gift(code.strip().upper(), uid)
    except Exception as e:
        logger.error(f"redeem_gift: {e}")
        return 0


# ---- گزارش‌ها ----
def sales_summary(seconds, title) -> str:
    since = time.time() - seconds
    since_s = datetime.fromtimestamp(since, TZ).strftime("%Y/%m/%d %H:%M:%S")
    with conn() as c:
        s = c.execute("SELECT COUNT(*) n, COALESCE(SUM(amount),0) t FROM orders "
                      "WHERE status='done' AND created_at>=? AND kind!='credit'", (since,)).fetchone()
        cr = c.execute("SELECT COUNT(*) n, COALESCE(SUM(amount),0) t FROM orders "
                       "WHERE status='done' AND created_at>=? AND kind='credit'", (since,)).fetchone()
        nu = c.execute("SELECT COUNT(*) n FROM users WHERE joined_at>=?", (since_s,)).fetchone()
        pend = c.execute("SELECT COUNT(*) n FROM orders WHERE status IN ('pending','processing')").fetchone()
    return (f"📈 {title}\n\n🛒 فروش سرویس: {s['n']} سفارش | {fmt(s['t'])} تومان\n"
            f"🏦 شارژ کیف پول: {cr['n']} مورد | {fmt(cr['t'])} تومان\n"
            f"👥 کاربر جدید: {nu['n']}\n🕓 سفارش باز: {pend['n']}")


def sales_by_day(days=7) -> str:
    with conn() as c:
        rows = c.execute(
            "SELECT strftime('%m/%d', created_at+12600, 'unixepoch') d, COUNT(*) n, SUM(amount) t FROM orders "
            "WHERE status='done' AND kind!='credit' AND created_at>=? GROUP BY d ORDER BY d DESC",
            (time.time() - days * 86400,)).fetchall()
    if not rows:
        return "📅 فروش روزانه: داده‌ای نیست."
    return "📅 فروش روزانه:\n" + "\n".join(f"{r['d']} → {r['n']} سفارش | {fmt(r['t'])} تومان" for r in rows)


def stats_text() -> str:
    with conn() as c:
        users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        banned = c.execute("SELECT COUNT(*) n FROM users WHERE banned=1").fetchone()["n"]
        buyers = c.execute("SELECT COUNT(DISTINCT user_id) n FROM orders WHERE status='done' "
                           "AND kind IN ('new','renew')").fetchone()["n"]
        svcs = c.execute("SELECT COUNT(*) n FROM services").fetchone()["n"]
        pend = c.execute("SELECT COUNT(*) n FROM orders WHERE status IN ('pending','processing')").fetchone()["n"]
        tick = c.execute("SELECT COUNT(*) n FROM tickets WHERE status='open'").fetchone()["n"]
        card = c.execute("SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='done' AND method='card' "
                         "AND kind!='credit'").fetchone()["s"]
        wal = c.execute("SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='done' "
                        "AND method='wallet'").fetchone()["s"]
        cred = c.execute("SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='done' "
                         "AND kind='credit'").fetchone()["s"]
        bal = c.execute("SELECT COALESCE(SUM(balance),0) s FROM balances").fetchone()["s"]
        today = datetime.now(TZ).strftime("%Y/%m/%d")
        nt = c.execute("SELECT COUNT(*) n FROM users WHERE joined_at LIKE ?", (today + "%",)).fetchone()["n"]
    conv = f"{buyers * 100 // users}٪" if users else "0٪"
    return (f"📊 آمار\n\n👥 کاربران: {users} (امروز: {nt} | مسدود: {banned})\n"
            f"🛍 خریداران: {buyers} (نرخ تبدیل {conv})\n📦 سرویس‌ها: {svcs}\n"
            f"🕓 سفارش‌های باز: {pend}\n🎫 تیکت‌های باز: {tick}\n"
            f"💳 فروش کارت‌به‌کارت: {fmt(card)} تومان\n💰 فروش از کیف پول: {fmt(wal)} تومان\n"
            f"🏦 شارژ کیف پول: {fmt(cred)} تومان\n👛 جمع موجودی کیف‌پول کاربران: {fmt(bal)} تومان")


def sales_text() -> str:
    return "\n\n".join([sales_summary(86400, "۲۴ ساعت اخیر"), sales_summary(7 * 86400, "۷ روز اخیر"),
                        sales_summary(30 * 86400, "۳۰ روز اخیر"), sales_by_day()])


def orders_text() -> str:
    with conn() as c:
        rows = c.execute("SELECT * FROM orders WHERE status IN ('pending','processing','awaiting_receipt') "
                         "ORDER BY id DESC LIMIT 25").fetchall()
    if not rows:
        return "سفارش بازی وجود ندارد."
    return "\n".join(f"#{r['id']} | {r['kind']} | کاربر {r['user_id']} | {fmt(r['amount'])} | {r['status']}"
                     for r in rows)


def tickets_text() -> str:
    with conn() as c:
        rows = c.execute("SELECT * FROM tickets WHERE status='open' ORDER BY id DESC LIMIT 15").fetchall()
    if not rows:
        return "🎫 تیکت بازی وجود ندارد."
    return "🎫 تیکت‌های باز:\n\n" + "\n\n".join(
        f"#{r['id']} | کاربر {r['user_id']} | {r['created_at']}\n{r['text'][:120]}" for r in rows) + \
        "\n\nبرای پاسخ: /ticket شماره  (پیام اصلی را دوباره می‌فرستد تا ریپلای کنید)"


def top_referrers_text() -> str:
    with conn() as c:
        rows = c.execute("SELECT referrer_id, COUNT(*) n FROM referrals GROUP BY referrer_id "
                         "ORDER BY n DESC LIMIT 10").fetchall()
    if not rows:
        return "هنوز دعوتی ثبت نشده."
    return "🏆 برترین معرف‌ها:\n" + "\n".join(
        f"{i}. کاربر {r['referrer_id']} — {r['n']} دعوت | درآمد {fmt(referral_earnings(r['referrer_id']))} تومان"
        for i, r in enumerate(rows, 1))


def settings_text() -> str:
    lines = ["⚙️ تنظیمات فعلی:\n"]
    names = {"cashback_percent": "کش‌بک هر خرید (٪)", "first_buy_percent": "تخفیف اولین خرید (٪)",
             "referral_percent": "پورسانت دعوت (٪)", "topup_bonus_percent": "هدیه شارژ بالای 200 هزار (٪)",
             "daily_bonus": "هدیه روزانه (تومان)", "test_enabled": "اکانت تست (1=فعال)",
             "test_daily_cap": "سقف تست روزانه (0=نامحدود)", "notify_new": "اعلان کاربر جدید به ادمین (1=فعال)"}
    for k, v in names.items():
        lines.append(f"• {v}: {get_setting(k)}   ←  /set {k} مقدار")
    lines.append(f"\n🛠 حالت تعمیر: {'روشن' if get_setting('maintenance') == '1' else 'خاموش'}")
    lines.append(f"📣 بنر خوشامد: {get_setting('promo') or '-'}   ←  /setpromo متن  (برای حذف: -)")
    return "\n".join(lines)


def welcome_text() -> str:
    t = f"🚀 به فروشگاه {BRAND_TITLE} خوش آمدید\n\n"
    promo = get_setting("promo")
    if promo:
        t += f"{promo}\n\n"
    return t + "یکی از گزینه‌ها رو انتخاب کن:"


def export_orders_csv() -> BytesIO:
    with conn() as c:
        rows = c.execute("SELECT * FROM orders ORDER BY id").fetchall()
    sio = io.StringIO()
    w = csv.writer(sio)
    w.writerow(["id", "user_id", "kind", "amount", "dk", "vk", "username", "discount_code", "method",
                "status", "created"])
    for r in rows:
        w.writerow([r["id"], r["user_id"], r["kind"], r["amount"], r["dk"], r["vk"], r["username"],
                    r["discount_code"], r["method"], r["status"],
                    datetime.fromtimestamp(r["created_at"], TZ).strftime("%Y/%m/%d %H:%M:%S")])
    b = BytesIO(sio.getvalue().encode("utf-8-sig"))
    b.name = "orders.csv"
    return b


# ============================== پنل ==============================
HTTP: "httpx.AsyncClient | None" = None
_GROUP_CACHE = {"ids": None}


async def panel(method, path, **kw):
    return await HTTP.request(method, f"{PANEL_URL}{path}", headers={"X-Api-Key": PANEL_API_KEY},
                              timeout=25.0, **kw)


def sub_url(d: dict) -> str:
    u = d.get("subscription_url") or ""
    if isinstance(u, str) and u.startswith("/"):
        u = PANEL_URL + u
    return u if isinstance(u, str) else ""


def parse_expire(v):
    if not v:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def quota_valid(info: dict) -> bool:
    expire = parse_expire(info.get("expire"))
    limit = int(info.get("data_limit") or 0)
    used = int(info.get("used_traffic") or 0)
    return (not expire or expire > time.time()) and (not limit or used < limit)


def _fallback_groups():
    try:
        return [int(PANEL_GROUP_NAME)]
    except ValueError:
        return [1]


async def get_group_ids():
    if _GROUP_CACHE["ids"] is not None:
        return _GROUP_CACHE["ids"]
    try:
        resp = await panel("GET", "/api/groups")
        if resp.status_code == 200:
            groups = resp.json()
            items = groups.get("groups", groups) if isinstance(groups, dict) else groups
            for g in items:
                if str(g.get("name", "")).lower() == str(PANEL_GROUP_NAME).lower():
                    _GROUP_CACHE["ids"] = [g["id"]]
                    return _GROUP_CACHE["ids"]
            _GROUP_CACHE["ids"] = _fallback_groups()
            return _GROUP_CACHE["ids"]
    except Exception as e:
        logger.warning(f"خطا در گرفتن گروه‌ها: {e}")
    return _fallback_groups()


async def panel_get_user(username):
    for _ in range(2):
        try:
            r = await panel("GET", f"/api/user/{username}")
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except Exception as e:
            logger.warning(f"panel_get_user: {e}")
    return None


async def panel_user_exists(username):
    try:
        r = await panel("GET", f"/api/user/{username}")
        if r.status_code == 200:
            return True
        if r.status_code == 404:
            return False
    except Exception as e:
        logger.warning(f"panel_user_exists: {e}")
    return None


async def panel_delete_user(username):
    try:
        await panel("DELETE", f"/api/user/{username}")
    except Exception as e:
        logger.warning(f"panel_delete_user: {e}")


async def panel_create_user(username: str, gb: float, days: int, tag: str = ""):
    note = f"bot:{tag}"
    payload = {"username": username, "status": "active", "data_limit": int(gb * 1024 ** 3),
               "data_limit_reset_strategy": "no_reset",
               "expire": int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp()),
               "group_ids": await get_group_ids(), "note": note}
    status, err = 0, ""
    try:
        resp = await panel("POST", "/api/user", json=payload)
        status = resp.status_code
        if status in (200, 201):
            link = sub_url(resp.json())
            if not link:
                info = await panel_get_user(username)
                link = sub_url(info) if info else ""
            if link:
                return True, link
            await panel_delete_user(username)
            return False, "کاربر ساخته شد ولی لینک اشتراک برنگشت (کاربر حذف شد)."
        err = f"خطای پنل ({status}): {resp.text[:300]}"
    except Exception as e:
        err = f"خطا در اتصال به پنل: {e}"
    if tag and status in (0, 409):
        info = await panel_get_user(username)
        if info and info.get("note") == note:
            link = sub_url(info)
            if link:
                return True, link
    return False, err


async def panel_renew(username: str, gb: float, days: int):
    info = await panel_get_user(username)
    if not info:
        return False, "کاربر در پنل پیدا نشد یا پنل در دسترس نیست."
    now = time.time()
    expire = parse_expire(info.get("expire"))
    limit = int(info.get("data_limit") or 0)
    active = info.get("status") in ("active", "disabled", "on_hold") and quota_valid(info)
    add_bytes = int(gb * 1024 ** 3)
    try:
        if active:
            new_expire = int((expire or now) + days * 86400)
            new_limit = (limit + add_bytes) if limit else add_bytes
        else:
            await panel("POST", f"/api/user/{username}/reset")
            new_expire = int(now + days * 86400)
            new_limit = add_bytes
        resp = await panel("PUT", f"/api/user/{username}",
                           json={"expire": new_expire, "data_limit": new_limit, "status": "active"})
        if resp.status_code in (200, 201):
            return True, sub_url(resp.json())
        return False, f"خطای پنل ({resp.status_code}): {resp.text[:300]}"
    except Exception as e:
        return False, f"خطا در اتصال به پنل: {e}"


async def panel_change_status(username: str, status: str) -> bool:
    try:
        resp = await panel("PUT", f"/api/user/{username}", json={"status": status})
        return resp.status_code in (200, 201)
    except Exception:
        return False


async def panel_revoke_sub(username: str) -> str:
    try:
        resp = await panel("POST", f"/api/user/{username}/revoke_sub")
        if resp.status_code in (200, 201):
            return sub_url(resp.json())
    except Exception as e:
        logger.warning(f"panel_revoke_sub: {e}")
    return ""


# ============================== عضویت و دسترسی ==============================
_JOIN_CACHE = {}
_JOIN_ALERT = {"t": 0.0}


async def is_member(bot, user_id) -> bool:
    now = time.time()
    c = _JOIN_CACHE.get(user_id)
    if c and now - c < 120:
        return True
    try:
        m = await bot.get_chat_member(chat_id=CHANNEL, user_id=user_id)
        ok = m.status in ("member", "administrator", "creator") or \
            (m.status == "restricted" and getattr(m, "is_member", False))
        if ok:
            _JOIN_CACHE[user_id] = now
        return ok
    except Exception as e:
        logger.warning(f"خطا در بررسی عضویت: {e}")
        if now - _JOIN_ALERT["t"] > 3600:
            _JOIN_ALERT["t"] = now
            await notify_admin(bot, f"⚠️ بررسی عضویت کانال خطا داد (ربات ادمین کانال است؟):\n{e}")
        return True


def join_keyboard():
    return Markup([[Btn("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL.lstrip('@')}")],
                   [Btn("✅ عضو شدم", callback_data="check_join")]])


JOIN_TEXT = "برای استفاده از بات، اول باید تو کانال ما عضو بشی 👇"
BANNED_TEXT = "⛔️ دسترسی شما به ربات مسدود شده است."
MAINT_TEXT = "🛠 ربات در حال بروزرسانی است. لطفاً کمی بعد دوباره تلاش کنید."


def _target(update):
    return update.message or (update.callback_query.message if update.callback_query else None)


async def gate(update, context, user_id) -> bool:
    """True یعنی کاربر نباید ادامه دهد."""
    if user_id == ADMIN_ID:
        return False
    if throttled(user_id, "flood", 0.5):
        return True
    t = _target(update)
    if is_banned(user_id):
        if t:
            await t.reply_text(BANNED_TEXT)
        return True
    if get_setting("maintenance") == "1":
        if t:
            await t.reply_text(MAINT_TEXT)
        return True
    if await is_member(context.bot, user_id):
        return False
    if t:
        await t.reply_text(JOIN_TEXT, reply_markup=join_keyboard())
    return True


# ============================== کیبوردها و متن‌ها ==============================
def main_keyboard():
    return ReplyKeyboardMarkup([
        [BTN_NEW], [BTN_TEST, BTN_RENEW], [BTN_MY, BTN_ORDERS], [BTN_PRICES, BTN_DAILY],
        [BTN_CREDIT, BTN_INVITE], [BTN_HELP, BTN_SUPPORT], [BTN_AGENT],
    ], resize_keyboard=True)


def cancel_receipt_keyboard():
    return ReplyKeyboardMarkup([[BTN_CANCEL_RECEIPT]], resize_keyboard=True)


def back_menu_kb():
    return Markup([[Btn("🔙 بازگشت به منوی اصلی", callback_data="menu")]])


def duration_menu():
    rows = [[Btn(t, callback_data=f"dur_{k}")] for k, (t, _) in DURATIONS.items()]
    rows.append([Btn("🔙 بازگشت به منوی اصلی", callback_data="menu")])
    return Markup(rows)


def volume_menu(dk):
    rows = [[Btn(f"{gb} گیگ | {fmt(prices[dk])} تومان", callback_data=f"vol_{dk}_{vk}")]
            for vk, (gb, prices) in VOLUMES.items()]
    rows.append([Btn("🔙 بازگشت به مدت‌ها", callback_data="dur_menu")])
    return Markup(rows)


def help_menu():
    return Markup([[Btn("Android", callback_data="hlp_android")], [Btn("iOS", callback_data="hlp_ios")],
                   [Btn("Windows", callback_data="hlp_windows")],
                   [Btn("🔙 بازگشت به منوی اصلی", callback_data="menu")]])


def admin_keyboard(oid):
    return Markup([[Btn("✅ تایید", callback_data=f"approve_{oid}"), Btn("❌ رد", callback_data=f"reject_{oid}")]])


def admin_panel_kb():
    return Markup([
        [Btn("📊 آمار", callback_data="adm_stats"), Btn("📈 فروش", callback_data="adm_sales")],
        [Btn("🕓 سفارش‌های باز", callback_data="adm_orders"), Btn("🎫 تیکت‌ها", callback_data="adm_tickets")],
        [Btn("⏰ نزدیک انقضا", callback_data="adm_expiring"), Btn("🏆 برترین معرف‌ها", callback_data="adm_top")],
        [Btn("⚙️ تنظیمات", callback_data="adm_settings"), Btn("📤 خروجی سفارش‌ها", callback_data="adm_export")],
        [Btn("💾 بکاپ", callback_data="adm_backup"), Btn("🔌 تست اتصال پنل", callback_data="adm_panel")],
    ])


def support_kb():
    return Markup([[Btn("🎫 ثبت تیکت (پاسخ داخل ربات)", callback_data="ticket_new")],
                   [Btn("💬 ارسال پیام به پشتیبانی", url=f"https://t.me/{SUPPORT.lstrip('@')}")],
                   [Btn("❓ سوالات متداول", callback_data="faq")],
                   [Btn("🔙 بازگشت به منوی اصلی", callback_data="menu")]])


def credit_kb(uid):
    on = notify_on(uid)
    return Markup([
        [Btn("💳 افزایش موجودی", callback_data="add_credit")],
        [Btn("📜 تراکنش‌های کیف پول", callback_data="wlog")],
        [Btn("🎁 اعمال کد هدیه", callback_data="gift_code")],
        [Btn(f"🔔 یادآوری سرویس‌ها: {'روشن ✅' if on else 'خاموش ❌'}", callback_data="toggle_notify")],
        [Btn("🔙 بازگشت به منوی اصلی", callback_data="menu")]])


async def services_kb(uid, prefix):
    svcs = user_services(uid)
    infos = await asyncio.gather(*(panel_get_user(s["username"]) for s in svcs))
    rows = []
    for s, info in zip(svcs, infos):
        icon = "⚪️" if info is None else STATUS_ICON.get(info.get("status", ""), "⚪️")
        rows.append([Btn(f"{icon} {s['username']}", callback_data=f"{prefix}{s['username']}")])
    rows.append([Btn("🔙 بازگشت به منوی اصلی", callback_data="menu")])
    return Markup(rows)


def price_list_text() -> str:
    lines = ["💎 <b>تعرفه سرویس‌ها</b> (تومان)\n"]
    for dk, (title, days) in DURATIONS.items():
        lines.append(f"\n⏳ <b>{title}</b> ({days} روز)")
        for vk, (gb, prices) in VOLUMES.items():
            lines.append(f"  • {gb} گیگ — {fmt(prices[dk])}")
    fb, cb = sint("first_buy_percent"), sint("cashback_percent")
    extra = []
    if fb:
        extra.append(f"🎁 اولین خرید شما {fb}٪ تخفیف دارد")
    if cb:
        extra.append(f"💸 {cb}٪ کش‌بک از هر خرید به کیف پول")
    extra.append("🏆 با خرید بیشتر سطح حساب شما (نقره‌ای، طلایی، الماسی) ارتقا پیدا می‌کند")
    return "\n".join(lines) + "\n\n" + "\n".join(extra)


def price_info(uid, order) -> dict:
    base = VOLUMES[order["vk"]][1][order["dk"]]
    code_pct = int(order.get("discount_percent", 0))
    _, lvl_name, lvl_pct, _ = level_info(uid)
    first_pct = sint("first_buy_percent") if (order["kind"] == "new" and not has_purchase(uid)) else 0
    total = min(code_pct + lvl_pct + first_pct, 60)
    final = max(base - base * total // 100, 1000)
    return {"base": base, "code": code_pct, "level": lvl_pct, "level_name": lvl_name,
            "first": first_pct, "total": total, "final": final}


def order_text(uid, order) -> str:
    title, days = DURATIONS[order["dk"]]
    gb = VOLUMES[order["vk"]][0]
    p = price_info(uid, order)
    kind = "تمدید سرویس" if order["kind"] == "renew" else "خرید سرویس جدید"
    lines = [f"🧾 <b>پیش‌فاکتور ({kind})</b>", "",
             f"👤 نام کاربری: <code>{esc(order['username'])}</code>", f"🌐 سرور: {esc(BRAND)}",
             f"📦 دسته محصول: {title}", f"🏷 نوع سرویس: {gb} گیگ | {days} روزه", ""]
    if p["total"]:
        lines.append(f"💵 قیمت اصلی: {fmt(p['base'])} تومان")
        if p["first"]:
            lines.append(f"🎁 تخفیف اولین خرید: {p['first']}٪")
        if p["level"]:
            lines.append(f"🏆 تخفیف سطح {p['level_name']}: {p['level']}٪")
        if p["code"]:
            lines.append(f"🏷 کد تخفیف: {p['code']}٪")
    lines.append(f"💳 مبلغ قابل پرداخت: <b>{fmt(p['final'])}</b> تومان")
    cb = sint("cashback_percent")
    if cb:
        lines.append(f"💸 کش‌بک بعد از خرید: {fmt(p['final'] * cb // 100)} تومان به کیف پول")
    lines += ["", "سفارش شما آماده پرداخت است"]
    return "\n".join(lines)


def confirm_menu(order):
    rows = []
    if not order.get("discount_code"):
        rows.append([Btn("🎁 اعمال کد تخفیف", callback_data="apply_discount")])
    rows.append([Btn("💳 پرداخت ریالی (کارت به کارت)", callback_data="paycard")])
    rows.append([Btn("💰 پرداخت از موجودی کیف پول", callback_data="paywallet")])
    rows.append([Btn("🏠 بازگشت به منوی اصلی", callback_data="menu")])
    return Markup(rows)


def pay_text(amount) -> str:
    owner = f"\n{esc(card_owner())}" if card_owner() else ""
    return (
        f"لطفاً دقیقاً مبلغ <b>{fmt(amount)}</b> تومان (<code>{amount * 10}</code> ریال) به کارت زیر واریز نمایید.\n\n"
        f"<code>{esc(card_number())}</code>{owner}\n\n"
        f"✅ بعد از واریز، پرداخت شما به‌صورت خودکار شناسایی و سرویس فعال می‌شود. "
        f"اگر تا چند دقیقه تایید نشد، عکس رسید را ارسال کنید.\n\n"
        "⚠️ مبلغ را <b>دقیقاً همین عدد</b> واریز کنید و از رند کردن آن خودداری فرمایید (برای شناسایی خودکار ضروری است).\n"
        "⚠️ از اپلیکیشن آپ و تاپ با توجه به محدودیت روزانه برای انتقال استفاده نکنید.\n"
        "⚠️ مبلغی که کپی میکنید ریالی است، توجه کنید.\n"
        f"⏳ این فاکتور تا {ORDER_TTL // 3600 or 1} ساعت معتبر است."
    )


def pay_markup(amount):
    return Markup([
        [Btn("📋 کپی مبلغ", copy_text=CopyTextButton(text=str(amount * 10))),
         Btn("📋 کپی شماره کارت", copy_text=CopyTextButton(text=card_digits()))],
        [Btn("📤 ارسال رسید پرداخت", callback_data="send_receipt")],
        [Btn("🔙 بازگشت به منوی اصلی", callback_data="menu")],
    ])


def gb_fmt(b) -> str:
    return f"{float(b) / 1024 ** 3:.2f}"


def service_view(svc, info, has_plan=False):
    uname = svc["username"]
    st, usage = None, ""
    if info is None:
        status_line = "⚪️ وضعیت: نامشخص (پنل در دسترس نیست)"
    else:
        st = info.get("status", "")
        status_line = f"وضعیت: {STATUS_FA.get(st, st)}"
        used, limit = int(info.get("used_traffic") or 0), int(info.get("data_limit") or 0)
        exp = parse_expire(info.get("expire"))
        usage = f"\n📊 مصرف: {gb_fmt(used)} / {gb_fmt(limit) if limit else '∞'} گیگابایت"
        if limit:
            usage += f"\n{progress_bar(used, limit)}"
            usage += f"\n📦 باقی‌مانده حجم: {max((limit - used) / 1024 ** 3, 0):.2f} گیگابایت"
        if exp:
            usage += f"\n⏳ زمان باقی‌مانده: {max(int((exp - time.time()) // 86400), 0)} روز"
    text = ("📌 <b>اطلاعات سرویس شما</b>\n\n"
            f"👤 نام کاربری: <code>{esc(uname)}</code>\n{status_line}\n🌐 نام پنل: {esc(BRAND)}\n"
            f"📦 نوع اشتراک: {esc(svc['title'])}{usage}\n\n🔗 لینک اشتراک:\n<code>{esc(svc['link'])}</code>")
    row2 = []
    if st == "active":
        row2.append(Btn("⏹ توقف سرویس", callback_data=f"stop_{uname}"))
    elif st == "disabled":
        row2.append(Btn("▶️ فعالسازی سرویس", callback_data=f"active_{uname}"))
    row2.append(Btn("♻️ تمدید سرویس", callback_data=f"rn_{uname}"))
    rows = [[Btn("📥 دریافت QR Code", callback_data=f"qr_{uname}"),
             Btn("🔄 بروزرسانی", callback_data=f"serv_{uname}")], row2]
    if has_plan:
        rows.append([Btn("⚡️ تمدید سریع (همان پلن قبلی)", callback_data=f"qrn_{uname}")])
    rows.append([Btn("❌ حذف لینک فعلی و دریافت لینک جدید", callback_data=f"reset_confirm_{uname}")])
    rows.append([Btn("🔙 بازگشت به لیست سرویس ها", callback_data="back_to_services")])
    return text, Markup(rows)


async def send_service_to_user(bot, user_id, username, link, days, gb, renewed=False):
    head = "🟢 سرویس شما با موفقیت تمدید شد." if renewed else "🟢 اشتراک شما با موفقیت ساخته شد."
    caption = (f"{head}\n👤 نام کاربری شما:\n<code>{esc(username)}</code>\n\n"
               f"🌐 لینک اشتراک شما:\n\n<code>{esc(link)}</code>\n\n"
               "📲 برای کپی کردن لینک بالا فقط کافیست آدرس لینک را یک بار لمس کنید!")
    markup = Markup([
        [Btn("⏳ زمان اشتراک", callback_data="none"), Btn(f"{days} روز", callback_data="none")],
        [Btn("🌐 حجم سرویس", callback_data="none"), Btn(f"{gb} گیگابایت", callback_data="none")],
        [Btn("📥 دریافت QR Code", callback_data=f"qr_{username}")],
        [Btn("📚 آموزش نصب", callback_data="hlp_android")]])
    await bot.send_photo(chat_id=user_id, photo=InputFile(generate_qr_buffer(link), filename="sub_qr.png"),
                         caption=caption, parse_mode=HTML, reply_markup=markup)


def clear_flow(context):
    for k in ("state", "order", "order_id"):
        context.user_data.pop(k, None)


def valid_order(order) -> bool:
    return bool(order) and order.get("dk") in DURATIONS and order.get("vk") in VOLUMES \
        and bool(order.get("username"))


async def show_order_confirm(q, uid, order):
    await q.edit_message_text(order_text(uid, order), parse_mode=HTML, reply_markup=confirm_menu(order))


# ============================== انجام سفارش ==============================
async def reward_referrer(bot, buyer_id, amount):
    pct = sint("referral_percent")
    ref = get_referrer(buyer_id)
    if not ref or ref == buyer_id or pct <= 0 or is_banned(ref):
        return
    reward = int(amount) * pct // 100
    if reward <= 0:
        return
    add_balance(ref, reward, "پاداش دعوت")
    await safe_send(bot, ref, f"🎁 یکی از دعوت‌شده‌های شما خرید کرد و {fmt(reward)} تومان به کیف پولتان اضافه شد.")


async def fulfill_order(bot, oid):
    """سفارشِ در وضعیت processing را اجرا می‌کند. -> (ok, error)"""
    o = get_order(oid)
    uid = o["user_id"]

    if o["kind"] == "credit":
        add_balance(uid, o["amount"], "شارژ حساب")
        bonus, pct = 0, sint("topup_bonus_percent")
        if pct > 0 and o["amount"] >= TOPUP_BONUS_MIN:
            bonus = o["amount"] * pct // 100
            add_balance(uid, bonus, "هدیه شارژ")
        set_order_status(oid, "done")
        msg = f"✅ پرداخت شما تایید شد و {fmt(o['amount'])} تومان به موجودی حساب شما اضافه شد."
        if bonus:
            msg += f"\n🎁 هدیه شارژ: {fmt(bonus)} تومان"
        await safe_send(bot, uid, msg + f"\n💰 موجودی فعلی: {fmt(get_balance(uid))} تومان")
        return True, ""

    title, days = DURATIONS[o["dk"]]
    gb = VOLUMES[o["vk"]][0]
    username = o["username"]

    if o["kind"] == "new":
        ok, res = await panel_create_user(username, gb, days, tag=f"order:{oid}")
        if not ok:
            return False, res
        link = res
        try:
            if not find_service_any(username):
                add_service(uid, username, title, gb, days, link)
        except Exception as e:
            logger.error(f"ثبت سرویس: {e}")
            await notify_admin(bot, f"⚠️ سرویس {username} در پنل ساخته شد ولی در دیتابیس ثبت نشد:\n{e}")
    else:
        svc = find_service(username, uid)
        if not svc:
            return False, "سرویسِ تمدید، متعلق به این کاربر پیدا نشد."
        ok, res = await panel_renew(username, gb, days)
        if not ok:
            return False, res
        link = res or svc["link"]
        if res:
            update_service_link(username, res)
        clear_notified(username)

    set_order_status(oid, "done")
    cash = 0
    try:
        await reward_referrer(bot, uid, o["amount"])
        cb = sint("cashback_percent")
        cash = o["amount"] * cb // 100 if cb > 0 else 0
        if cash:
            add_balance(uid, cash, "کش‌بک خرید")
    except Exception as e:
        logger.error(f"پاداش/کش‌بک: {e}")
        cash = 0
    try:
        await send_service_to_user(bot, uid, username, link, days, gb, renewed=(o["kind"] == "renew"))
        if cash:
            await safe_send(bot, uid, f"💸 {fmt(cash)} تومان کش‌بک این خرید به کیف پول شما اضافه شد.")
    except Exception as e:
        logger.error(f"ارسال سرویس به مشتری ناموفق: {e}")
        await notify_admin(bot, f"⚠️ سفارش #{oid} انجام شد ولی ارسال به کاربر {uid} ناموفق بود "
                                f"(احتمالاً ربات را بلاک کرده).\nنام کاربری: {username}\nلینک: {link}")
    return True, ""


async def run_fulfill(bot, oid):
    try:
        return await fulfill_order(bot, oid)
    except Exception as e:
        logger.exception("fulfill_order")
        return False, f"استثنا: {e}"


async def handle_admin_decision(update, context, data):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await ack(q, "فقط ادمین می‌تونه این کارو بکنه.", True)
        return
    action, oid_s = data.split("_", 1)
    try:
        oid = int(oid_s)
    except ValueError:
        await ack(q, "شناسه نامعتبر", True)
        return
    o = get_order(oid)
    if not o:
        await ack(q, "سفارش پیدا نشد.", True)
        return
    caption = q.message.caption or ""

    if action == "reject":
        if not claim_order(oid, "pending", "rejected"):
            await ack(q, "این سفارش قبلاً بررسی شده یا در حال پردازش است.", True)
            return
        release_discount(o["discount_code"], o["user_id"])
        await ack(q, "رد شد")
        try:
            await q.edit_message_caption(caption=caption + "\n\n❌ رد شد.")
        except Exception:
            pass
        await safe_send(context.bot, o["user_id"],
                        f"❌ متاسفانه رسید شما تایید نشد. لطفاً با پشتیبانی تماس بگیرید: {SUPPORT}")
        return

    if not claim_order(oid, "pending", "processing"):
        await ack(q, "این سفارش قبلاً بررسی شده یا در حال پردازش است.", True)
        return
    await ack(q, "⏳ در حال انجام...")
    ok, err = await run_fulfill(context.bot, oid)
    if not ok:
        claim_order(oid, "processing", "pending")
        await notify_admin(context.bot, f"⚠️ انجام سفارش #{oid} ناموفق بود:\n{err}\n\n"
                                        f"بعد از رفع مشکل دوباره روی ✅ همان رسید بزن.")
        return
    try:
        await q.edit_message_caption(caption=caption + "\n\n✅ تایید شد و انجام/ارسال شد.")
    except Exception:
        pass


# ============================== تایید خودکار با نوتیفیکیشن/پیامک بانک ==============================
DEPOSIT_WORDS = ("واریز", "deposit", "دریافت", "+", "افزایش")
WITHDRAW_WORDS = ("برداشت", "withdraw", "کارمزد", "خرید", "پرداخت از")
BALANCE_WORDS = ("موجودی", "مانده", "balance")
PLACEHOLDER_RE = re.compile(r"^\s*[\{\[%<].{0,40}[\}\]%>]\s*$", re.S)
_SMS_LOCK = None


def _extract_amounts(source: str):
    nums = []
    for m in re.findall(r"\d[\d,٬،]*", source):
        try:
            n = int(re.sub(r"[,٬،]", "", m))
        except ValueError:
            continue
        if 1000 <= n < 10 ** 10:
            nums.append(n)
    cands = set()
    for n in nums:
        cands.add(n)
        if n % 10 == 0:
            cands.add(n // 10)
    return cands


async def process_sms(bot, text: str, sender: str = "") -> str:
    async with _SMS_LOCK:
        return await _process_sms(bot, text, sender)


async def _process_sms(bot, text, sender):
    t = normalize_digits(text or "").strip()
    if not t:
        if not throttled(0, "sms_empty", 20):
            await notify_admin(bot, "ℹ️ نوتیفیکیشن با متن خالی رسید (متغیر ماکرو خالی رفته)")
        return "empty"
    if len(t) < 45 and PLACEHOLDER_RE.match(t):
        if not throttled(0, "sms_ph", 20):
            await notify_admin(bot, f"⚠️ متغیر ماکرو جایگزین نشده و خودِ اسمش آمده:\n{t}\n"
                                    "در Body ماکرو متغیر را از دکمه {} انتخاب کنید، دستی تایپ نکنید.")
        return "placeholder"
    if SMS_SENDERS and not any(s.lower() in (sender or "").lower() for s in SMS_SENDERS):
        return "ignored_sender"

    low = t.lower()
    has_dep = any(k in low for k in DEPOSIT_WORDS)
    has_wd = any(k in low for k in WITHDRAW_WORDS)
    if has_wd and not any(k in low for k in ("واریز", "deposit", "دریافت")):
        return "ignored_withdraw"
    if not has_dep:
        if not throttled(0, "sms_ign", 20):
            await notify_admin(bot, f"ℹ️ پیام نادیده گرفته شد (نشانه واریز ندارد):\n\n{t[:500]}")
        return "ignored"

    h = hashlib.sha256(t.encode("utf-8")).hexdigest()
    if sms_recent(h):
        return "duplicate"

    lines = [ln for ln in t.splitlines() if not any(b in ln.lower() for b in BALANCE_WORDS)]
    dep_lines = [ln for ln in lines if any(k in ln.lower() for k in DEPOSIT_WORDS)]
    cands = _extract_amounts(" ".join(dep_lines)) or _extract_amounts(" ".join(lines))
    if not cands:
        await notify_admin(bot, f"ℹ️ مبلغی در پیام پیدا نشد:\n\n{t[:600]}")
        return "no_amount"

    rows = find_orders_by_amounts(list(cands), ("awaiting_receipt", "pending"))
    if not rows:
        late = find_orders_by_amounts(list(cands), ("expired", "cancelled"))
        if late:
            ids = ", ".join(f"#{r['id']}" for r in late)
            await notify_admin(bot, f"⚠️ واریز دریافت شد ولی سفارش مربوطه منقضی/لغو شده ({ids}).\n"
                                    f"در صورت تایید: /force شماره_سفارش\n\n{t[:500]}")
            return "late_match"
        await notify_admin(bot, f"ℹ️ واریز دریافت شد ولی سفارش بازی با این مبلغ پیدا نشد:\n\n{t[:600]}")
        return "no_match"
    if len(rows) > 1:
        pend = [r for r in rows if r["status"] == "pending"]
        if len(pend) == 1:
            rows = pend
    if len(rows) > 1:
        ids = ", ".join(f"#{r['id']}" for r in rows)
        await notify_admin(bot, f"⚠️ چند سفارش هم‌مبلغ ({ids}) مطابقت دارند؛ دستی تایید کنید:\n\n{t[:600]}")
        return "ambiguous"

    o = rows[0]
    oid = o["id"]
    if not claim_order_any(oid, ("pending", "awaiting_receipt"), "processing"):
        return "busy"
    ok, err = await run_fulfill(bot, oid)
    if not ok:
        claim_order(oid, "processing", "pending")
        await notify_admin(bot, f"⚠️ تایید خودکار سفارش #{oid} ناموفق بود:\n{err}")
        return "failed"
    sms_register(h)
    await notify_admin(bot, f"✅ سفارش #{oid} ({KIND_FA.get(o['kind'], o['kind'])} | {fmt(o['amount'])} تومان | "
                            f"کاربر {o['user_id']}) خودکار تایید شد.")
    return "ok"


def parse_sms_body(raw: str, query: dict):
    s = (raw or "").strip()
    try:
        j = json.loads(s)
        if isinstance(j, dict):
            return (str(j.get("text") or j.get("message") or j.get("body") or ""),
                    str(j.get("sender") or j.get("from") or (query.get("sender") or [""])[0]))
    except ValueError:
        pass
    m = re.search(r'"text"\s*:\s*"(.*)"\s*,\s*"sender"\s*:\s*"(.*?)"\s*\}?\s*$', s, re.S)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r'^(?:text|message)=(.*?)(?:&sender=([^&]*))?$', s, re.S)
    if m:
        t, snd = m.group(1), m.group(2) or ""
        if re.search(r'%[0-9A-Fa-f]{2}', t + snd):
            t, snd = unquote_plus(t), unquote_plus(snd)
        return t, snd
    return s, (query.get("sender") or [""])[0]


_BOT = None
_LOOP = None


class WebHandler(BaseHTTPRequestHandler):
    def _reply(self, code, body: dict):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - bot is running")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        try:
            u = urlparse(self.path)
            if u.path.rstrip("/") != "/sms" or not SMS_SECRET:
                return self._reply(404, {"ok": False})
            query = parse_qs(u.query)
            key = (query.get("key") or [""])[0] or self.headers.get("X-Secret", "")
            if not secrets.compare_digest(key.encode("utf-8"), SMS_SECRET.encode("utf-8")):
                return self._reply(403, {"ok": False})
            length = min(int(self.headers.get("Content-Length") or 0), 10000)
            raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
            text, sender = parse_sms_body(raw, query)
            if _BOT is None or _LOOP is None:
                return self._reply(503, {"ok": False})
            fut = asyncio.run_coroutine_threadsafe(process_sms(_BOT, text, sender), _LOOP)
            self._reply(200, {"ok": True, "result": fut.result(timeout=90)})
        except Exception as e:
            logger.error(f"webhook: {e}")
            try:
                self._reply(500, {"ok": False})
            except Exception:
                pass

    def log_message(self, format, *args):
        pass


def run_web_server():
    try:
        server = ThreadingHTTPServer(("0.0.0.0", int(PORT)), WebHandler)
        logger.info(f"وب‌سرور روی پورت {PORT} فعال شد")
        server.serve_forever()
    except OSError as e:
        logger.warning(f"وب‌سرور اجرا نشد: {e}")


# ============================== هندلرها ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    clear_flow(context)
    user = update.effective_user
    user_id = user.id
    is_new = add_user(user_id)
    if is_new:
        if context.args and context.args[0].startswith("ref_"):
            try:
                referrer_id = int(context.args[0][4:])
            except ValueError:
                referrer_id = None
            if referrer_id and referrer_id != user_id:
                add_referral(referrer_id, user_id)
        if sint("notify_new") == 1:
            await notify_admin(context.bot, f"👤 کاربر جدید: {user.full_name} (@{user.username or '-'}) | {user_id}")
    if await gate(update, context, user_id):
        return
    await update.message.reply_text(welcome_text(), reply_markup=main_keyboard())


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.message:
        return
    user_id = user.id
    add_user(user_id)
    if await gate(update, context, user_id):
        return
    msg = update.message
    text = (msg.text or "").strip()

    if text in MENU_BUTTONS:
        clear_flow(context)
        if text == BTN_CANCEL_RECEIPT:
            await msg.reply_text("به منوی اصلی بازگشتید!", reply_markup=main_keyboard())
            return

    state = context.user_data.get("state")

    if state == "waiting_custom_credit":
        context.user_data.pop("state", None)
        try:
            amount = int(normalize_digits(text).replace(",", "").replace("٬", "").strip())
            if amount < 10000 or amount > MAX_WALLET_TOPUP:
                raise ValueError
        except ValueError:
            await msg.reply_text("❌ مبلغ نامعتبر است (حداقل 10,000 و حداکثر 50,000,000 تومان)",
                                 reply_markup=Markup([[Btn("🔙 بازگشت", callback_data="add_credit")]]))
            return
        await start_credit_order(msg, context, user_id, amount)
        return

    if state == "waiting_ticket":
        context.user_data.pop("state", None)
        if not text:
            return
        if throttled(user_id, "ticket", 20):
            await msg.reply_text("⏳ کمی صبر کنید و دوباره تیکت را ارسال کنید.")
            return
        body = text[:1500]
        tid = create_ticket(user_id, body)
        await notify_admin(context.bot,
                           f"🎫 تیکت #{tid}\n👤 {user.full_name} (@{user.username or '-'})\n🆔 {user_id}\n\n"
                           f"{body}\n\n↩️ برای پاسخ روی همین پیام ریپلای کنید.")
        await msg.reply_text(f"✅ تیکت #{tid} ثبت شد. پاسخ را همین‌جا در ربات دریافت می‌کنید.",
                             reply_markup=main_keyboard())
        return

    if state == "waiting_gift_code":
        context.user_data.pop("state", None)
        if throttled(user_id, "gift", 3):
            return
        amount = redeem_gift_safe(text, user_id)
        if amount:
            await msg.reply_text(f"🎁 کد هدیه اعمال شد و {fmt(amount)} تومان به موجودی شما اضافه شد.",
                                 reply_markup=main_keyboard())
        else:
            await msg.reply_text("❌ کد هدیه نامعتبر یا استفاده‌شده است.", reply_markup=back_menu_kb())
        return

    if state == "waiting_discount":
        context.user_data.pop("state", None)
        order = context.user_data.get("order")
        if not valid_order(order):
            await msg.reply_text("نشست منقضی شد. دوباره از منو شروع کن.", reply_markup=main_keyboard())
            return
        if throttled(user_id, "discount", 3):
            return
        code = text.upper()
        pct = lookup_discount(code, user_id)
        if pct is None:
            await msg.reply_text("❌ کد تخفیف نامعتبر یا قبلاً استفاده شده است.")
        else:
            order["discount_code"], order["discount_percent"] = code, pct
            order.pop("oid", None)
            context.user_data["order"] = order
        await msg.reply_text(order_text(user_id, order), parse_mode=HTML, reply_markup=confirm_menu(order))
        return

    if state == "waiting_agent_msg":
        context.user_data.pop("state", None)
        await notify_admin(context.bot,
                           f"🤝 درخواست نمایندگی جدید\n\n👤 نام: {user.full_name}\n"
                           f"🆔 یوزرنیم: @{user.username or 'ندارد'}\n🔢 آیدی: {user_id}\n\n💬 متن پیام:\n{text}\n\n"
                           f"پاسخ: /sendto {user_id} متن")
        await msg.reply_text("🚀 پیام شما ارسال شد منتظر پاسخ مدیریت باشید.", reply_markup=main_keyboard())
        return

    if state == "waiting_username":
        username = text.lower()
        if not USERNAME_RE.match(username):
            await msg.reply_text("❌ نام کاربری نامعتبر است. فقط حروف کوچک لاتین، عدد و _ (بین 3 تا 20 کاراکتر) مجاز است:")
            return
        order = context.user_data.get("order")
        if not order or "dk" not in order:
            context.user_data.pop("state", None)
            await msg.reply_text("نشست منقضی شد. دوباره از منو شروع کن.", reply_markup=main_keyboard())
            return
        if username_taken_local(username) or await panel_user_exists(username) is True:
            await msg.reply_text("❌ این نام کاربری قبلاً گرفته شده. نام دیگری وارد کنید:")
            return
        context.user_data.pop("state", None)
        order["username"] = username
        order.pop("oid", None)
        context.user_data["order"] = order
        await msg.reply_text(order_text(user_id, order), parse_mode=HTML, reply_markup=confirm_menu(order))
        return

    if state == "waiting_receipt":
        await msg.reply_text("📷 لطفاً تصویر رسید پرداخت را به صورت عکس ارسال نمایید.",
                             reply_markup=cancel_receipt_keyboard())
        return

    if text == BTN_NEW:
        context.user_data["order"] = {"kind": "new"}
        await msg.reply_text("⏳ انتخاب مدت زمان:", reply_markup=duration_menu())
        return

    if text == BTN_RENEW:
        if not user_services(user_id):
            await msg.reply_text("شما هنوز سرویسی ندارید.", reply_markup=main_keyboard())
            return
        await msg.reply_text("♻️ سرویسی که می‌خواهید تمدید شود را انتخاب کنید:",
                             reply_markup=await services_kb(user_id, "rn_"))
        return

    if text == BTN_TEST:
        await handle_test_request(update, context)
        return

    if text == BTN_MY:
        if not user_services(user_id):
            await msg.reply_text("📲 شما هنوز سرویسی خریداری نکرده‌اید.\n\nبرای خرید از منوی اصلی گزینه «🚀 خرید سرویس جدید» را بزنید.",
                                 reply_markup=main_keyboard())
            return
        await msg.reply_text("🔹 برای مشاهده اطلاعات روی محصول کلیک کنید:",
                             reply_markup=await services_kb(user_id, "serv_"))
        return

    if text == BTN_ORDERS:
        rows = user_orders(user_id)
        if not rows:
            await msg.reply_text("🧾 هنوز سفارشی ثبت نکرده‌اید.", reply_markup=main_keyboard())
            return
        lines = ["🧾 <b>آخرین سفارش‌های شما:</b>\n"]
        for r in rows:
            lines.append(f"#{r['id']} | {KIND_FA.get(r['kind'], r['kind'])} | {fmt(r['amount'])} تومان | "
                         f"{ORDER_STATUS_FA.get(r['status'], r['status'])}")
        await msg.reply_text("\n".join(lines), parse_mode=HTML, reply_markup=main_keyboard())
        return

    if text == BTN_PRICES:
        await msg.reply_text(price_list_text(), parse_mode=HTML,
                             reply_markup=Markup([[Btn("🛒 خرید سرویس", callback_data="buy")],
                                                  [Btn("🔙 بازگشت به منوی اصلی", callback_data="menu")]]))
        return

    if text == BTN_DAILY:
        st, bonus, streak = claim_daily(user_id)
        if st == "off":
            await msg.reply_text("هدیه روزانه فعلاً غیرفعال است.", reply_markup=main_keyboard())
        elif st == "already":
            await msg.reply_text(f"✅ هدیه امروز را گرفته‌اید. فردا دوباره سر بزنید!\n🔥 روز {streak} از ۷ (روز هفتم هدیه ویژه 5 برابر دارد)",
                                 reply_markup=main_keyboard())
        else:
            bar = "🟩" * streak + "⬜️" * (7 - streak)
            await msg.reply_text(f"🎁 {fmt(bonus)} تومان به کیف پول شما اضافه شد!\n\n{bar}\n🔥 روز {streak} از ۷\n"
                                 f"💰 موجودی: {fmt(get_balance(user_id))} تومان", reply_markup=main_keyboard())
        return

    if text == BTN_CREDIT:
        spent, lname, lpct, nxt = level_info(user_id)
        lvl = f"🏆 سطح شما: {lname}" + (f" (تخفیف دائمی {lpct}٪)" if lpct else "")
        if nxt:
            lvl += f"\n📈 تا سطح {nxt[1]}: {fmt(max(nxt[0] - spent, 0))} تومان خرید دیگر"
        credit_text = ("👤 <b>اطلاعات حساب کاربری شما:</b>\n\n"
                       f"💰 موجودی: {fmt(get_balance(user_id))} تومان\n{lvl}\n"
                       f"🆔 آیدی عددی: <code>{user_id}</code>\n📦 تعداد سرویس‌ها: {len(user_services(user_id))}\n"
                       f"📅 تاریخ ورود به بات: {esc(get_join_date(user_id))}\n\n🕒 {now_str()}")
        await msg.reply_text(credit_text, parse_mode=HTML, reply_markup=credit_kb(user_id))
        return

    if text == BTN_HELP:
        await msg.reply_text("لطفا یک گزینه را انتخاب نمایید:", reply_markup=help_menu())
        return

    if text == BTN_SUPPORT:
        await msg.reply_text(SUPPORT_TEXT, reply_markup=support_kb())
        return

    if text == BTN_INVITE:
        link = f"https://t.me/{context.bot.username}?start=ref_{user_id}"
        pct = sint("referral_percent")
        body = (f"🎁 با معرفی دوستان با لینک زیر، به ازای هر خرید موفق آن‌ها {pct}٪ به کیف پول شما واریز می‌شود "
                f"(برای همیشه!).\n\n" if pct else "")
        await msg.reply_text("✨ دریافت سرویس‌های ایمن و پر سرعت بدون معطلی از طریق ربات\n\n" + body +
                             f"📊 افراد وارد شده با لینک شما: {referral_count(user_id)}\n"
                             f"💵 درآمد شما از دعوت‌ها: {fmt(referral_earnings(user_id))} تومان\n\n"
                             f"👇 لینک اختصاصی دعوت شما:\n{link}", reply_markup=back_menu_kb())
        return

    if text == BTN_AGENT:
        context.user_data["state"] = "waiting_agent_msg"
        await msg.reply_text(f"🤝 {SUPPORT_TEXT.split(chr(10))[0]}", reply_markup=back_menu_kb())
        return

    await msg.reply_text("یکی از گزینه‌های منو رو انتخاب کن 👇", reply_markup=main_keyboard())


async def start_credit_order(target, context, user_id, amount, edit=False):
    trim_open_orders(user_id, 2)
    oid, final_amount = create_card_order(user_id, "credit", amount)
    context.user_data["order_id"] = oid
    bonus_note = ""
    pct = sint("topup_bonus_percent")
    if pct and final_amount >= TOPUP_BONUS_MIN:
        bonus_note = f"\n\n🎁 هدیه شارژ: {fmt(final_amount * pct // 100)} تومان اعتبار اضافه"
    text = pay_text(final_amount) + bonus_note
    if edit:
        await target.edit_message_text(text, parse_mode=HTML, reply_markup=pay_markup(final_amount))
    else:
        await target.reply_text(text, parse_mode=HTML, reply_markup=pay_markup(final_amount))


_TEST_BUSY = set()


async def handle_test_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.message
    if sint("test_enabled") != 1:
        await msg.reply_text("🧪 اکانت تست فعلاً غیرفعال است.")
        return
    if user_id in _TEST_BUSY:
        await msg.reply_text("⏳ اکانت تست شما در حال ساخت است، لطفاً چند لحظه صبر کنید.")
        return
    cap = sint("test_daily_cap")
    if cap and await asyncio.to_thread(tests_today) >= cap:
        await msg.reply_text("⏳ ظرفیت اکانت تست امروز تکمیل شده است. فردا دوباره تلاش کنید.")
        return
    if not claim_test(user_id):
        await msg.reply_text("❌ شما قبلاً اکانت تست گرفته‌اید. هر کاربر فقط یک بار می‌تواند تست بگیرد.")
        return
    _TEST_BUSY.add(user_id)
    try:
        await msg.reply_text("⏳ در حال ساخت اکانت تست...")
        username = f"test_{user_id}_{secrets.token_hex(2)}"
        ok, result = await panel_create_user(username, gb=0.1, days=1, tag=f"test:{user_id}")
        if ok:
            try:
                add_service(user_id, username, TEST_TITLE, 0.1, 1, result)
            except Exception as e:
                logger.error(f"ثبت تست: {e}")
            try:
                await send_service_to_user(context.bot, user_id, username, result, 1, 0.1)
            except Exception as e:
                logger.error(f"ارسال تست: {e}")
                await msg.reply_text(f"اکانت تست ساخته شد:\n<code>{esc(result)}</code>", parse_mode=HTML)
        else:
            unclaim_test(user_id)
            await msg.reply_text(f"❌ متاسفانه مشکلی پیش اومد. چند دقیقه دیگه دوباره امتحان کن یا با پشتیبانی تماس بگیر: {SUPPORT}")
            logger.error(f"خطای ساخت تست: {result}")
            await notify_admin(context.bot, f"⚠️ خطا در ساخت تست برای {user_id}:\n{result}")
    finally:
        _TEST_BUSY.discard(user_id)


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    user_id = q.from_user.id

    if data.startswith(("approve_", "reject_")):
        await handle_admin_decision(update, context, data)
        return

    if data.startswith("adm_"):
        if user_id != ADMIN_ID:
            await ack(q, "⛔️", True)
            return
        await ack(q)
        key = data[4:]
        if key == "stats":
            txt = stats_text()
        elif key == "sales":
            txt = sales_text()
        elif key == "orders":
            txt = orders_text()
        elif key == "tickets":
            txt = tickets_text()
        elif key == "top":
            txt = top_referrers_text()
        elif key == "settings":
            txt = settings_text()
        elif key == "expiring":
            txt = await expiring_text()
        elif key == "panel":
            try:
                r = await panel("GET", "/api/groups")
                txt = f"🟢 پنل پاسخ داد (کد {r.status_code})"
            except Exception as e:
                txt = f"🔴 اتصال به پنل ناموفق: {e}"
        elif key == "export":
            try:
                b = await asyncio.to_thread(export_orders_csv)
                await context.bot.send_document(ADMIN_ID, document=b, filename="orders.csv",
                                                caption=f"📤 خروجی سفارش‌ها {now_str()}")
                return
            except Exception as e:
                txt = f"❌ خروجی ناموفق: {e}"
        elif key == "backup":
            try:
                path = await asyncio.to_thread(do_backup)
                with open(path, "rb") as f:
                    await context.bot.send_document(ADMIN_ID, f, caption=f"💾 بکاپ {now_str()}")
                return
            except Exception as e:
                txt = f"❌ بکاپ ناموفق: {e}"
        else:
            return
        await q.message.reply_text(txt)
        return

    if data == "check_join":
        if user_id != ADMIN_ID and is_banned(user_id):
            await ack(q, BANNED_TEXT, True)
            return
        if await is_member(context.bot, user_id):
            await ack(q)
            try:
                await q.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=user_id, text=welcome_text(), reply_markup=main_keyboard())
        else:
            await ack(q, "هنوز عضو نشدی! اول عضو کانال شو.", True)
        return

    if await gate(update, context, user_id):
        await ack(q)
        return

    if data == "send_receipt":
        oid = context.user_data.get("order_id")
        o = get_order(oid) if oid else None
        if not o or o["user_id"] != user_id or o["status"] != "awaiting_receipt":
            await ack(q, "این سفارش قبلاً انجام شده یا منقضی شده است.", True)
            return
        await ack(q)
        context.user_data["state"] = "waiting_receipt"
        await context.bot.send_message(chat_id=user_id, text="👇 لطفا تصویر رسید پرداخت خود را ارسال نمایید:",
                                       reply_markup=cancel_receipt_keyboard())
        return

    if data.startswith("serv_"):
        svc = find_service(data[5:], user_id)
        if not svc:
            await ack(q, "سرویس مورد نظر پیدا نشد!", True)
            return
        await ack(q)
        info = await panel_get_user(svc["username"])
        text, markup = service_view(svc, info, bool(last_plan(svc["username"], user_id)))
        await safe_edit(q, text, parse_mode=HTML, reply_markup=markup)
        return

    if data.startswith(("stop_", "active_")):
        make_active = data.startswith("active_")
        svc = find_service(data.split("_", 1)[1], user_id)
        if not svc:
            await ack(q, "سرویس پیدا نشد!", True)
            return
        info = await panel_get_user(svc["username"])
        if info is None:
            await ack(q, "❌ پنل در دسترس نیست. بعداً تلاش کنید.", True)
            return
        st = info.get("status")
        if make_active:
            if st != "disabled":
                await ack(q, "فقط سرویسِ متوقف‌شده قابل فعالسازی است.", True)
                return
            if not quota_valid(info):
                await ack(q, "زمان یا حجم این سرویس تمام شده؛ لطفاً آن را تمدید کنید.", True)
                return
        elif st != "active":
            await ack(q, "این سرویس فعال نیست.", True)
            return
        if await panel_change_status(svc["username"], "active" if make_active else "disabled"):
            text, markup = service_view(svc, await panel_get_user(svc["username"]),
                                        bool(last_plan(svc["username"], user_id)))
            await safe_edit(q, text, parse_mode=HTML, reply_markup=markup)
            await ack(q, "سرویس فعال شد!" if make_active
                      else "امکان اتصال به این اشتراک به طور موقت غیر فعال شد!", True)
        else:
            await ack(q, "❌ خطا در تغییر وضعیت سرویس.", True)
        return

    if data.startswith("reset_do_"):
        svc = find_service(data[9:], user_id)
        if not svc:
            await ack(q, "سرویس پیدا نشد!", True)
            return
        new_sub = await panel_revoke_sub(svc["username"])
        if not new_sub:
            await ack(q, "❌ خطا در دریافت لینک جدید از پنل.", True)
            return
        await ack(q)
        update_service_link(svc["username"], new_sub)
        try:
            await q.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=user_id, parse_mode=HTML,
                                       text=f"✅ لینک جدید شما با موفقیت ساخته شد:\n\n<code>{esc(new_sub)}</code>")
        await context.bot.send_photo(chat_id=user_id, photo=InputFile(generate_qr_buffer(new_sub), filename="new_qr.png"),
                                     caption=f"📥 QR Code لینک جدید برای کاربر: <code>{esc(svc['username'])}</code>",
                                     parse_mode=HTML)
        return

    if data.startswith("qrn_"):
        svc = find_service(data[4:], user_id)
        if not svc:
            await ack(q, "سرویس پیدا نشد!", True)
            return
        plan = last_plan(svc["username"], user_id)
        if not plan or plan["dk"] not in DURATIONS or plan["vk"] not in VOLUMES:
            await ack(q, "پلن قبلی پیدا نشد؛ از دکمه تمدید معمولی استفاده کنید.", True)
            return
        await ack(q)
        order = {"kind": "renew", "username": svc["username"], "dk": plan["dk"], "vk": plan["vk"]}
        context.user_data["order"] = order
        context.user_data.pop("state", None)
        text = order_text(user_id, order)
        try:
            await q.edit_message_text(text, parse_mode=HTML, reply_markup=confirm_menu(order))
        except BadRequest:
            await context.bot.send_message(chat_id=user_id, text=text, parse_mode=HTML,
                                           reply_markup=confirm_menu(order))
        return

    if data.startswith("rn_"):
        svc = find_service(data[3:], user_id)
        if not svc:
            await ack(q, "سرویس پیدا نشد!", True)
            return
        await ack(q)
        context.user_data["order"] = {"kind": "renew", "username": svc["username"]}
        context.user_data.pop("state", None)
        text = f"♻️ تمدید سرویس <code>{esc(svc['username'])}</code>\n\n⏳ انتخاب مدت زمان:"
        try:
            await q.edit_message_text(text, parse_mode=HTML, reply_markup=duration_menu())
        except BadRequest:
            await context.bot.send_message(chat_id=user_id, text=text, parse_mode=HTML, reply_markup=duration_menu())
        return

    if data in ("paycard", "paywallet"):
        order = context.user_data.get("order")
        if not valid_order(order):
            await ack(q, "نشست منقضی شد. دوباره از منو شروع کن.", True)
            return
        if throttled(user_id, "pay", 3):
            await ack(q)
            return
        info = price_info(user_id, order)
        final = info["final"]

        if data == "paycard" and order.get("oid"):
            prev = get_order(order["oid"])
            if prev and prev["user_id"] == user_id and prev["status"] == "awaiting_receipt" \
                    and order.get("final") == final:
                await ack(q)
                context.user_data["order_id"] = prev["id"]
                await q.edit_message_text(pay_text(prev["amount"]), parse_mode=HTML,
                                          reply_markup=pay_markup(prev["amount"]))
                return

        if order["kind"] == "new" and (username_taken_local(order["username"])
                                       or await panel_user_exists(order["username"]) is True):
            await ack(q, "این نام کاربری در این فاصله گرفته شد. دوباره از منو شروع کن.", True)
            return
        if order["kind"] == "renew" and not find_service(order["username"], user_id):
            await ack(q, "سرویس پیدا نشد.", True)
            return

        code = order.get("discount_code")
        if code and not consume_discount(code, user_id):
            order.pop("discount_code", None)
            order.pop("discount_percent", None)
            await ack(q, "این کد تخفیف دیگر معتبر نیست.", True)
            await show_order_confirm(q, user_id, order)
            return

        if data == "paycard":
            await ack(q)
            trim_open_orders(user_id, 2)
            oid, amount = create_card_order(user_id, order["kind"], final, order["dk"], order["vk"],
                                            order["username"], code)
            order["oid"], order["final"] = oid, final
            context.user_data["order"] = order
            context.user_data["order_id"] = oid
            await q.edit_message_text(pay_text(amount), parse_mode=HTML, reply_markup=pay_markup(amount))
            return

        if not try_deduct(user_id, final, "خرید سرویس"):
            release_discount(code, user_id)
            await ack(q, f"موجودی کافی نیست.\nموجودی شما: {fmt(get_balance(user_id))} تومان", True)
            return
        try:
            oid = create_order(user_id, order["kind"], final, order["dk"], order["vk"], order["username"],
                               code, "wallet", "processing")
        except Exception:
            logger.exception("create_order wallet")
            add_balance(user_id, final, "بازگشت وجه (خطا)")
            release_discount(code, user_id)
            await ack(q, "❌ خطای داخلی؛ مبلغ برگشت داده شد. دوباره تلاش کنید.", True)
            return
        await ack(q, "⏳ در حال انجام...")
        ok, err = await run_fulfill(context.bot, oid)
        if ok:
            clear_flow(context)
            try:
                await q.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=user_id, text="✅ پرداخت از موجودی انجام شد.",
                                           reply_markup=main_keyboard())
        else:
            if claim_order(oid, "processing", "failed"):
                add_balance(user_id, final, "بازگشت وجه (ساخت سرویس ناموفق)")
                release_discount(code, user_id)
            await notify_admin(context.bot, f"⚠️ سفارش کیف پول #{oid} کاربر {user_id} ناموفق بود (پول برگشت داده شد):\n{err}")
            await q.edit_message_text(f"❌ ساخت سرویس انجام نشد و مبلغ به موجودی شما برگشت. لطفاً بعداً تلاش کنید یا با {SUPPORT} تماس بگیرید.",
                                      reply_markup=back_menu_kb())
        return

    await ack(q)

    if data == "none":
        return

    if data == "menu":
        clear_flow(context)
        try:
            await q.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=user_id, text=welcome_text(), reply_markup=main_keyboard())
        return

    if data == "buy":
        context.user_data["order"] = {"kind": "new"}
        context.user_data.pop("state", None)
        await q.message.reply_text("⏳ انتخاب مدت زمان:", reply_markup=duration_menu())
        return

    if data == "ticket_new":
        context.user_data["state"] = "waiting_ticket"
        await q.message.reply_text("🎫 مشکل یا سوال خود را در قالب یک پیام بنویسید و ارسال کنید:",
                                   reply_markup=back_menu_kb())
        return

    if data == "toggle_notify":
        on = toggle_notify(user_id)
        try:
            await q.edit_message_reply_markup(reply_markup=credit_kb(user_id))
        except BadRequest:
            pass
        await context.bot.send_message(chat_id=user_id,
                                       text="🔔 یادآوری‌ها روشن شد." if on else "🔕 یادآوری‌ها خاموش شد.")
        return

    if data == "wlog":
        rows = wallet_history(user_id)
        if not rows:
            await q.message.reply_text("هنوز تراکنشی ندارید.")
            return
        lines = ["📜 <b>آخرین تراکنش‌های کیف پول:</b>\n"]
        for r in rows:
            sign = "➕" if r["amount"] > 0 else "➖"
            lines.append(f"{sign} {fmt(abs(r['amount']))} تومان — {esc(r['reason'] or '')} ({esc(r['created_at'])})")
        await q.message.reply_text("\n".join(lines), parse_mode=HTML)
        return

    if data == "faq":
        await q.edit_message_text(FAQ_TEXT, reply_markup=Markup([
            [Btn("💬 ارسال پیام به پشتیبانی", url=f"https://t.me/{SUPPORT.lstrip('@')}")],
            [Btn("🔙 بازگشت به پشتیبانی", callback_data="back_to_support")]]))
        return

    if data == "back_to_support":
        await q.edit_message_text(SUPPORT_TEXT, reply_markup=support_kb())
        return

    if data == "add_credit":
        amts = [(1000000, 750000), (500000, 250000), (200000, 150000), (125000, 100000), (75000, 50000)]
        rows = [[Btn(f"{fmt(a)} تومان", callback_data=f"payamt_{a}"),
                 Btn(f"{fmt(b)} تومان", callback_data=f"payamt_{b}")] for a, b in amts]
        rows.append([Btn("✍️ رقم دلخواه", callback_data="payamt_custom")])
        rows.append([Btn("❌ انصراف", callback_data="menu")])
        pct = sint("topup_bonus_percent")
        note = f"\n\n🎁 شارژ بالای {fmt(TOPUP_BONUS_MIN)} تومان: {pct}٪ اعتبار هدیه" if pct else ""
        await q.edit_message_text("💰 مبلغ مورد نظر را برای شارژ کیف پول انتخاب کنید:" + note,
                                  reply_markup=Markup(rows))
        return

    if data.startswith("payamt_"):
        key = data[7:]
        if key == "custom":
            context.user_data["state"] = "waiting_custom_credit"
            await q.edit_message_text("💸 مبلغ را به تومان وارد کنید (حداقل 10,000):",
                                      reply_markup=Markup([[Btn("🔙 بازگشت", callback_data="add_credit")]]))
            return
        try:
            amount = int(key)
        except ValueError:
            return
        if amount < 10000 or amount > MAX_WALLET_TOPUP:
            return
        if throttled(user_id, "topup", 3):
            return
        await start_credit_order(q, context, user_id, amount, edit=True)
        return

    if data == "gift_code":
        context.user_data["state"] = "waiting_gift_code"
        await q.message.reply_text("🎁 کد هدیه خود را وارد کنید:", reply_markup=back_menu_kb())
        return

    if data == "apply_discount":
        if not valid_order(context.user_data.get("order")):
            await q.edit_message_text("نشست منقضی شد. دوباره از منو شروع کن.", reply_markup=back_menu_kb())
            return
        context.user_data["state"] = "waiting_discount"
        await q.message.reply_text("🎁 کد تخفیف خود را وارد کنید:")
        return

    if data.startswith("reset_confirm_"):
        uname = data[14:]
        if not find_service(uname, user_id):
            return
        await q.edit_message_text(
            "⚠️ هشدار: درصورت تایید، فیلترشکن فعلی شما بلافاصله از کار می افتد و برای استفاده از باقیمانده "
            "سرویس خود باید لینک جدید که در ادامه ارسال می گردد را از نو وارد نرم افزار کنید. آیا مطمئن هستید؟",
            reply_markup=Markup([[Btn("✅ تایید", callback_data=f"reset_do_{uname}"),
                                  Btn("❌ انصراف", callback_data=f"serv_{uname}")]]))
        return

    if data == "back_to_services":
        await q.edit_message_text("🔹 برای مشاهده اطلاعات روی محصول کلیک کنید:",
                                  reply_markup=await services_kb(user_id, "serv_"))
        return

    if data.startswith("qr_"):
        svc = find_service(data[3:], user_id)
        if svc:
            await q.message.reply_photo(photo=InputFile(generate_qr_buffer(svc["link"]), filename="qr.png"),
                                        caption=f"📥 QR Code مربوط به نام کاربری: <code>{esc(svc['username'])}</code>",
                                        parse_mode=HTML)
        return

    if data.startswith("hlp_"):
        text = HELP_TEXTS.get(data[4:], "آموزش یافت نشد.")
        markup = Markup([[Btn("🔙 بازگشت به آموزش‌ها", callback_data="back_to_help")]])
        if q.message.photo:
            await q.message.reply_text(text, reply_markup=markup)
        else:
            await q.edit_message_text(text, reply_markup=markup)
        return

    if data == "back_to_help":
        await q.edit_message_text("لطفا یک گزینه را انتخاب نمایید:", reply_markup=help_menu())
        return

    if data == "dur_menu":
        await q.edit_message_text("⏳ انتخاب مدت زمان:", reply_markup=duration_menu())
        return

    if data.startswith("dur_"):
        dk = data[4:]
        if dk not in DURATIONS:
            return
        order = context.user_data.get("order") or {"kind": "new"}
        order["dk"] = dk
        context.user_data["order"] = order
        await q.edit_message_text(f"مدت انتخابی: {DURATIONS[dk][0]}\n\n📦 حالا حجم رو انتخاب کن:",
                                  reply_markup=volume_menu(dk))
        return

    if data.startswith("vol_"):
        parts = data.split("_", 2)
        if len(parts) != 3 or parts[1] not in DURATIONS or parts[2] not in VOLUMES:
            return
        _, dk, vk = parts
        order = context.user_data.get("order") or {"kind": "new"}
        order.update({"dk": dk, "vk": vk})
        for k in ("discount_code", "discount_percent", "oid", "final"):
            order.pop(k, None)
        context.user_data["order"] = order
        if order["kind"] == "renew" and order.get("username"):
            await show_order_confirm(q, user_id, order)
            return
        context.user_data["state"] = "waiting_username"
        await q.edit_message_text("✍️ سرویس مورد نظر انتخاب شد. لطفا یک نام کاربری با حروف کوچک لاتین (حداکثر 20 کاراکتر) وارد نمایید.")
        return


async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.message:
        return
    if await gate(update, context, user.id):
        return
    msg = update.message
    oid = context.user_data.get("order_id")
    o = get_order(oid) if oid else None

    if context.user_data.get("state") != "waiting_receipt" or not o or o["user_id"] != user.id:
        await msg.reply_text("لطفاً اول از منو سرویس مورد نظرتون رو انتخاب کنید و بعد از پرداخت، دکمه «ارسال رسید پرداخت» رو بزنید.",
                             reply_markup=main_keyboard())
        return
    if throttled(user.id, "receipt", 5):
        return
    if o["status"] != "awaiting_receipt":
        await msg.reply_text("این سفارش قبلاً ثبت یا انجام شده است.", reply_markup=main_keyboard())
        clear_flow(context)
        return

    is_photo = bool(msg.photo)
    f = msg.photo[-1] if is_photo else msg.document
    if receipt_used_before(f.file_unique_id):
        await msg.reply_text("❌ این رسید قبلاً ارسال شده است.", reply_markup=main_keyboard())
        return
    if not attach_receipt(oid, user.id, f.file_unique_id):
        await msg.reply_text("این سفارش قبلاً ثبت یا انجام شده است.", reply_markup=main_keyboard())
        clear_flow(context)
        return

    uname_txt = f"@{user.username}" if user.username else "بدون یوزرنیم"
    caption = (f"🧾 رسید جدید | سفارش #{oid}\n\n📌 نوع: {KIND_FA.get(o['kind'], o['kind'])}\n"
               f"👤 کاربر: {user.full_name} ({uname_txt})\n🆔 آیدی: {user.id}\n")
    if o["kind"] != "credit":
        title, _ = DURATIONS[o["dk"]]
        caption += f"👤 نام کاربری: {o['username']}\n⏳ مدت: {title}\n📦 حجم: {VOLUMES[o['vk']][0]} گیگ\n"
        if o["discount_code"]:
            caption += f"🎁 کد تخفیف: {o['discount_code']}\n"
    caption += f"💰 مبلغ: {fmt(o['amount'])} تومان"

    try:
        if is_photo:
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=f.file_id, caption=caption,
                                         reply_markup=admin_keyboard(oid))
        else:
            await context.bot.send_document(chat_id=ADMIN_ID, document=f.file_id, caption=caption,
                                            reply_markup=admin_keyboard(oid))
    except Exception as e:
        logger.error(f"ارسال رسید به ادمین ناموفق: {e}")
        revert_receipt(oid)
        await msg.reply_text("❌ ارسال رسید به مدیریت ناموفق بود. چند دقیقه بعد دوباره تلاش کنید.")
        return

    clear_flow(context)
    await msg.reply_text("✅ رسید پرداخت شما دریافت شد. اگر پرداخت شما شناسایی شود سفارش خودکار انجام می‌شود؛ "
                         "در غیر این صورت پس از بررسی ادمین انجام می‌گردد. لطفا از ارسال مجدد رسید خودداری نمایید.",
                         reply_markup=main_keyboard())


# ============================== دستورات ادمین ==============================
async def expiring_text() -> str:
    svcs = all_services()[:80]
    sem = asyncio.Semaphore(5)

    async def one(s):
        async with sem:
            return s, await panel_get_user(s["username"])

    res = await asyncio.gather(*(one(s) for s in svcs))
    now, lines = time.time(), []
    for s, info in res:
        if not info:
            continue
        exp = parse_expire(info.get("expire"))
        if exp and 0 < exp - now < 3 * 86400:
            lines.append(f"• {s['username']} | کاربر {s['user_id']} | {max(int((exp - now) // 86400), 0)} روز")
    return "\n".join(["⏰ نزدیک انقضا:"] + lines) if lines else "سرویسی نزدیک انقضا نیست."


async def admin_reply(update, context):
    """ریپلای ادمین روی پیام تیکت = ارسال پاسخ به کاربر؛ در غیر این صورت رفتار عادی."""
    msg = update.message
    src = msg.reply_to_message
    m = re.search(r"تیکت #(\d+)", (src.text or src.caption or "")) if src else None
    if not m:
        await text_router(update, context)
        return
    tid = int(m.group(1))
    t = get_ticket(tid)
    if not t:
        await msg.reply_text("تیکت پیدا نشد.")
        return
    ok = await safe_send(context.bot, t["user_id"], f"📩 پاسخ پشتیبانی (تیکت #{tid}):\n\n{msg.text}")
    if ok:
        set_ticket_status(tid, "answered")
        await msg.reply_text(f"✅ پاسخ تیکت #{tid} ارسال شد.")
    else:
        await msg.reply_text("❌ ارسال ناموفق (کاربر ربات را بلاک کرده است).")


async def cmd_admin(update, context):
    await update.message.reply_text("🛠 پنل مدیریت", reply_markup=admin_panel_kb())


async def cmd_adminhelp(update, context):
    await update.message.reply_text(
        "🛠 دستورات ادمین:\n\n/admin — پنل دکمه‌ای\n/stats /sales /orders /expiring /top\n"
        "/order ID — جزئیات سفارش\n/userinfo ID — اطلاعات کاربر\n/find USERNAME — صاحب سرویس\n"
        "/addbalance ID AMOUNT — تغییر موجودی (منفی هم می‌شود)\n"
        "/addservice ID GB DAYS — ساخت سرویس هدیه برای کاربر\n"
        "/sendto ID متن — پیام مستقیم\n/ban ID /unban ID\n/maintenance on|off\n/setcard شماره [نام]\n"
        "/set KEY VALUE — تنظیمات\n/settings — نمایش تنظیمات\n/setpromo متن — بنر خوشامد (حذف: -)\n"
        "/adddiscount CODE PERCENT [MAX]\n/discounts\n/deldiscount CODE\n/addgift CODE AMOUNT\n"
        "/force ORDER_ID — اجرای دستی سفارش منقضی/لغوشده\n"
        "/broadcast [all|buyers|nobuyers] متن (یا ریپلای روی پیام)\n"
        "/export — خروجی CSV سفارش‌ها\n/smstest متن — تست تایید خودکار\n/panelcheck\n/backup\n\n"
        "🎫 پاسخ تیکت: روی پیام تیکت ریپلای کنید.")


async def cmd_stats(update, context):
    await update.message.reply_text(stats_text())


async def cmd_sales(update, context):
    await update.message.reply_text(sales_text())


async def cmd_orders(update, context):
    await update.message.reply_text(orders_text())


async def cmd_expiring(update, context):
    await update.message.reply_text(await expiring_text())


async def cmd_top(update, context):
    await update.message.reply_text(top_referrers_text())


async def cmd_export(update, context):
    try:
        b = await asyncio.to_thread(export_orders_csv)
        await context.bot.send_document(ADMIN_ID, document=b, filename="orders.csv",
                                        caption=f"📤 خروجی سفارش‌ها {now_str()}")
    except Exception as e:
        await update.message.reply_text(f"❌ خروجی ناموفق: {e}")


async def cmd_settings(update, context):
    await update.message.reply_text(settings_text())


async def cmd_set(update, context):
    try:
        key, val = context.args[0], int(context.args[1])
        lo, hi = SETTING_LIMITS[key]
        if not lo <= val <= hi:
            raise ValueError
    except (IndexError, ValueError, KeyError):
        await update.message.reply_text("استفاده: /set KEY VALUE\n\n" + settings_text())
        return
    set_setting(key, val)
    await update.message.reply_text(f"✅ {key} = {val}")


async def cmd_setpromo(update, context):
    parts = (update.message.text or "").split(None, 1)
    if len(parts) < 2:
        await update.message.reply_text("استفاده: /setpromo متن بنر  (برای حذف: /setpromo -)")
        return
    set_setting("promo", "" if parts[1].strip() == "-" else parts[1].strip()[:300])
    await update.message.reply_text("✅ ذخیره شد.\n\n" + welcome_text())


async def cmd_order(update, context):
    try:
        o = get_order(int(context.args[0]))
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /order order_id")
        return
    if not o:
        await update.message.reply_text("سفارش پیدا نشد.")
        return
    created = datetime.fromtimestamp(o["created_at"], TZ).strftime("%Y/%m/%d %H:%M:%S")
    await update.message.reply_text(
        f"🧾 سفارش #{o['id']}\n👤 کاربر: {o['user_id']}\n📌 نوع: {KIND_FA.get(o['kind'], o['kind'])}\n"
        f"💰 مبلغ: {fmt(o['amount'])}\n🏦 روش: {o['method']}\n📍 وضعیت: {ORDER_STATUS_FA.get(o['status'], o['status'])}\n"
        f"👤 نام کاربری: {o['username'] or '-'}\n🏷 کد تخفیف: {o['discount_code'] or '-'}\n🕒 {created}")


async def cmd_userinfo(update, context):
    try:
        uid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /userinfo user_id")
        return
    svcs = user_services(uid)
    spent, lname, _, _ = level_info(uid)
    lines = [f"🆔 {uid}", f"📅 ورود: {get_join_date(uid)}", f"💰 موجودی: {fmt(get_balance(uid))}",
             f"🏆 سطح: {lname} (جمع خرید {fmt(spent)})", f"⛔️ مسدود: {'بله' if is_banned(uid) else 'خیر'}",
             f"👥 دعوت‌ها: {referral_count(uid)}", f"📦 سرویس‌ها: {len(svcs)}"]
    for s in svcs:
        lines.append(f" • {s['username']} ({s['title']})")
    await update.message.reply_text("\n".join(lines))


async def cmd_find(update, context):
    if not context.args:
        await update.message.reply_text("استفاده: /find username")
        return
    s = find_service_any(context.args[0].lower())
    await update.message.reply_text(f"👤 صاحب: {s['user_id']} | {s['title']}" if s else "پیدا نشد.")


async def cmd_addbalance(update, context):
    try:
        uid, amount = int(context.args[0]), int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /addbalance user_id amount")
        return
    if amount < 0:
        if not try_deduct(uid, -amount, "کسر توسط مدیریت"):
            await update.message.reply_text("موجودی کاربر کافی نیست.")
            return
    else:
        add_balance(uid, amount, "افزایش توسط مدیریت")
        await safe_send(context.bot, uid, f"💰 {fmt(amount)} تومان به موجودی شما اضافه شد.")
    await update.message.reply_text(f"✅ انجام شد. موجودی فعلی: {fmt(get_balance(uid))}")


async def cmd_addservice(update, context):
    try:
        uid, gb, days = int(context.args[0]), float(context.args[1]), int(context.args[2])
        if gb <= 0 or days <= 0 or gb > 1000 or days > 3650:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /addservice user_id GB DAYS")
        return
    username = f"u{uid}_{secrets.token_hex(2)}"[:20]
    await update.message.reply_text("⏳ در حال ساخت...")
    ok, res = await panel_create_user(username, gb, days, tag=f"admin:{secrets.token_hex(3)}")
    if not ok:
        await update.message.reply_text(f"❌ ناموفق:\n{res}")
        return
    add_user(uid)
    add_service(uid, username, GIFT_TITLE, gb, days, res)
    try:
        await send_service_to_user(context.bot, uid, username, res, days, gb)
        await update.message.reply_text(f"✅ سرویس {username} ساخته و برای کاربر ارسال شد.")
    except Exception as e:
        await update.message.reply_text(f"✅ ساخته شد ولی ارسال به کاربر ناموفق بود ({e}).\nلینک:\n{res}")


async def cmd_sendto(update, context):
    parts = (update.message.text or "").split(None, 2)
    if len(parts) < 3 or not parts[1].isdigit():
        await update.message.reply_text("استفاده: /sendto user_id متن پیام")
        return
    ok = await safe_send(context.bot, int(parts[1]), parts[2])
    await update.message.reply_text("✅ ارسال شد." if ok else "❌ ارسال ناموفق (بلاک یا آیدی اشتباه).")


async def cmd_ban(update, context):
    try:
        uid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /ban user_id")
        return
    if uid == ADMIN_ID:
        await update.message.reply_text("نمی‌شود ادمین را مسدود کرد.")
        return
    set_ban(uid, True)
    await update.message.reply_text(f"⛔️ کاربر {uid} مسدود شد.")


async def cmd_unban(update, context):
    try:
        uid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /unban user_id")
        return
    set_ban(uid, False)
    await update.message.reply_text(f"✅ کاربر {uid} آزاد شد.")


async def cmd_maintenance(update, context):
    if not context.args or context.args[0] not in ("on", "off"):
        await update.message.reply_text("استفاده: /maintenance on|off")
        return
    set_setting("maintenance", "1" if context.args[0] == "on" else "0")
    await update.message.reply_text("🛠 حالت تعمیر " + ("فعال شد." if context.args[0] == "on" else "غیرفعال شد."))


async def cmd_setcard(update, context):
    if not context.args:
        await update.message.reply_text(f"💳 کارت فعلی: {card_number()}\nنام: {card_owner() or '-'}\n\n"
                                        "تغییر: /setcard 6037-1234-5678-9012 نام صاحب کارت")
        return
    set_setting("card_number", context.args[0])
    owner = " ".join(context.args[1:]).strip()
    if owner:
        set_setting("card_owner", owner)
    await update.message.reply_text(f"✅ کارت پرداخت تغییر کرد:\n{card_number()}\n{card_owner() or ''}")


async def cmd_adddiscount(update, context):
    try:
        code, pct = context.args[0].upper(), int(context.args[1])
        mx = int(context.args[2]) if len(context.args) > 2 else 0
        if not 1 <= pct <= 60:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /adddiscount CODE PERCENT(1-60) [MAXUSES (0=نامحدود)]")
        return
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO discounts(code, percent, max_uses, used, active) VALUES(?,?,?,0,1)",
                  (code, pct, mx))
        c.execute("DELETE FROM discount_users WHERE code=?", (code,))
    await update.message.reply_text(f"✅ کد {code} با {pct}٪ تخفیف ساخته شد (هر کاربر یک بار).")


async def cmd_discounts(update, context):
    with conn() as c:
        rows = c.execute("SELECT * FROM discounts ORDER BY rowid DESC LIMIT 50").fetchall()
    await update.message.reply_text("\n".join(
        f"{r['code']} | {r['percent']}٪ | استفاده: {r['used']}/{r['max_uses'] or '∞'}" for r in rows)
        if rows else "کدی وجود ندارد.")


async def cmd_deldiscount(update, context):
    if not context.args:
        await update.message.reply_text("استفاده: /deldiscount CODE")
        return
    with conn() as c:
        cur = c.execute("DELETE FROM discounts WHERE code=?", (context.args[0].upper(),))
    await update.message.reply_text("✅ حذف شد." if cur.rowcount else "کد پیدا نشد.")


async def cmd_addgift(update, context):
    try:
        code, amount = context.args[0].upper(), int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /addgift CODE AMOUNT")
        return
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO gifts(code, amount, used_by) VALUES(?,?,NULL)", (code, amount))
    await update.message.reply_text(f"✅ کد هدیه {code} به مبلغ {fmt(amount)} ساخته شد.")


async def cmd_force(update, context):
    try:
        oid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /force order_id")
        return
    o = get_order(oid)
    if not o:
        await update.message.reply_text("سفارش پیدا نشد.")
        return
    if not claim_order_any(oid, ("expired", "cancelled", "awaiting_receipt", "pending", "failed", "rejected"),
                           "processing"):
        await update.message.reply_text(f"وضعیت سفارش ({o['status']}) قابل اجرا نیست.")
        return
    await update.message.reply_text("⏳ در حال انجام...")
    ok, err = await run_fulfill(context.bot, oid)
    if ok:
        await update.message.reply_text(f"✅ سفارش #{oid} انجام شد.")
    else:
        claim_order(oid, "processing", "pending")
        await update.message.reply_text(f"❌ ناموفق:\n{err}")


async def cmd_smstest(update, context):
    parts = (update.message.text or "").split(None, 1)
    if len(parts) < 2:
        await update.message.reply_text("استفاده: /smstest متن پیام بانک")
        return
    await update.message.reply_text(f"نتیجه: {await process_sms(context.bot, parts[1], '')}")


async def cmd_panelcheck(update, context):
    try:
        r = await panel("GET", "/api/groups")
        await update.message.reply_text(f"🟢 پنل پاسخ داد (کد {r.status_code})")
    except Exception as e:
        await update.message.reply_text(f"🔴 اتصال به پنل ناموفق: {e}")


async def cmd_backup(update, context):
    try:
        path = await asyncio.to_thread(do_backup)
        with open(path, "rb") as f:
            await context.bot.send_document(ADMIN_ID, f, caption=f"💾 بکاپ {now_str()}")
    except Exception as e:
        await update.message.reply_text(f"❌ بکاپ ناموفق: {e}")


_BG_TASKS = set()


async def do_broadcast(bot, chat_id, text, src_chat, src_msg, segment):
    ok = fail = 0
    for uid in all_user_ids(segment):
        try:
            if text is not None:
                await bot.send_message(uid, text)
            else:
                await bot.copy_message(uid, src_chat, src_msg)
            ok += 1
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await safe_send(bot, chat_id, f"📣 ارسال همگانی تمام شد.\n✅ موفق: {ok} | ❌ ناموفق: {fail}")


async def cmd_broadcast(update, context):
    msg = update.message
    src = msg.reply_to_message
    parts = (msg.text or "").split(None, 1)
    rest = parts[1] if len(parts) > 1 else ""
    segment = "all"
    first = rest.split(None, 1)
    if first and first[0] in SEGMENTS:
        segment = first[0]
        rest = first[1] if len(first) > 1 else ""
    if not src and not rest.strip():
        await msg.reply_text("استفاده: /broadcast [all|buyers|nobuyers] متن   یا ریپلای روی یک پیام")
        return
    text = None if src else rest
    n = len(all_user_ids(segment))
    await msg.reply_text(f"📣 ارسال همگانی برای {n} کاربر ({segment}) شروع شد؛ پایان آن را اعلام می‌کنم.")
    t = asyncio.create_task(do_broadcast(context.bot, msg.chat_id, text, msg.chat_id,
                                         src.message_id if src else None, segment))
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)


# ============================== حلقه‌های پس‌زمینه ==============================
def do_backup() -> str:
    path = os.path.join(DATA_DIR, f"backup_{datetime.now().strftime('%a')}.db")
    src, dst = sqlite3.connect(DB_PATH), sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return path


async def backup_loop(app):
    while True:
        await asyncio.sleep(12 * 3600)
        try:
            path = await asyncio.to_thread(do_backup)
            with open(path, "rb") as f:
                await app.bot.send_document(ADMIN_ID, f, caption=f"💾 بکاپ {now_str()}")
        except Exception as e:
            logger.error(f"بکاپ ناموفق: {e}")


_STUCK_SEEN = set()


async def expire_loop(app):
    while True:
        await asyncio.sleep(600)
        try:
            n = await asyncio.to_thread(expire_old_orders)
            if n:
                logger.info(f"{n} سفارش منقضی شد")
            for r in await asyncio.to_thread(stuck_orders):
                if r["id"] not in _STUCK_SEEN:
                    _STUCK_SEEN.add(r["id"])
                    await notify_admin(app.bot, f"⚠️ سفارش #{r['id']} (کاربر {r['user_id']}, {fmt(r['amount'])} تومان, "
                                                f"{r['method']}) بیش از ۱۵ دقیقه در حالت «در حال انجام» مانده؛ بررسی کنید.")
        except Exception as e:
            logger.error(f"expire_loop: {e}")


async def panel_watch_loop(app):
    """اگر پنل از دسترس خارج شد (یا کلید API نامعتبر شد) به ادمین خبر می‌دهد."""
    await asyncio.sleep(60)
    fails, alerted = 0, False
    while True:
        reason = ""
        try:
            r = await panel("GET", "/api/groups")
            ok = r.status_code < 500 and r.status_code not in (401, 403)
            if not ok:
                reason = f"کد {r.status_code}"
        except Exception as e:
            ok, reason = False, str(e)[:150]
        if ok:
            if alerted:
                await notify_admin(app.bot, "🟢 پنل دوباره در دسترس است.")
            fails, alerted = 0, False
        else:
            fails += 1
            if fails >= 2 and not alerted:
                alerted = True
                await notify_admin(app.bot, f"🔴 پنل پاسخ درست نمی‌دهد ({reason}). خریدها تا رفع مشکل ناموفق می‌شوند.")
        await asyncio.sleep(300)


async def daily_report_loop(app):
    await asyncio.sleep(600)
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            await notify_admin(app.bot, await asyncio.to_thread(sales_summary, 86400, "گزارش روزانه (۲۴ ساعت اخیر)"))
        except Exception as e:
            logger.error(f"daily_report: {e}")


async def run_reminders(bot):
    sem = asyncio.Semaphore(5)

    async def one(s):
        async with sem:
            uname = s["username"]
            if not notify_on(s["user_id"]):
                return
            info = await panel_get_user(uname)
            if not info:
                return
            kb = Markup([[Btn("♻️ تمدید سرویس", callback_data=f"rn_{uname}")]])
            st = info.get("status")
            if st in ("expired", "limited"):
                if not was_notified(uname, "expd"):
                    why = "زمان" if st == "expired" else "حجم"
                    if await safe_send(bot, s["user_id"],
                                       f"⛔️ {why} سرویس <code>{esc(uname)}</code> تمام شده است.\n"
                                       "برای وصل شدن دوباره، همین حالا تمدید کنید.", parse_mode=HTML, reply_markup=kb):
                        mark_notified(uname, "expd")
                return
            if st != "active":
                return
            now = time.time()
            exp = parse_expire(info.get("expire"))
            limit, used = int(info.get("data_limit") or 0), int(info.get("used_traffic") or 0)
            if exp and 0 < exp - now < REMIND_DAYS * 86400 and not was_notified(uname, "exp"):
                left = max(int((exp - now) // 86400), 0)
                if await safe_send(bot, s["user_id"],
                                   f"⏰ سرویس <code>{esc(uname)}</code> حدود {left} روز دیگر منقضی می‌شود.\n"
                                   "برای جلوگیری از قطعی، تمدید کنید (مدت باقی‌مانده نمی‌سوزد).",
                                   parse_mode=HTML, reply_markup=kb):
                    mark_notified(uname, "exp")
            if limit and used / limit >= REMIND_USAGE and not was_notified(uname, "vol"):
                if await safe_send(bot, s["user_id"],
                                   f"📉 بیش از {int(REMIND_USAGE * 100)}٪ حجم سرویس <code>{esc(uname)}</code> مصرف شده.\n"
                                   "برای افزودن حجم، تمدید کنید.", parse_mode=HTML, reply_markup=kb):
                    mark_notified(uname, "vol")

    await asyncio.gather(*(one(s) for s in all_services()), return_exceptions=True)


async def reminder_loop(app):
    await asyncio.sleep(180)
    while True:
        try:
            await run_reminders(app.bot)
        except Exception as e:
            logger.error(f"reminder_loop: {e}")
        await asyncio.sleep(6 * 3600)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, Conflict):
        logger.warning("Conflict: نمونه دیگری با همین توکن در حال اجراست")
        return
    logger.error("خطا:", exc_info=err)
    if isinstance(err, NetworkError):
        return
    if isinstance(err, BadRequest) and "not modified" in str(err).lower():
        return
    if not throttled(0, "err_notify", 60):
        await notify_admin(context.bot, f"⚠️ خطای ربات: {type(err).__name__}: {err}")
    if isinstance(update, Update) and update.effective_chat and update.effective_chat.type == "private":
        await safe_send(context.bot, update.effective_chat.id,
                        "❌ مشکلی پیش آمد. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.")


async def post_init(app: Application):
    global HTTP, _BOT, _LOOP, _SMS_LOCK
    HTTP = httpx.AsyncClient()
    _BOT = app.bot
    _LOOP = asyncio.get_running_loop()
    _SMS_LOCK = asyncio.Lock()
    try:
        await app.bot.set_my_short_description(f"فروشگاه {BRAND_TITLE} | تست رایگان | پشتیبانی ۲۴ ساعته")
        await app.bot.set_my_description(
            f"🚀 فروشگاه {BRAND_TITLE}\n\n✅ اتصال پرسرعت، پایدار و بدون قطعی\n🧪 تست رایگان قبل از خرید\n"
            "🎁 هدیه روزانه، کش‌بک و پاداش دعوت\n🛟 پشتیبانی ۲۴ ساعته با تیکت\n\nبرای شروع روی Start بزنید.")
    except Exception as e:
        logger.warning(f"set description: {e}")
    for coro in (backup_loop(app), expire_loop(app), reminder_loop(app), daily_report_loop(app),
                 panel_watch_loop(app)):
        t = asyncio.create_task(coro)
        _BG_TASKS.add(t)


async def post_shutdown(app: Application):
    for t in list(_BG_TASKS):
        t.cancel()
    _BG_TASKS.clear()
    if HTTP:
        await HTTP.aclose()


def main():
    init_db()
    if PORT:
        threading.Thread(target=run_web_server, daemon=True).start()
    if SMS_SECRET and not PORT:
        logger.warning("SMS_SECRET تنظیم شده ولی PORT نیست؛ وب‌هوک پیامک فعال نمی‌شود.")

    persistence = PicklePersistence(filepath=PERSIST_PATH, update_interval=10)
    app = (Application.builder().token(TOKEN).persistence(persistence).concurrent_updates(True)
           .post_init(post_init).post_shutdown(post_shutdown).build())

    private = filters.ChatType.PRIVATE
    admin_only = filters.User(ADMIN_ID) & private
    app.add_handler(CommandHandler("start", start, filters=private))
    admin_cmds = {
        "admin": cmd_admin, "adminhelp": cmd_adminhelp, "stats": cmd_stats, "sales": cmd_sales,
        "orders": cmd_orders, "expiring": cmd_expiring, "top": cmd_top, "export": cmd_export,
        "settings": cmd_settings, "set": cmd_set, "setpromo": cmd_setpromo, "order": cmd_order,
        "userinfo": cmd_userinfo, "find": cmd_find, "addbalance": cmd_addbalance,
        "addservice": cmd_addservice, "sendto": cmd_sendto, "ban": cmd_ban, "unban": cmd_unban,
        "maintenance": cmd_maintenance, "setcard": cmd_setcard, "adddiscount": cmd_adddiscount,
        "discounts": cmd_discounts, "deldiscount": cmd_deldiscount, "addgift": cmd_addgift,
        "force": cmd_force, "smstest": cmd_smstest, "panelcheck": cmd_panelcheck,
        "backup": cmd_backup, "broadcast": cmd_broadcast,
    }
    for name, fn in admin_cmds.items():
        app.add_handler(CommandHandler(name, fn, filters=admin_only))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(private & (filters.PHOTO | filters.Document.IMAGE), receipt_handler))
    app.add_handler(MessageHandler(admin_only & filters.REPLY & filters.TEXT & ~filters.COMMAND, admin_reply))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(on_error)

    logger.info("بات در حال اجراست...")
    app.run_polling(bootstrap_retries=-1)


if __name__ == "__main__":
    main()
