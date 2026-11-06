import asyncio
import html
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO

import httpx
import qrcode
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, InputFile, CopyTextButton)
from telegram.constants import ParseMode
from telegram.error import RetryAfter, NetworkError
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, PicklePersistence, filters, ContextTypes)

# ============================== لاگ ==============================
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
# جلوگیری از چاپ آدرس‌های حاوی توکن در لاگ
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ============================ تنظیمات ============================
def env_required(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"متغیر محیطی {name} تنظیم نشده است!")
    return v


TOKEN = env_required("BOT_TOKEN")
ADMIN_ID = int(env_required("ADMIN_ID"))
CARD_NUMBER = env_required("CARD_NUMBER")
CARD_OWNER = env_required("CARD_OWNER")
CARD_DIGITS = "".join(c for c in CARD_NUMBER if c.isdigit())
SUPPORT = env_required("SUPPORT_USERNAME")
CHANNEL = env_required("CHANNEL_USERNAME")
PANEL_URL = env_required("PANEL_URL").rstrip("/")
PANEL_API_KEY = env_required("PANEL_API_KEY")
PANEL_GROUP_NAME = os.environ.get("PANEL_GROUP_ID", "group1")
BRAND = os.environ.get("BRAND_NAME", "SpeedUR")
DATA_DIR = os.environ.get("DATA_DIR", ".")
PORT = os.environ.get("PORT")  # فقط اگر روی Render و مشابه اجرا می‌کنی
REFERRAL_PERCENT = int(os.environ.get("REFERRAL_PERCENT", "5"))

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot.db")
PERSIST_PATH = os.path.join(DATA_DIR, "bot_state.pickle")

RECEIPT_TTL = 24 * 3600  # اعتبار سفارش منتظر رسید

# ============================== تعرفه ==============================
DURATIONS = {
    "d1": ("1 ماهه", 30),
    "d2": ("2 ماهه", 60),
    "d3": ("3 ماهه", 90),
    "d5": ("5 ماهه", 150),
    "d12": ("1 ساله", 365),
}

VOLUMES = {
    # TODO: قیمت 5 ماهه v10 با 3 ماهه یکسان است؛ چک کن اشتباه تایپی نباشد
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
WELCOME = f"🚀 به فروشگاه {BRAND} خوش آمدید\n\nیکی از گزینه‌ها رو انتخاب کن:"

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

SUPPORT_TEXT = ("لطفا در قالب یک پیام درخواست و نکات قابل ملاحظه درباره خود را ارسال کرده "
                "و سپس تا تماس از طرف ادمین منتظر بمانید")

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

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")
HTML = ParseMode.HTML


# ============================== ابزارهای کوچک ==============================
def esc(s) -> str:
    return html.escape(str(s), quote=False)


def fmt(n) -> str:
    return f"{int(n):,}"


def now_str() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_digits(s: str) -> str:
    return s.translate(_DIGITS)


_LAST = {}


def throttled(user_id, key, seconds) -> bool:
    """True یعنی خیلی زود تکرار شده."""
    now = time.time()
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


async def ack(q, text=None, alert=False):
    try:
        await q.answer(text, show_alert=alert)
    except Exception:
        pass


async def safe_send(bot, chat_id, text, **kw) -> bool:
    try:
        await bot.send_message(chat_id=chat_id, text=text, **kw)
        return True
    except Exception as e:
        logger.warning(f"ارسال پیام به {chat_id} ناموفق: {e}")
        return False


async def notify_admin(bot, text):
    await safe_send(bot, ADMIN_ID, text[:4000])


# ============================== دیتابیس (SQLite) ==============================
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY, joined_at TEXT NOT NULL, test_used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS balances(
    user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS services(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT NOT NULL UNIQUE,
    title TEXT, gb REAL, days INTEGER, link TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_services_user ON services(user_id);
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
    amount INTEGER NOT NULL, dk TEXT, vk TEXT, username TEXT, discount_code TEXT,
    method TEXT NOT NULL DEFAULT 'card', status TEXT NOT NULL, receipt_uid TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS referrals(
    referred_id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS discounts(
    code TEXT PRIMARY KEY, percent INTEGER NOT NULL, max_uses INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS gifts(
    code TEXT PRIMARY KEY, amount INTEGER NOT NULL, used_by INTEGER);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
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


# ---- کاربران ----
def add_user(uid) -> bool:
    with conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO users(user_id, joined_at) VALUES(?,?)", (uid, now_str()))
        return cur.rowcount == 1


def get_join_date(uid) -> str:
    with conn() as c:
        r = c.execute("SELECT joined_at FROM users WHERE user_id=?", (uid,)).fetchone()
    return r["joined_at"] if r else "نامشخص"


def all_user_ids():
    with conn() as c:
        return [r["user_id"] for r in c.execute("SELECT user_id FROM users")]


def claim_test(uid) -> bool:
    with conn() as c:
        cur = c.execute("UPDATE users SET test_used=1 WHERE user_id=? AND test_used=0", (uid,))
        return cur.rowcount == 1


def unclaim_test(uid):
    with conn() as c:
        c.execute("UPDATE users SET test_used=0 WHERE user_id=?", (uid,))


# ---- موجودی ----
def get_balance(uid) -> int:
    with conn() as c:
        r = c.execute("SELECT balance FROM balances WHERE user_id=?", (uid,)).fetchone()
    return int(r["balance"]) if r else 0


def add_balance(uid, amount):
    with conn() as c:
        c.execute("INSERT INTO balances(user_id, balance) VALUES(?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET balance=balance+excluded.balance",
                  (uid, int(amount)))


def try_deduct(uid, amount) -> bool:
    with conn() as c:
        cur = c.execute("UPDATE balances SET balance=balance-? WHERE user_id=? AND balance>=?",
                        (int(amount), uid, int(amount)))
        return cur.rowcount == 1


# ---- سرویس‌ها ----
def add_service(uid, username, title, gb, days, link):
    with conn() as c:
        c.execute("INSERT INTO services(user_id, username, title, gb, days, link, created_at) "
                  "VALUES(?,?,?,?,?,?,?)", (uid, username, title, gb, days, link, now_str()))


def user_services(uid):
    with conn() as c:
        return c.execute("SELECT * FROM services WHERE user_id=? ORDER BY id", (uid,)).fetchall()


def find_service(uname, uid):
    """فقط اگر سرویس مال همین کاربر باشد برمی‌گرداند."""
    with conn() as c:
        return c.execute("SELECT * FROM services WHERE username=? AND user_id=?", (uname, uid)).fetchone()


def username_taken_local(uname) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM services WHERE username=?", (uname,)).fetchone() is not None


def update_service_link(uname, link):
    with conn() as c:
        c.execute("UPDATE services SET link=? WHERE username=?", (link, uname))


# ---- سفارش‌ها ----
def create_order(uid, kind, amount, dk=None, vk=None, username=None,
                 discount_code=None, method="card", status="awaiting_receipt") -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO orders(user_id, kind, amount, dk, vk, username, discount_code, method, status, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (uid, kind, int(amount), dk, vk, username, discount_code, method, status, time.time()))
        return cur.lastrowid


def get_order(oid):
    with conn() as c:
        return c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()


def claim_order(oid, from_status, to_status) -> bool:
    with conn() as c:
        cur = c.execute("UPDATE orders SET status=? WHERE id=? AND status=?", (to_status, oid, from_status))
        return cur.rowcount == 1


def set_order_status(oid, status):
    with conn() as c:
        c.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))


def attach_receipt(oid, uid, file_uid) -> bool:
    with conn() as c:
        cur = c.execute("UPDATE orders SET status='pending', receipt_uid=? "
                        "WHERE id=? AND user_id=? AND status='awaiting_receipt'", (file_uid, oid, uid))
        return cur.rowcount == 1


def receipt_used_before(file_uid) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM orders WHERE receipt_uid=?", (file_uid,)).fetchone() is not None


# ---- دعوت ----
def add_referral(referrer, referred):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO referrals(referred_id, referrer_id) VALUES(?,?)", (referred, referrer))


def referral_count(uid) -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM referrals WHERE referrer_id=?", (uid,)).fetchone()["n"]


def get_referrer(uid):
    with conn() as c:
        r = c.execute("SELECT referrer_id FROM referrals WHERE referred_id=?", (uid,)).fetchone()
    return r["referrer_id"] if r else None


# ---- تخفیف و هدیه ----
def lookup_discount(code):
    with conn() as c:
        r = c.execute("SELECT percent FROM discounts WHERE code=? AND active=1 "
                      "AND (max_uses=0 OR used<max_uses)", (code,)).fetchone()
    return int(r["percent"]) if r else None


def consume_discount(code) -> bool:
    with conn() as c:
        cur = c.execute("UPDATE discounts SET used=used+1 WHERE code=? AND active=1 "
                        "AND (max_uses=0 OR used<max_uses)", (code,))
        return cur.rowcount == 1


def release_discount(code):
    if code:
        with conn() as c:
            c.execute("UPDATE discounts SET used=MAX(used-1,0) WHERE code=?", (code,))


def redeem_gift(code, uid) -> int:
    with conn() as c:
        row = c.execute("SELECT amount FROM gifts WHERE code=?", (code,)).fetchone()
        if not row:
            return 0
        cur = c.execute("UPDATE gifts SET used_by=? WHERE code=? AND used_by IS NULL", (uid, code))
        if cur.rowcount != 1:
            return 0
        c.execute("INSERT INTO balances(user_id, balance) VALUES(?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET balance=balance+excluded.balance",
                  (uid, row["amount"]))
        return int(row["amount"])


# ============================== پنل ==============================
HTTP: "httpx.AsyncClient | None" = None
_GROUP_CACHE = {"ids": None}


async def panel(method, path, **kw):
    return await HTTP.request(method, f"{PANEL_URL}{path}",
                              headers={"X-Api-Key": PANEL_API_KEY}, timeout=25.0, **kw)


def sub_url(d: dict) -> str:
    u = d.get("subscription_url") or ""
    if isinstance(u, str) and u.startswith("/"):
        u = PANEL_URL + u
    return u


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
    except Exception as e:
        logger.warning(f"خطا در گرفتن گروه‌ها: {e}")
    try:
        _GROUP_CACHE["ids"] = [int(PANEL_GROUP_NAME)]
    except ValueError:
        _GROUP_CACHE["ids"] = [1]
    return _GROUP_CACHE["ids"]


async def panel_get_user(username):
    try:
        r = await panel("GET", f"/api/user/{username}")
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.warning(f"panel_get_user: {e}")
    return None


async def panel_user_exists(username):
    """True / False / None (نامشخص)"""
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
        resp = await panel("POST", "/api/user", json=payload)
        if resp.status_code not in (200, 201):
            return False, f"خطای پنل ({resp.status_code}): {resp.text[:300]}"
        link = sub_url(resp.json())
        if not link:
            info = await panel_get_user(username)
            link = sub_url(info) if info else ""
        if not link:
            await panel_delete_user(username)  # جلوگیری از کاربر یتیم در پنل
            return False, "کاربر ساخته شد ولی لینک اشتراک برنگشت (کاربر حذف شد)."
        return True, link
    except Exception as e:
        return False, f"خطا در اتصال به پنل: {e}"


async def panel_renew(username: str, gb: float, days: int):
    info = await panel_get_user(username)
    if not info:
        return False, "کاربر در پنل پیدا نشد یا پنل در دسترس نیست."
    now = time.time()
    expire = parse_expire(info.get("expire"))
    limit = int(info.get("data_limit") or 0)
    used = int(info.get("used_traffic") or 0)
    active = (info.get("status") == "active"
              and (not expire or expire > now)
              and (not limit or used < limit))
    add_bytes = int(gb * 1024 ** 3)
    try:
        if active:
            new_expire = int((expire or now) + days * 86400)
            new_limit = (limit + add_bytes) if limit else add_bytes
        else:
            await panel("POST", f"/api/user/{username}/reset")  # صفر کردن مصرف
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
    """ساخت لینک اشتراک جدید (نه ریست مصرف)."""
    try:
        resp = await panel("POST", f"/api/user/{username}/revoke_sub")
        if resp.status_code in (200, 201):
            return sub_url(resp.json())
    except Exception as e:
        logger.warning(f"panel_revoke_sub: {e}")
    return ""


# ============================== عضویت کانال ==============================
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
        # اگر ربات ادمین کانال نباشد، کاربران بلاک نشوند (fail-open) و ادمین خبردار شود
        logger.warning(f"خطا در بررسی عضویت: {e}")
        if now - _JOIN_ALERT["t"] > 3600:
            _JOIN_ALERT["t"] = now
            await notify_admin(bot, f"⚠️ بررسی عضویت کانال خطا داد (ربات ادمین کانال است؟):\n{e}")
        return True


def join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")],
    ])


JOIN_TEXT = "برای استفاده از بات، اول باید تو کانال ما عضو بشی 👇"


async def require_join(update, context, user_id) -> bool:
    """True یعنی کاربر عضو نیست و پیام عضویت فرستاده شد."""
    if user_id == ADMIN_ID or await is_member(context.bot, user_id):
        return False
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if target:
        await target.reply_text(JOIN_TEXT, reply_markup=join_keyboard())
    return True


# ============================== کیبوردها و متن‌ها ==============================
def main_keyboard():
    return ReplyKeyboardMarkup([
        [BTN_NEW], [BTN_TEST, BTN_RENEW], [BTN_MY],
        [BTN_HELP, BTN_CREDIT], [BTN_INVITE, BTN_SUPPORT], [BTN_AGENT],
    ], resize_keyboard=True)


def cancel_receipt_keyboard():
    return ReplyKeyboardMarkup([[BTN_CANCEL_RECEIPT]], resize_keyboard=True)


def back_menu_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]])


def duration_menu():
    rows = [[InlineKeyboardButton(t, callback_data=f"dur_{k}")] for k, (t, _) in DURATIONS.items()]
    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def volume_menu(dk):
    rows = [[InlineKeyboardButton(f"{gb} گیگ | {fmt(prices[dk])} تومان", callback_data=f"vol_{dk}_{vk}")]
            for vk, (gb, prices) in VOLUMES.items()]
    rows.append([InlineKeyboardButton("🔙 بازگشت به مدت‌ها", callback_data="dur_menu")])
    return InlineKeyboardMarkup(rows)


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


def support_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 ارسال پیام به پشتیبانی", url=f"https://t.me/{SUPPORT.lstrip('@')}")],
        [InlineKeyboardButton("❓ سوالات متداول", callback_data="faq")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")],
    ])


def services_kb(uid, prefix):
    rows = [[InlineKeyboardButton(f"🟢 {s['username']}", callback_data=f"{prefix}{s['username']}")]
            for s in user_services(uid)]
    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def final_price(order) -> int:
    base = VOLUMES[order["vk"]][1][order["dk"]]
    pct = int(order.get("discount_percent", 0))
    return max(base - base * pct // 100, 1000)


def order_text(order) -> str:
    title, days = DURATIONS[order["dk"]]
    gb = VOLUMES[order["vk"]][0]
    base = VOLUMES[order["vk"]][1][order["dk"]]
    kind = "تمدید سرویس" if order["kind"] == "renew" else "خرید سرویس جدید"
    lines = [
        f"🧾 <b>پیش‌فاکتور ({kind})</b>", "",
        f"👤 نام کاربری: <code>{esc(order['username'])}</code>",
        f"🌐 سرور: {esc(BRAND)}",
        f"📦 دسته محصول: {title}",
        f"🏷 نوع سرویس: {gb} گیگ | {days} روزه",
        "",
    ]
    pct = int(order.get("discount_percent", 0))
    if pct:
        lines.append(f"🎁 تخفیف {pct}٪ اعمال شد (قیمت اصلی: {fmt(base)} تومان)")
    lines.append(f"💳 مبلغ قابل پرداخت: <b>{fmt(final_price(order))}</b> تومان")
    lines += ["", "سفارش شما آماده پرداخت است"]
    return "\n".join(lines)


def confirm_menu(order):
    rows = []
    if not order.get("discount_code"):
        rows.append([InlineKeyboardButton("🎁 اعمال کد تخفیف", callback_data="apply_discount")])
    rows.append([InlineKeyboardButton("💳 پرداخت ریالی (کارت به کارت)", callback_data="paycard")])
    rows.append([InlineKeyboardButton("💰 پرداخت از موجودی کیف پول", callback_data="paywallet")])
    rows.append([InlineKeyboardButton("🏠 بازگشت به منوی اصلی", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def pay_text(amount) -> str:
    return (
        f"لطفاً مبلغ <b>{fmt(amount)}</b> تومان (<code>{amount * 10}</code> ریال) به کارت زیر واریز نمایید.\n\n"
        f"<code>{esc(CARD_NUMBER)}</code>\n{esc(CARD_OWNER)}\n\n"
        "📷 عکس رسید خود را در این مرحله ارسال نمایید.\n\n"
        "⚠️ از اپلیکیشن آپ و تاپ با توجه به محدودیت روزانه برای انتقال استفاده نکنید.\n"
        "⚠️ مبلغ را دقیق واریز کنید؛ و از رند کردن آن خودداری فرمایید.\n"
        "⚠️ مبلغی که کپی میکنید ریالی است، توجه کنید.\n\n"
        "👇 بعد از واریز دکمه ارسال رسید رو بزنید"
    )


def pay_markup(amount):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 کپی مبلغ", copy_text=CopyTextButton(text=str(amount * 10))),
         InlineKeyboardButton("📋 کپی شماره کارت", copy_text=CopyTextButton(text=CARD_DIGITS))],
        [InlineKeyboardButton("📤 ارسال رسید پرداخت", callback_data="send_receipt")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")],
    ])


def gb_fmt(b) -> str:
    return f"{float(b) / 1024 ** 3:.2f}"


def service_view(svc, info):
    uname = svc["username"]
    if info is None:
        status_line, active, usage = "⚪️ وضعیت: نامشخص (پنل در دسترس نیست)", True, ""
    else:
        st = info.get("status", "")
        active = st == "active"
        names = {"active": "🟢 فعال", "disabled": "🔴 غیرفعال", "expired": "🔴 منقضی",
                 "limited": "🟠 اتمام حجم", "on_hold": "🟡 در انتظار"}
        status_line = f"وضعیت: {names.get(st, st)}"
        used = int(info.get("used_traffic") or 0)
        limit = int(info.get("data_limit") or 0)
        exp = parse_expire(info.get("expire"))
        usage = f"\n📊 مصرف: {gb_fmt(used)} / {gb_fmt(limit) if limit else '∞'} گیگابایت"
        if exp:
            left = int((exp - time.time()) // 86400)
            usage += f"\n⏳ زمان باقی‌مانده: {max(left, 0)} روز"
    text = (
        "📌 <b>اطلاعات سرویس شما</b>\n\n"
        f"👤 نام کاربری: <code>{esc(uname)}</code>\n"
        f"{status_line}\n"
        f"🌐 نام پنل: {esc(BRAND)}\n"
        f"📦 نوع اشتراک: {esc(svc['title'])}{usage}\n\n"
        f"🔗 لینک اشتراک:\n<code>{esc(svc['link'])}</code>"
    )
    toggle = (InlineKeyboardButton("⏹ توقف سرویس", callback_data=f"stop_{uname}") if active
              else InlineKeyboardButton("▶️ فعالسازی سرویس", callback_data=f"active_{uname}"))
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 دریافت QR Code", callback_data=f"qr_{uname}")],
        [toggle, InlineKeyboardButton("♻️ تمدید سرویس", callback_data=f"rn_{uname}")],
        [InlineKeyboardButton("❌ حذف لینک فعلی و دریافت لینک جدید", callback_data=f"reset_confirm_{uname}")],
        [InlineKeyboardButton("🔙 بازگشت به لیست سرویس ها", callback_data="back_to_services")],
    ])
    return text, markup


async def send_service_to_user(bot, user_id, username, link, days, gb, renewed=False):
    head = "🟢 سرویس شما با موفقیت تمدید شد." if renewed else "🟢 اشتراک شما با موفقیت ساخته شد."
    caption = (f"{head}\n👤 نام کاربری شما:\n<code>{esc(username)}</code>\n\n"
               f"🌐 لینک اشتراک شما:\n\n<code>{esc(link)}</code>\n\n"
               "📲 برای کپی کردن لینک بالا فقط کافیست آدرس لینک را یک بار لمس کنید!")
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
                         caption=caption, parse_mode=HTML, reply_markup=markup)


def clear_flow(context):
    for k in ("state", "order", "order_id"):
        context.user_data.pop(k, None)


# ============================== انجام سفارش ==============================
async def reward_referrer(bot, buyer_id, amount):
    ref = get_referrer(buyer_id)
    if not ref or ref == buyer_id or REFERRAL_PERCENT <= 0:
        return
    reward = int(amount) * REFERRAL_PERCENT // 100
    if reward <= 0:
        return
    add_balance(ref, reward)
    await safe_send(bot, ref, f"🎁 یکی از دعوت‌شده‌های شما خرید کرد و {fmt(reward)} تومان "
                              f"به کیف پولتان اضافه شد.")


async def fulfill_order(bot, oid):
    """سفارشی که وضعیتش 'processing' است را اجرا می‌کند. -> (ok, error)"""
    o = get_order(oid)
    uid = o["user_id"]

    if o["kind"] == "credit":
        add_balance(uid, o["amount"])
        set_order_status(oid, "done")
        await safe_send(bot, uid, f"✅ پرداخت شما تایید شد و {fmt(o['amount'])} تومان به موجودی حساب شما "
                                  f"اضافه شد.\n💰 موجودی فعلی: {fmt(get_balance(uid))} تومان")
        return True, ""

    title, days = DURATIONS[o["dk"]]
    gb = VOLUMES[o["vk"]][0]
    username = o["username"]

    if o["kind"] == "new":
        ok, res = await panel_create_user(username, gb, days)
        if not ok:
            return False, res
        link = res
        try:
            add_service(uid, username, title, gb, days, link)
        except Exception as e:
            logger.error(f"ثبت سرویس در دیتابیس ناموفق: {e}")
            await notify_admin(bot, f"⚠️ سرویس {username} در پنل ساخته شد ولی در دیتابیس ثبت نشد:\n{e}")
    else:  # renew
        ok, res = await panel_renew(username, gb, days)
        if not ok:
            return False, res
        svc = find_service(username, uid)
        link = res or (svc["link"] if svc else "")
        if res and svc:
            update_service_link(username, res)

    set_order_status(oid, "done")
    await reward_referrer(bot, uid, o["amount"])
    try:
        await send_service_to_user(bot, uid, username, link, days, gb, renewed=(o["kind"] == "renew"))
    except Exception as e:
        logger.error(f"ارسال سرویس به مشتری ناموفق: {e}")
        await notify_admin(bot, f"⚠️ سفارش #{oid} انجام شد ولی ارسال به کاربر {uid} ناموفق بود "
                                f"(احتمالاً ربات را بلاک کرده).\nنام کاربری: {username}\nلینک: {link}")
    return True, ""


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
        release_discount(o["discount_code"])
        await ack(q, "رد شد")
        try:
            await q.edit_message_caption(caption=caption + "\n\n❌ رد شد.")
        except Exception:
            pass
        await safe_send(context.bot, o["user_id"],
                        f"❌ متاسفانه رسید شما تایید نشد. لطفاً با پشتیبانی تماس بگیرید: {SUPPORT}")
        return

    # تایید: ابتدا قفل اتمیک
    if not claim_order(oid, "pending", "processing"):
        await ack(q, "این سفارش قبلاً بررسی شده یا در حال پردازش است.", True)
        return
    await ack(q, "⏳ در حال انجام...")
    try:
        ok, err = await fulfill_order(context.bot, oid)
    except Exception as e:
        ok, err = False, f"استثنا: {e}"
        logger.exception("fulfill_order")
    if not ok:
        claim_order(oid, "processing", "pending")  # دوباره قابل تایید
        await notify_admin(context.bot, f"⚠️ انجام سفارش #{oid} ناموفق بود:\n{err}\n\n"
                                        f"بعد از رفع مشکل دوباره روی ✅ همان رسید بزن.")
        return
    try:
        await q.edit_message_caption(caption=caption + "\n\n✅ تایید شد و انجام/ارسال شد.")
    except Exception:
        pass


# ============================== هندلرها ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_flow(context)
    user_id = update.effective_user.id
    is_new = add_user(user_id)

    if is_new and context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0][4:])
        except ValueError:
            referrer_id = None
        if referrer_id and referrer_id != user_id:
            add_referral(referrer_id, user_id)

    if await require_join(update, context, user_id):
        return
    await update.message.reply_text(WELCOME, reply_markup=main_keyboard())


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    add_user(user_id)
    if await require_join(update, context, user_id):
        return

    text = (update.message.text or "").strip()

    if text in MENU_BUTTONS:
        clear_flow(context)
        if text == BTN_CANCEL_RECEIPT:
            await update.message.reply_text("به منوی اصلی بازگشتید!", reply_markup=main_keyboard())
            return

    state = context.user_data.get("state")

    if state == "waiting_custom_credit":
        context.user_data.pop("state", None)
        try:
            amount = int(normalize_digits(text).replace(",", "").replace("٬", "").strip())
            if amount < 10000 or amount > 50_000_000:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ مبلغ نامعتبر است (حداقل 10,000 و حداکثر 50,000,000 تومان)",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="add_credit")]]))
            return
        oid = create_order(user_id, "credit", amount)
        context.user_data["order_id"] = oid
        await update.message.reply_text(pay_text(amount), parse_mode=HTML, reply_markup=pay_markup(amount))
        return

    if state == "waiting_gift_code":
        context.user_data.pop("state", None)
        if throttled(user_id, "gift", 3):
            return
        amount = redeem_gift(text.upper())
        amount = amount if False else redeem_gift(text.upper(), user_id) if False else 0
        # (خط بالا حذف شود؛ نسخه صحیح در پایین)
        amount = redeem_gift_safe(text, user_id)
        if amount:
            await update.message.reply_text(
                f"🎁 کد هدیه اعمال شد و {fmt(amount)} تومان به موجودی شما اضافه شد.",
                reply_markup=main_keyboard())
        else:
            await update.message.reply_text("❌ کد هدیه نامعتبر یا استفاده‌شده است.", reply_markup=back_menu_kb())
        return

    if state == "waiting_discount":
        context.user_data.pop("state", None)
        order = context.user_data.get("order")
        if not order or "dk" not in order or "username" not in order:
            await update.message.reply_text("نشست منقضی شد. دوباره از منو شروع کن.", reply_markup=main_keyboard())
            return
        if throttled(user_id, "discount", 3):
            return
        code = text.upper()
        pct = lookup_discount(code)
        if pct is None:
            await update.message.reply_text("❌ کد تخفیف نامعتبر است.")
        else:
            order["discount_code"], order["discount_percent"] = code, pct
            context.user_data["order"] = order
        await update.message.reply_text(order_text(order), parse_mode=HTML, reply_markup=confirm_menu(order))
        return

    if state == "waiting_agent_msg":
        context.user_data.pop("state", None)
        await notify_admin(
            context.bot,
            f"🤝 درخواست نمایندگی جدید\n\n👤 نام: {user.full_name}\n"
            f"🆔 یوزرنیم: @{user.username or 'ندارد'}\n🔢 آیدی: {user_id}\n\n💬 متن پیام:\n{text}")
        await update.message.reply_text("🚀 پیام شما ارسال شد منتظر پاسخ مدیریت باشید.",
                                        reply_markup=main_keyboard())
        return

    if state == "waiting_username":
        username = text.lower()
        if not USERNAME_RE.match(username):
            await update.message.reply_text(
                "❌ نام کاربری نامعتبر است. فقط حروف کوچک لاتین، عدد و _ (بین 3 تا 20 کاراکتر) مجاز است:")
            return
        order = context.user_data.get("order")
        if not order or "dk" not in order:
            context.user_data.pop("state", None)
            await update.message.reply_text("نشست منقضی شد. دوباره از منو شروع کن.", reply_markup=main_keyboard())
            return
        if username_taken_local(username) or await panel_user_exists(username) is True:
            await update.message.reply_text("❌ این نام کاربری قبلاً گرفته شده. نام دیگری وارد کنید:")
            return
        context.user_data.pop("state", None)
        order["username"] = username
        context.user_data["order"] = order
        await update.message.reply_text(order_text(order), parse_mode=HTML, reply_markup=confirm_menu(order))
        return

    if state == "waiting_receipt":
        await update.message.reply_text("📷 لطفاً تصویر رسید پرداخت را به صورت عکس ارسال نمایید.",
                                        reply_markup=cancel_receipt_keyboard())
        return

    if text == BTN_NEW:
        context.user_data["order"] = {"kind": "new"}
        await update.message.reply_text("⏳ انتخاب مدت زمان:", reply_markup=duration_menu())
        return

    if text == BTN_RENEW:
        if not user_services(user_id):
            await update.message.reply_text("شما هنوز سرویسی ندارید.", reply_markup=main_keyboard())
            return
        await update.message.reply_text("♻️ سرویسی که می‌خواهید تمدید شود را انتخاب کنید:",
                                        reply_markup=services_kb(user_id, "rn_"))
        return

    if text == BTN_TEST:
        await handle_test_request(update, context)
        return

    if text == BTN_MY:
        if not user_services(user_id):
            await update.message.reply_text(
                "📲 شما هنوز سرویسی خریداری نکرده‌اید.\n\nبرای خرید از منوی اصلی گزینه «🚀 خرید سرویس جدید» را بزنید.",
                reply_markup=main_keyboard())
            return
        await update.message.reply_text("🔹 برای مشاهده اطلاعات روی محصول کلیک کنید:",
                                        reply_markup=services_kb(user_id, "serv_"))
        return

    if text == BTN_CREDIT:
        credit_text = (
            "👤 <b>اطلاعات حساب کاربری شما:</b>\n\n"
            f"💰 موجودی: {fmt(get_balance(user_id))} تومان\n"
            f"🆔 آیدی عددی: <code>{user_id}</code>\n"
            f"📦 تعداد سرویس‌ها: {len(user_services(user_id))}\n"
            f"📅 تاریخ ورود به بات: {esc(get_join_date(user_id))}\n\n"
            f"🕒 {now_str()}")
        await update.message.reply_text(
            credit_text, parse_mode=HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 افزایش موجودی", callback_data="add_credit")],
                [InlineKeyboardButton("🎁 اعمال کد هدیه", callback_data="gift_code")],
                [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]]))
        return

    if text == BTN_HELP:
        await update.message.reply_text("لطفا یک گزینه را انتخاب نمایید:", reply_markup=help_menu())
        return

    if text == BTN_SUPPORT:
        await update.message.reply_text(SUPPORT_TEXT, reply_markup=support_kb())
        return

    if text == BTN_INVITE:
        link = f"https://t.me/{context.bot.username}?start=ref_{user_id}"
        msg = (
            "✨ دریافت سرویس‌های ایمن و پر سرعت بدون معطلی از طریق ربات\n\n"
            f"🎁 در صورت معرفی دوستان خود با لینک زیر، با هر خرید موفق هر کدام از آن‌ها "
            f"{REFERRAL_PERCENT}٪ به عنوان هدیه به کیف پول شما واریز می‌گردد.\n\n"
            f"📊 افراد وارد شده با لینک شما: {referral_count(user_id)}\n\n"
            f"👇 لینک اختصاصی دعوت شما:\n{esc(link)}")
        await update.message.reply_text(msg, parse_mode=HTML, reply_markup=back_menu_kb())
        return

    if text == BTN_AGENT:
        context.user_data["state"] = "waiting_agent_msg"
        await update.message.reply_text(f"🤝 {SUPPORT_TEXT}", reply_markup=back_menu_kb())
        return

    await update.message.reply_text("یکی از گزینه‌های منو رو انتخاب کن 👇", reply_markup=main_keyboard())


def redeem_gift_safe(code: str, uid: int) -> int:
    try:
        return redeem_gift(code.strip().upper(), uid)
    except Exception as e:
        logger.error(f"redeem_gift: {e}")
        return 0


async def handle_test_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if throttled(user_id, "test", 15):
        return
    if not claim_test(user_id):  # علامت‌گذاری اتمیک قبل از ساخت
        await update.message.reply_text("❌ شما قبلاً اکانت تست گرفته‌اید. هر کاربر فقط یک بار می‌تواند تست بگیرد.")
        return
    await update.message.reply_text("⏳ در حال ساخت اکانت تست...")
    username = f"test_{user_id}_{secrets.token_hex(2)}"
    ok, result = await panel_create_user(username, gb=0.1, days=1)
    if ok:
        try:
            add_service(user_id, username, "اکانت تست", 0.1, 1, result)
        except Exception as e:
            logger.error(f"ثبت تست: {e}")
        try:
            await send_service_to_user(context.bot, user_id, username, result, 1, 0.1)
        except Exception as e:
            logger.error(f"ارسال تست: {e}")
            await update.message.reply_text(f"اکانت تست ساخته شد:\n<code>{esc(result)}</code>", parse_mode=HTML)
    else:
        unclaim_test(user_id)  # شکست خورد، حق تست برگردد
        await update.message.reply_text(
            f"❌ متاسفانه مشکلی پیش اومد. چند دقیقه دیگه دوباره امتحان کن یا با پشتیبانی تماس بگیر: {SUPPORT}")
        logger.error(f"خطای ساخت تست: {result}")
        await notify_admin(context.bot, f"⚠️ خطا در ساخت تست برای {user_id}:\n{result}")


async def show_order_confirm(q, order):
    await q.edit_message_text(order_text(order), parse_mode=HTML, reply_markup=confirm_menu(order))


def valid_order(order) -> bool:
    return bool(order) and order.get("dk") in DURATIONS and order.get("vk") in VOLUMES \
        and bool(order.get("username"))


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    user_id = q.from_user.id

    if data.startswith(("approve_", "reject_")):
        await handle_admin_decision(update, context, data)
        return

    if data == "check_join":
        if await is_member(context.bot, user_id):
            await ack(q)
            try:
                await q.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=user_id, text=WELCOME, reply_markup=main_keyboard())
        else:
            await ack(q, "هنوز عضو نشدی! اول عضو کانال شو.", True)
        return

    if user_id != ADMIN_ID and not await is_member(context.bot, user_id):
        await ack(q, "اول باید عضو کانال شوید.", True)
        await q.message.reply_text(JOIN_TEXT, reply_markup=join_keyboard())
        return

    # ---------- شاخه‌هایی که خودشان answer می‌دهند ----------
    if data == "send_receipt":
        oid = context.user_data.get("order_id")
        o = get_order(oid) if oid else None
        if not o or o["user_id"] != user_id or o["status"] != "awaiting_receipt":
            await ack(q, "سفارش منقضی شد. دوباره از منو شروع کن.", True)
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
        text, markup = service_view(svc, await panel_get_user(svc["username"]))
        await q.edit_message_text(text, parse_mode=HTML, reply_markup=markup)
        return

    if data.startswith(("stop_", "active_")):
        make_active = data.startswith("active_")
        svc = find_service(data.split("_", 1)[1], user_id)
        if not svc:
            await ack(q, "سرویس پیدا نشد!", True)
            return
        if await panel_change_status(svc["username"], "active" if make_active else "disabled"):
            text, markup = service_view(svc, await panel_get_user(svc["username"]))
            await q.edit_message_text(text, parse_mode=HTML, reply_markup=markup)
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
        await context.bot.send_photo(chat_id=user_id,
                                     photo=InputFile(generate_qr_buffer(new_sub), filename="new_qr.png"),
                                     caption=f"📥 QR Code لینک جدید برای کاربر: <code>{esc(svc['username'])}</code>",
                                     parse_mode=HTML)
        return

    if data.startswith("rn_"):
        svc = find_service(data[3:], user_id)
        if not svc:
            await ack(q, "سرویس پیدا نشد!", True)
            return
        await ack(q)
        context.user_data["order"] = {"kind": "renew", "username": svc["username"]}
        context.user_data.pop("state", None)
        await q.edit_message_text(f"♻️ تمدید سرویس <code>{esc(svc['username'])}</code>\n\n⏳ انتخاب مدت زمان:",
                                  parse_mode=HTML, reply_markup=duration_menu())
        return

    if data in ("paycard", "paywallet"):
        order = context.user_data.get("order")
        if not valid_order(order):
            await ack(q, "نشست منقضی شد. دوباره از منو شروع کن.", True)
            return
        if throttled(user_id, "pay", 3):
            await ack(q)
            return
        amount = final_price(order)
        code = order.get("discount_code")
        if code and not consume_discount(code):
            order.pop("discount_code", None)
            order.pop("discount_percent", None)
            await ack(q, "ظرفیت این کد تخفیف تمام شده.", True)
            await show_order_confirm(q, order)
            return

        if data == "paycard":
            await ack(q)
            oid = create_order(user_id, order["kind"], amount, order["dk"], order["vk"],
                               order["username"], code, "card", "awaiting_receipt")
            context.user_data["order_id"] = oid
            await q.edit_message_text(pay_text(amount), parse_mode=HTML, reply_markup=pay_markup(amount))
            return

        # پرداخت با کیف پول
        if not try_deduct(user_id, amount):
            release_discount(code)
            await ack(q, f"موجودی کافی نیست.\nموجودی شما: {fmt(get_balance(user_id))} تومان", True)
            return
        await ack(q, "⏳ در حال انجام...")
        oid = create_order(user_id, order["kind"], amount, order["dk"], order["vk"],
                           order["username"], code, "wallet", "processing")
        try:
            ok, err = await fulfill_order(context.bot, oid)
        except Exception as e:
            ok, err = False, f"استثنا: {e}"
            logger.exception("wallet fulfill")
        if ok:
            clear_flow(context)
            try:
                await q.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=user_id, text="✅ پرداخت از موجودی انجام شد.",
                                           reply_markup=main_keyboard())
        else:
            add_balance(user_id, amount)  # بازگشت پول
            release_discount(code)
            set_order_status(oid, "failed")
            await notify_admin(context.bot, f"⚠️ سفارش کیف پول #{oid} کاربر {user_id} ناموفق بود (پول برگشت داده شد):\n{err}")
            await q.edit_message_text(
                f"❌ ساخت سرویس انجام نشد و مبلغ به موجودی شما برگشت. لطفاً بعداً تلاش کنید یا با {SUPPORT} تماس بگیرید.",
                reply_markup=back_menu_kb())
        return

    await ack(q)

    # ---------- بقیه ----------
    if data == "none":
        return

    if data == "menu":
        clear_flow(context)
        try:
            await q.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=user_id, text=WELCOME, reply_markup=main_keyboard())
        return

    if data == "faq":
        await q.edit_message_text(FAQ_TEXT, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 ارسال پیام به پشتیبانی", url=f"https://t.me/{SUPPORT.lstrip('@')}")],
            [InlineKeyboardButton("🔙 بازگشت به پشتیبانی", callback_data="back_to_support")]]))
        return

    if data == "back_to_support":
        await q.edit_message_text(SUPPORT_TEXT, reply_markup=support_kb())
        return

    if data == "add_credit":
        amts = [(1000000, 750000), (500000, 250000), (200000, 150000), (125000, 100000), (75000, 50000)]
        rows = [[InlineKeyboardButton(f"{fmt(a)} تومان", callback_data=f"payamt_{a}"),
                 InlineKeyboardButton(f"{fmt(b)} تومان", callback_data=f"payamt_{b}")] for a, b in amts]
        rows.append([InlineKeyboardButton("✍️ رقم دلخواه", callback_data="payamt_custom")])
        rows.append([InlineKeyboardButton("❌ انصراف", callback_data="menu")])
        await q.edit_message_text("💰 مبلغ مورد نظر را برای شارژ کیف پول انتخاب کنید:",
                                  reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("payamt_"):
        key = data[7:]
        if key == "custom":
            context.user_data["state"] = "waiting_custom_credit"
            await q.edit_message_text(
                "💸 مبلغ را به تومان وارد کنید (حداقل 10,000):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="add_credit")]]))
            return
        try:
            amount = int(key)
        except ValueError:
            return
        if amount < 10000 or amount > 50_000_000:
            return
        oid = create_order(user_id, "credit", amount)
        context.user_data["order_id"] = oid
        await q.edit_message_text(pay_text(amount), parse_mode=HTML, reply_markup=pay_markup(amount))
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
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ تایید", callback_data=f"reset_do_{uname}"),
                InlineKeyboardButton("❌ انصراف", callback_data=f"serv_{uname}")]]))
        return

    if data == "back_to_services":
        await q.edit_message_text("🔹 برای مشاهده اطلاعات روی محصول کلیک کنید:",
                                  reply_markup=services_kb(user_id, "serv_"))
        return

    if data.startswith("qr_"):
        svc = find_service(data[3:], user_id)
        if svc:
            await q.message.reply_photo(
                photo=InputFile(generate_qr_buffer(svc["link"]), filename="qr.png"),
                caption=f"📥 QR Code مربوط به نام کاربری: <code>{esc(svc['username'])}</code>",
                parse_mode=HTML)
        return

    if data.startswith("hlp_"):
        text = HELP_TEXTS.get(data[4:], "آموزش یافت نشد.")
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به آموزش‌ها", callback_data="back_to_help")]])
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
        order.pop("discount_code", None)
        order.pop("discount_percent", None)
        context.user_data["order"] = order
        if order["kind"] == "renew" and order.get("username"):
            await show_order_confirm(q, order)
            return
        context.user_data["state"] = "waiting_username"
        await q.edit_message_text(
            "✍️ سرویس مورد نظر انتخاب شد. لطفا یک نام کاربری با حروف کوچک لاتین (حداکثر 20 کاراکتر) وارد نمایید.")
        return


async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await require_join(update, context, user.id):
        return
    oid = context.user_data.get("order_id")
    o = get_order(oid) if oid else None

    if context.user_data.get("state") != "waiting_receipt" or not o or o["user_id"] != user.id:
        await update.message.reply_text(
            "لطفاً اول از منو سرویس مورد نظرتون رو انتخاب کنید و بعد از پرداخت، دکمه «ارسال رسید پرداخت» رو بزنید.",
            reply_markup=main_keyboard())
        return
    if throttled(user.id, "receipt", 5):
        return
    if time.time() - o["created_at"] > RECEIPT_TTL:
        set_order_status(oid, "expired")
        release_discount(o["discount_code"])
        clear_flow(context)
        await update.message.reply_text("⌛️ این سفارش منقضی شده. لطفاً دوباره از منو شروع کنید.",
                                        reply_markup=main_keyboard())
        return

    photo = update.message.photo[-1]
    if receipt_used_before(photo.file_unique_id):
        await update.message.reply_text("❌ این رسید قبلاً ارسال شده است.", reply_markup=main_keyboard())
        return
    if not attach_receipt(oid, user.id, photo.file_unique_id):
        await update.message.reply_text("این سفارش قبلاً ثبت شده است.", reply_markup=main_keyboard())
        clear_flow(context)
        return

    kind_map = {"credit": "شارژ کیف پول", "new": "خرید سرویس جدید", "renew": "تمدید سرویس"}
    caption = (f"🧾 رسید جدید | سفارش #{oid}\n\n"
               f"📌 نوع: {kind_map.get(o['kind'], o['kind'])}\n"
               f"👤 کاربر: {user.full_name} (@{user.username})\n🆔 آیدی: {user.id}\n")
    if o["kind"] != "credit":
        title, _ = DURATIONS[o["dk"]]
        caption += (f"👤 نام کاربری: {o['username']}\n⏳ مدت: {title}\n"
                    f"📦 حجم: {VOLUMES[o['vk']][0]} گیگ\n")
        if o["discount_code"]:
            caption += f"🎁 کد تخفیف: {o['discount_code']}\n"
    caption += f"💰 مبلغ: {fmt(o['amount'])} تومان"

    try:
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo.file_id, caption=caption,
                                     reply_markup=admin_keyboard(oid))
    except Exception as e:
        logger.error(f"ارسال رسید به ادمین ناموفق: {e}")
        claim_order(oid, "pending", "awaiting_receipt")  # دوباره قابل ارسال
        await update.message.reply_text("❌ ارسال رسید به مدیریت ناموفق بود. چند دقیقه بعد دوباره تلاش کنید.")
        return

    clear_flow(context)
    await update.message.reply_text(
        "✅ رسید پرداخت شما دریافت شد. پس از بررسی توسط ادمین به درخواست شما رسیدگی می گردد. "
        "لطفا از ارسال مجدد رسید خودداری نمایید.", reply_markup=main_keyboard())


# ============================== دستورات ادمین ==============================
async def cmd_stats(update, context):
    with conn() as c:
        users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        svcs = c.execute("SELECT COUNT(*) n FROM services").fetchone()["n"]
        pend = c.execute("SELECT COUNT(*) n FROM orders WHERE status IN ('pending','processing')").fetchone()["n"]
        rev = c.execute("SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='done' "
                        "AND method='card' AND kind!='credit'").fetchone()["s"]
        cred = c.execute("SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='done' "
                         "AND kind='credit'").fetchone()["s"]
    await update.message.reply_text(
        f"📊 آمار\n\n👥 کاربران: {users}\n📦 سرویس‌ها: {svcs}\n🕓 سفارش‌های باز: {pend}\n"
        f"💳 فروش کارت‌به‌کارت: {fmt(rev)} تومان\n💰 شارژ کیف پول: {fmt(cred)} تومان")


async def cmd_orders(update, context):
    with conn() as c:
        rows = c.execute("SELECT * FROM orders WHERE status IN ('pending','processing') "
                         "ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await update.message.reply_text("سفارش بازی وجود ندارد.")
        return
    lines = [f"#{r['id']} | {r['kind']} | کاربر {r['user_id']} | {fmt(r['amount'])} | {r['status']}" for r in rows]
    await update.message.reply_text("\n".join(lines))


async def cmd_addbalance(update, context):
    try:
        uid, amount = int(context.args[0]), int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /addbalance user_id amount")
        return
    add_balance(uid, amount)
    await update.message.reply_text(f"✅ انجام شد. موجودی فعلی: {fmt(get_balance(uid))}")
    await safe_send(context.bot, uid, f"💰 {fmt(amount)} تومان به موجودی شما اضافه شد.")


async def cmd_adddiscount(update, context):
    try:
        code = context.args[0].upper()
        pct = int(context.args[1])
        mx = int(context.args[2]) if len(context.args) > 2 else 0
        if not 1 <= pct <= 90:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /adddiscount CODE PERCENT(1-90) [MAXUSES (0=نامحدود)]")
        return
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO discounts(code, percent, max_uses, used, active) VALUES(?,?,?,0,1)",
                  (code, pct, mx))
    await update.message.reply_text(f"✅ کد {code} با {pct}٪ تخفیف ساخته شد.")


async def cmd_addgift(update, context):
    try:
        code, amount = context.args[0].upper(), int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /addgift CODE AMOUNT")
        return
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO gifts(code, amount, used_by) VALUES(?,?,NULL)", (code, amount))
    await update.message.reply_text(f"✅ کد هدیه {code} به مبلغ {fmt(amount)} ساخته شد.")


async def cmd_broadcast(update, context):
    msg = update.message
    src, text = msg.reply_to_message, None
    if not src:
        parts = (msg.text or "").split(None, 1)
        if len(parts) < 2:
            await msg.reply_text("استفاده: /broadcast متن   یا ریپلای روی یک پیام")
            return
        text = parts[1]
    ok = fail = 0
    for uid in all_user_ids():
        try:
            if text is not None:
                await context.bot.send_message(uid, text)
            else:
                await context.bot.copy_message(uid, msg.chat_id, src.message_id)
            ok += 1
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await msg.reply_text(f"📣 ارسال شد: {ok} | ناموفق: {fail}")


# ============================== بکاپ و سلامت ==============================
def do_backup() -> str:
    path = os.path.join(DATA_DIR, f"backup_{datetime.now().strftime('%a')}.db")
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(path)
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
        server = HTTPServer(("0.0.0.0", int(PORT)), HealthHandler)
        logger.info(f"سرور سلامت روی پورت {PORT} فعال شد")
        server.serve_forever()
    except OSError as e:
        logger.warning(f"سرور سلامت اجرا نشد: {e}")


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    logger.error("خطا:", exc_info=err)
    if isinstance(err, NetworkError):
        return
    if not throttled(0, "err_notify", 60):
        await notify_admin(context.bot, f"⚠️ خطای ربات: {type(err).__name__}: {err}")
    if isinstance(update, Update) and update.effective_chat:
        await safe_send(context.bot, update.effective_chat.id,
                        "❌ مشکلی پیش آمد. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.")


async def post_init(app: Application):
    global HTTP
    HTTP = httpx.AsyncClient()
    app.bot_data["backup_task"] = asyncio.create_task(backup_loop(app))


async def post_shutdown(app: Application):
    if HTTP:
        await HTTP.aclose()


def main():
    init_db()
    if PORT:
        threading.Thread(target=run_health_server, daemon=True).start()

    persistence = PicklePersistence(filepath=PERSIST_PATH, update_interval=10)
    app = (Application.builder().token(TOKEN)
           .persistence(persistence)
           .concurrent_updates(True)
           .post_init(post_init).post_shutdown(post_shutdown)
           .build())

    admin_only = filters.User(ADMIN_ID)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", cmd_stats, filters=admin_only))
    app.add_handler(CommandHandler("orders", cmd_orders, filters=admin_only))
    app.add_handler(CommandHandler("addbalance", cmd_addbalance, filters=admin_only))
    app.add_handler(CommandHandler("adddiscount", cmd_adddiscount, filters=admin_only))
    app.add_handler(CommandHandler("addgift", cmd_addgift, filters=admin_only))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast, filters=admin_only))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.PHOTO, receipt_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(on_error)

    logger.info("بات در حال اجراست...")
    app.run_polling(bootstrap_retries=-1)  # تلاش مجدد نامحدود موقع استارت (خطای 503 شبکه)


if __name__ == "__main__":
    main()
