# requirements: python-telegram-bot==22.* httpx qrcode[pil]
import asyncio
import hashlib
import html
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
from urllib.parse import urlparse, parse_qs

import httpx
import qrcode
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
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

# ============================== لاگ ==============================
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
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
CARD_NUMBER = os.environ.get("CARD_NUMBER", "6219 8614 9793 7499").strip()
CARD_OWNER = os.environ.get("CARD_OWNER", "").strip()
CARD_DIGITS = "".join(c for c in CARD_NUMBER if c.isdigit())
SUPPORT = env_required("SUPPORT_USERNAME")
CHANNEL = env_required("CHANNEL_USERNAME")
PANEL_URL = env_required("PANEL_URL").rstrip("/")
PANEL_API_KEY = env_required("PANEL_API_KEY")
PANEL_GROUP_NAME = os.environ.get("PANEL_GROUP_ID", "group1")
BRAND = os.environ.get("BRAND_NAME", "SpeedUR")
DATA_DIR = os.environ.get("DATA_DIR", ".")
PORT = os.environ.get("PORT")
REFERRAL_PERCENT = int(os.environ.get("REFERRAL_PERCENT", "5"))

SMS_SECRET = os.environ.get("SMS_SECRET", "").strip()
SMS_SENDERS = [s.strip() for s in os.environ.get("SMS_SENDERS", "").split(",") if s.strip()]

PAY_GATEWAY = os.environ.get("PAY_GATEWAY", "zibal").strip().lower()
PAY_MERCHANT = os.environ.get("PAY_MERCHANT", "").strip()
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
GATEWAY_ENABLED = bool(PAY_MERCHANT and PUBLIC_URL and PAY_GATEWAY in ("zibal", "zarinpal"))

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot.db")
PERSIST_PATH = os.path.join(DATA_DIR, "bot_state.pickle")

RECEIPT_TTL = 24 * 3600
REMIND_DAYS = 3
REMIND_USAGE = 0.9

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

_seen_prices = set()
for _vk, (_gb, _prices) in VOLUMES.items():
    for _dk in _prices:
        while _prices[_dk] in _seen_prices:
            _prices[_dk] += 500
        _seen_prices.add(_prices[_dk])

BTN_NEW = "🚀 خرید سرویس جدید"
BTN_TEST = "🧪 اکانت تست"
BTN_RENEW = "♻️ تمدید سرویس"
BTN_MY = "📲 سرویس های من"
BTN_HELP = "💡 آموزش استفاده"
BTN_CREDIT = "💰 اعتبار من"
BTN_INVITE = "👥 معرفی به دوستان"
BTN_SUPPORT = "🧑‍💻 تماس با پشتیبانی"
BTN_AGENT = "🤝 درخواست نمایندگی"
BTN_ORDERS = "🧾 سفارش‌های من"
BTN_CANCEL_RECEIPT = "❌ لغو ارسال رسید و بازگشت"

MENU_BUTTONS = {BTN_NEW, BTN_TEST, BTN_RENEW, BTN_MY, BTN_HELP, BTN_CREDIT,
                BTN_INVITE, BTN_SUPPORT, BTN_AGENT, BTN_ORDERS, BTN_CANCEL_RECEIPT}

SUPPORT_TEXT = "لطفا در قالب یک پیام درخواست و نکات قابل ملاحظه درباره خود را ارسال کرده و سپس تا تماس از طرف ادمین منتظر بمانید"
FAQ_TEXT = "❓ سوالات متداول..."
HELP_TEXTS = {"android": "...", "ios": "...", "windows": "..."}

STATUS_ICON = {"active": "🟢", "disabled": "🔴", "expired": "⚫️", "limited": "🟠", "on_hold": "🟡"}
ORDER_STATUS_FA = {"awaiting_receipt": "منتظر رسید", "pending": "در انتظار بررسی", "processing": "در حال انجام", "done": "✅ انجام شد", "rejected": "❌ رد شد", "cancelled": "لغو شد", "expired": "منقضی", "failed": "ناموفق", "gateway_wait": "در انتظار پرداخت درگاه"}
KIND_FA = {"credit": "شارژ کیف پول", "new": "خرید سرویس جدید", "renew": "تمدید سرویس"}

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")
HTML = ParseMode.HTML
TEST_TITLE = "اکانت تست"

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

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY, joined_at TEXT NOT NULL, test_used INTEGER NOT NULL DEFAULT 0,
    banned INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS balances(
    user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS services(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT NOT NULL UNIQUE,
    title TEXT, gb REAL, days INTEGER, link TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_services_user ON services(user_id);
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
    amount INTEGER NOT NULL, dk TEXT, vk TEXT, username TEXT, discount_code TEXT,
    method TEXT NOT NULL DEFAULT 'card', status TEXT NOT NULL, receipt_uid TEXT, gate_ref TEXT,
    created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE TABLE IF NOT EXISTS referrals(
    referred_id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS discounts(
    code TEXT PRIMARY KEY, percent INTEGER NOT NULL, max_uses INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS gifts(
    code TEXT PRIMARY KEY, amount INTEGER NOT NULL, used_by INTEGER);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS notified(
    username TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY(username, kind));
CREATE TABLE IF NOT EXISTS sms_log(
    hash TEXT PRIMARY KEY, created_at REAL NOT NULL);
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
        cols = [r["name"] for r in c.execute("PRAGMA table_info(users)")]
        if "banned" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN banned INTEGER NOT NULL DEFAULT 0")
        ocols = [r["name"] for r in c.execute("PRAGMA table_info(orders)")]
        if "gate_ref" not in ocols:
            c.execute("ALTER TABLE orders ADD COLUMN gate_ref TEXT")

def get_setting(key, default=""):
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default

def set_setting(key, value):
    with conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

def add_user(uid) -> bool:
    with conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO users(user_id, joined_at) VALUES(?,?)", (uid, now_str()))
        return cur.rowcount == 1

def count_users() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

def get_join_date(uid) -> str:
    with conn() as c:
        r = c.execute("SELECT joined_at FROM users WHERE user_id=?", (uid,)).fetchone()
    return r["joined_at"] if r else "نامشخص"

def all_user_ids():
    with conn() as c:
        return [r["user_id"] for r in c.execute("SELECT user_id FROM users WHERE banned=0")]

def is_banned(uid) -> bool:
    with conn() as c:
        r = c.execute("SELECT banned FROM users WHERE user_id=?", (uid,)).fetchone()
    return bool(r and r["banned"])

def set_ban(uid, val: bool):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id, joined_at) VALUES(?,?)", (uid, now_str()))
        c.execute("UPDATE users SET banned=? WHERE user_id=?", (1 if val else 0, uid))

def claim_test(uid) -> bool:
    with conn() as c:
        cur = c.execute("UPDATE users SET test_used=1 WHERE user_id=? AND test_used=0", (uid,))
        return cur.rowcount == 1

def unclaim_test(uid):
    with conn() as c:
        c.execute("UPDATE users SET test_used=0 WHERE user_id=?", (uid,))

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

def username_taken_local(uname) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM services WHERE username=?", (uname,)).fetchone() is not None

def update_service_link(uname, link):
    with conn() as c:
        c.execute("UPDATE services SET link=? WHERE username=?", (link, uname))

def was_notified(uname, kind) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM notified WHERE username=? AND kind=?", (uname, kind)).fetchone() is not None

def mark_notified(uname, kind):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO notified(username, kind) VALUES(?,?)", (uname, kind))

def clear_notified(uname):
    with conn() as c:
        c.execute("DELETE FROM notified WHERE username=?", (uname,))

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

def user_orders(uid, limit=10):
    with conn() as c:
        return c.execute("SELECT * FROM orders WHERE user_id=? AND status NOT IN ('cancelled','expired') "
                         "ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()

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

def revert_receipt(oid):
    with conn() as c:
        c.execute("UPDATE orders SET status='awaiting_receipt', receipt_uid=NULL "
                  "WHERE id=? AND status='pending'", (oid,))

def receipt_used_before(file_uid) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM orders WHERE receipt_uid=?", (file_uid,)).fetchone() is not None

def cancel_awaiting(oid):
    if not oid:
        return
    o = get_order(oid)
    if o and claim_order(oid, "awaiting_receipt", "cancelled"):
        release_discount(o["discount_code"])

def expire_old_orders() -> int:
    cutoff = time.time() - RECEIPT_TTL
    with conn() as c:
        rows = [(r["id"], r["status"]) for r in c.execute(
            "SELECT id, status FROM orders WHERE status IN ('awaiting_receipt','gateway_wait') "
            "AND created_at<?", (cutoff,))]
    n = 0
    for oid, st in rows:
        o = get_order(oid)
        if o and claim_order(oid, st, "expired"):
            release_discount(o["discount_code"])
            n += 1
    return n

def find_open_orders_by_amounts(amounts):
    if not amounts:
        return []
    q = ",".join("?" * len(amounts))
    cutoff = time.time() - RECEIPT_TTL
    with conn() as c:
        return c.execute(
            f"SELECT * FROM orders WHERE status IN ('awaiting_receipt','pending') "
            f"AND method='card' AND created_at>=? AND amount IN ({q}) ORDER BY id",
            (cutoff, *amounts)).fetchall()

def sms_register(h) -> bool:
    with conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO sms_log(hash, created_at) VALUES(?,?)", (h, time.time()))
        return cur.rowcount == 1

def set_gate_ref(oid, ref):
    with conn() as c:
        c.execute("UPDATE orders SET gate_ref=? WHERE id=?", (str(ref), oid))

def to_gateway(oid, uid) -> bool:
    with conn() as c:
        cur = c.execute("UPDATE orders SET method='gateway', status='gateway_wait', created_at=? "
                        "WHERE id=? AND user_id=? AND status='awaiting_receipt'",
                        (time.time(), oid, uid))
        return cur.rowcount == 1

def back_to_card(oid):
    with conn() as c:
        c.execute("UPDATE orders SET method='card', status='awaiting_receipt' "
                  "WHERE id=? AND status='gateway_wait'", (oid,))

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

def redeem_gift_safe(code: str, uid: int) -> int:
    try:
        return redeem_gift(code.strip().upper(), uid)
    except Exception as e:
        logger.error(f"redeem_gift: {e}")
        return 0

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

def quota_valid(info: dict) -> bool:
    now = time.time()
    expire = parse_expire(info.get("expire"))
    limit = int(info.get("data_limit") or 0)
    used = int(info.get("used_traffic") or 0)
    return (not expire or expire > now) and (not limit or used < limit)

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
    try:
        r = await panel("GET", f"/api/user/{username}")
        if r.status_code == 200:
            return r.json()
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
            await panel_delete_user(username)
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
            await notify_admin(bot, f"⚠️ بررسی عضویت کانال خطا داد:\n{e}")
        return True

def join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")],
    ])

JOIN_TEXT = "برای استفاده از بات، اول باید تو کانال ما عضو بشی 👇"
BANNED_TEXT = "⛔️ دسترسی شما به ربات مسدود شده است."
MAINT_TEXT = "🛠 ربات در حال بروزرسانی است. لطفاً کمی بعد دوباره تلاش کنید."

def welcome_text() -> str:
    return "🚀 به فروشگاه v2ray market خوش آمدید\n\nیکی از گزینه‌ها رو انتخاب کن:"

def _target(update):
    return update.message or (update.callback_query.message if update.callback_query else None)

async def require_join(update, context, user_id) -> bool:
    if user_id == ADMIN_ID or await is_member(context.bot, user_id):
        return False
    t = _target(update)
    if t:
        await t.reply_text(JOIN_TEXT, reply_markup=join_keyboard())
    return True

async def gate(update, context, user_id) -> bool:
    if user_id == ADMIN_ID:
        return False
    t = _target(update)
    if is_banned(user_id):
        if t:
            await t.reply_text(BANNED_TEXT)
        return True
    if get_setting("maintenance") == "1":
        if t:
            await t.reply_text(MAINT_TEXT)
        return True
    return await require_join(update, context, user_id)

def main_keyboard():
    return ReplyKeyboardMarkup([
        [BTN_NEW], [BTN_TEST, BTN_RENEW], [BTN_MY, BTN_ORDERS],
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

async def services_kb(uid, prefix):
    svcs = user_services(uid)
    infos = await asyncio.gather(*(panel_get_user(s["username"]) for s in svcs))
    rows = []
    for s, info in zip(svcs, infos):
        icon = "⚪️" if info is None else STATUS_ICON.get(info.get("status", ""), "⚪️")
        rows.append([InlineKeyboardButton(f"{icon} {s['username']}",
                                          callback_data=f"{prefix}{s['username']}")])
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
    if GATEWAY_ENABLED:
        rows.append([InlineKeyboardButton("🌐 درگاه اینترنتی شاپرک (بدون کارمزد)", callback_data="paygate")])
    rows.append([InlineKeyboardButton("💰 پرداخت از موجودی کیف پول", callback_data="paywallet")])
    rows.append([InlineKeyboardButton("🏠 بازگشت به منوی اصلی", callback_data="menu")])
    return InlineKeyboardMarkup(rows)

def pay_text(amount) -> str:
    owner = f"\n{esc(CARD_OWNER)}" if CARD_OWNER else ""
    return (
        f"لطفاً مبلغ <b>{fmt(amount)}</b> تومان (<code>{amount * 10}</code> ریال) به کارت زیر واریز نمایید.\n\n"
        f"<code>{esc(CARD_NUMBER)}</code>{owner}\n\n"
        "✅ پرداخت شما بعد از واریز به‌صورت خودکار شناسایی و تایید می‌شود. "
        "اگر تا چند دقیقه تایید نشد، عکس رسید را ارسال کنید.\n\n"
        "⚠️ مبلغ را دقیق واریز کنید؛ و از رند کردن آن خودداری فرمایید.\n"
        "⚠️ مبلغی که کپی میکنید ریالی است، توجه کنید."
    )

def pay_markup(amount):
    rows = [[InlineKeyboardButton("📋 کپی مبلغ", copy_text=CopyTextButton(text=str(amount * 10))),
             InlineKeyboardButton("📋 کپی شماره کارت", copy_text=CopyTextButton(text=CARD_DIGITS))]]
    if GATEWAY_ENABLED:
        rows.append([InlineKeyboardButton("🌐 درگاه اینترنتی شاپرک (بدون کارمزد)", callback_data="gatepay")])
    rows.append([InlineKeyboardButton("📤 ارسال رسید پرداخت", callback_data="send_receipt")])
    rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")])
    return InlineKeyboardMarkup(rows)

def gb_fmt(b) -> str:
    return f"{float(b) / 1024 ** 3:.2f}"

def service_view(svc, info):
    uname = svc["username"]
    st = None
    if info is None:
        status_line, usage = "⚪️ وضعیت: نامشخص (پنل در دسترس نیست)", ""
    else:
        st = info.get("status", "")
        names = {"active": "🟢 فعال", "disabled": "🔴 غیرفعال", "expired": "⚫️ منقضی",
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
    second_row = []
    if st == "active":
        second_row.append(InlineKeyboardButton("⏹ توقف سرویس", callback_data=f"stop_{uname}"))
    elif st == "disabled":
        second_row.append(InlineKeyboardButton("▶️ فعالسازی سرویس", callback_data=f"active_{uname}"))
    second_row.append(InlineKeyboardButton("♻️ تمدید سرویس", callback_data=f"rn_{uname}"))
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 دریافت QR Code", callback_data=f"qr_{uname}")],
        second_row,
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
    cancel_awaiting(context.user_data.get("order_id"))
    for k in ("state", "order", "order_id"):
        context.user_data.pop(k, None)

async def reward_referrer(bot, buyer_id, amount):
    ref = get_referrer(buyer_id)
    if not ref or ref == buyer_id or REFERRAL_PERCENT <= 0:
        return
    reward = int(amount) * REFERRAL_PERCENT // 100
    if reward <= 0:
        return
    add_balance(ref, reward)
    await safe_send(bot, ref, f"🎁 یکی از دعوت‌شده‌های شما خرید کرد و {fmt(reward)} تومان به کیف پولتان اضافه شد.")

async def fulfill_order(bot, oid):
    o = get_order(oid)
    uid = o["user_id"]

    if o["kind"] == "credit":
        add_balance(uid, o["amount"])
        set_order_status(oid, "done")
        await safe_send(bot, uid, f"✅ پرداخت شما تایید شد و {fmt(o['amount'])} تومان به موجودی حساب شما اضافه شد.\n💰 موجودی فعلی: {fmt(get_balance(uid))} تومان")
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
    else:
        ok, res = await panel_renew(username, gb, days)
        if not ok:
            return False, res
        svc = find_service(username, uid)
        link = res or (svc["link"] if svc else "")
        if res and svc:
            update_service_link(username, res)
        clear_notified(username)

    set_order_status(oid, "done")
    await reward_referrer(bot, uid, o["amount"])
    try:
        await send_service_to_user(bot, uid, username, link, days, gb, renewed=(o["kind"] == "renew"))
    except Exception as e:
        logger.error(f"ارسال سرویس به مشتری ناموفق: {e}")
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
            await ack(q, "این سفارش قبلاً بررسی شده.", True)
            return
        release_discount(o["discount_code"])
        await ack(q, "رد شد")
        try:
            await q.edit_message_caption(caption=caption + "\n\n❌ رد شد.")
        except Exception:
            pass
        await safe_send(context.bot, o["user_id"], f"❌ متاسفانه رسید شما تایید نشد. ارتباط با پشتیبانی: {SUPPORT}")
        return

    if not claim_order(oid, "pending", "processing"):
        await ack(q, "این سفارش قبلاً بررسی شده.", True)
        return
    await ack(q, "⏳ در حال انجام...")
    ok, err = await fulfill_order(context.bot, oid)
    if not ok:
        claim_order(oid, "processing", "pending")
        await notify_admin(context.bot, f"⚠️ انجام سفارش #{oid} ناموفق بود:\n{err}")
        return
    try:
        await q.edit_message_caption(caption=caption + "\n\n✅ تایید و ارسال شد.")
    except Exception:
        pass

async def process_sms(bot, text: str, sender: str = "") -> str:
    if SMS_SENDERS and not any(s.lower() in (sender or "").lower() for s in SMS_SENDERS):
        return "ignored_sender"
    t = normalize_digits(text or "")
    if not t.strip() or ("برداشت" in t and "واریز" not in t) or not any(k in t for k in ("واریز", "+", "deposit")):
        return "ignored"
    h = hashlib.sha256(t.encode("utf-8")).hexdigest()
    if not sms_register(h):
        return "duplicate"
    lines = [ln for ln in t.splitlines() if ("واریز" in ln or "+" in ln) and "موجودی" not in ln]
    source = " ".join(lines) if lines else t
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
    rows = find_open_orders_by_amounts(list(cands))
    if not rows:
        return "no_match"
    o = rows[0]
    oid = o["id"]
    if not (claim_order(oid, "pending", "processing") or claim_order(oid, "awaiting_receipt", "processing")):
        return "busy"
    ok, err = await fulfill_order(bot, oid)
    if not ok:
        claim_order(oid, "processing", "pending")
        return "failed"
    return "ok"

def gateway_url(ref) -> str:
    if PAY_GATEWAY == "zarinpal":
        return f"https://payment.zarinpal.com/pg/StartPay/{ref}"
    return f"https://gateway.zibal.ir/start/{ref}"

async def gateway_create(oid, amount):
    cb = f"{PUBLIC_URL}/pay/callback/{oid}"
    rial = int(amount) * 10
    desc = f"{BRAND} - order {oid}"
    try:
        if PAY_GATEWAY == "zarinpal":
            r = await HTTP.post("https://payment.zarinpal.com/pg/v4/payment/request.json",
                                json={"merchant_id": PAY_MERCHANT, "amount": rial,
                                      "callback_url": cb, "description": desc},
                                headers={"Accept": "application/json"}, timeout=30.0)
            j = r.json()
            data = j.get("data")
            if isinstance(data, dict) and data.get("code") == 100 and data.get("authority"):
                a = str(data["authority"])
                return True, gateway_url(a), a
            return False, f"zarinpal: {str(j)[:300]}", ""
        r = await HTTP.post("https://gateway.zibal.ir/v1/request",
                            json={"merchant": PAY_MERCHANT, "amount": rial, "callbackUrl": cb,
                                  "description": desc, "orderId": str(oid)}, timeout=30.0)
        j = r.json()
        if j.get("result") == 100 and j.get("trackId"):
            t = str(j["trackId"])
            return True, gateway_url(t), t
        return False, f"zibal: {str(j)[:300]}", ""
    except Exception as e:
        return False, f"خطا در اتصال به درگاه: {e}", ""

async def gateway_verify(o):
    """
    تابع حیاتی تایید تراکنش:
    این تابع کاملاً ایمن شده است و اجازه دور زدن پرداخت را نمی‌دهد.
    """
    ref = o["gate_ref"]
    rial = int(o["amount"]) * 10
    if not ref:
        return "unpaid", "no_ref"
    try:
        if PAY_GATEWAY == "zarinpal":
            r = await HTTP.post("https://payment.zarinpal.com/pg/v4/payment/verify.json",
                                json={"merchant_id": PAY_MERCHANT, "amount": rial, "authority": ref},
                                headers={"Accept": "application/json"}, timeout=30.0)
            j = r.json()
            data = j.get("data")
            if isinstance(data, dict) and data.get("code") in (100, 101):
                return "paid", str(data.get("ref_id", ""))
            return "unpaid", f"zarinpal_err={str(j)}"
            
        # بررسی برای درگاه زیبال (Zibal)
        r = await HTTP.post("https://gateway.zibal.ir/v1/verify",
                            json={"merchant": PAY_MERCHANT, "trackId": int(ref)}, timeout=30.0)
        j = r.json()
        res = j.get("result")
        
        # کد 100 یعنی تراکنش کاملاً موفق و تایید شده است
        if res == 100:
            paid_amount = int(j.get("amount", 0))
            if paid_amount != rial:
                return "mismatch", f"amount_mismatch={paid_amount}"
            return "paid", str(j.get("refNumber", ""))
        else:
            return "unpaid", f"zibal_result={res}"
    except Exception as e:
        return "error", str(e)

async def process_gateway(bot, oid) -> str:
    o = get_order(oid)
    if not o or o["method"] != "gateway" or not o["gate_ref"]:
        return "notfound"
    if o["status"] == "done":
        return "already"
    if o["status"] != "gateway_wait":
        if o["status"] == "processing":
            return "busy"
        if o["status"] == "pending":
            return "paid_pending"
        return "closed"

    # پیش از هر اقدامی، استعلام قطعی از سرور درگاه گرفته می‌شود
    state, detail = await gateway_verify(o)
    if state in ("unpaid", "error", "mismatch"):
        return state

    if not claim_order(oid, "gateway_wait", "processing"):
        return "busy"

    ok, err = await fulfill_order(bot, oid)
    if not ok:
        claim_order(oid, "processing", "pending")
        return "paid_pending"
    return "ok"

def gateway_text(amount) -> str:
    return (
        "🌐 <b>پرداخت از طریق درگاه اینترنتی شاپرک</b>\n\n"
        f"💳 مبلغ قابل پرداخت: <b>{fmt(amount)}</b> تومان\n\n"
        "✅ بعد از پرداخت موفق و کسر وجه از حساب، سفارش به‌صورت خودکار تایید و کانفیگ تحویل داده می‌شود.\n\n"
        "👇 روی دکمه زیر بزنید تا به صفحه پرداخت بروید:")

async def show_gateway(q, o):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 ورود به درگاه پرداخت", url=gateway_url(o["gate_ref"]))],
        [InlineKeyboardButton("♻️ بررسی وضعیت پرداخت", callback_data=f"gcheck_{o['id']}")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")],
    ])
    await q.edit_message_text(gateway_text(o["amount"]), parse_mode=HTML, reply_markup=kb)

async def start_gateway_payment(q, context, oid, revert_to_card=False):
    o = get_order(oid)
    ok, res, ref = await gateway_create(oid, o["amount"])
    if not ok:
        if revert_to_card:
            back_to_card(oid)
            context.user_data["order_id"] = oid
        else:
            set_order_status(oid, "failed")
            release_discount(o["discount_code"])
        try:
            if revert_to_card:
                await q.edit_message_text("❌ خطا در اتصال به درگاه؛ لطفاً از کارت‌به‌کارت استفاده کنید.\n\n" + pay_text(o["amount"]), parse_mode=HTML, reply_markup=pay_markup(o["amount"]))
            else:
                await q.edit_message_text("❌ خطا در اتصال به درگاه.", reply_markup=back_menu_kb())
        except Exception:
            pass
        return
    set_gate_ref(oid, ref)
    await show_gateway(q, get_order(oid))

GATE_PAGES = {
    "ok": ("✅ پرداخت موفق", "پرداخت شما با موفقیت تأیید شد. کانفیگ شما در ربات ارسال گردید.", True),
    "already": ("✅ پرداخت موفق", "این سفارش قبلاً پرداخت شده است.", True),
    "paid_pending": ("✅ پرداخت ثبت شد", "پرداخت شما تایید شد؛ اشتراک شما به زودی ارسال می‌شود.", True),
    "unpaid": ("❌ پرداخت انجام نشد", "تراکنش انجام نشد، لغو گردید یا وجهی واریز نشده است.", False),
    "error": ("⚠️ خطا در ارتباط", "ارتباط با درگاه برقرار نشد.", False),
    "mismatch": ("⚠️ مغایرت مبلغ", "مبلغ پرداختی با فاکتور مطابقت ندارد.", False),
    "closed": ("⌛️ سفارش بسته شده", "این سفارش منقضی یا بسته شده است.", False),
    "notfound": ("❌ سفارش پیدا نشد", "سفارش نامعتبر است.", False),
}

def pay_page(title, msg, ok) -> bytes:
    try:
        uname = _BOT.username if _BOT else ""
    except Exception:
        uname = ""
    btn = f'<a class="b" href="https://t.me/{uname}">بازگشت به ربات</a>' if uname else ""
    color = "#16a34a" if ok else "#dc2626"
    page = (
        "<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>" + title + "</title><style>"
        "body{font-family:Tahoma,sans-serif;background:#f3f4f6;display:flex;align-items:center;"
        "justify-content:center;min-height:100vh;margin:0}"
        ".c{background:#fff;border-radius:16px;padding:32px;max-width:380px;text-align:center;"
        "box-shadow:0 4px 20px rgba(0,0,0,.1)}"
        "h2{color:" + color + "}"
        ".b{display:inline-block;margin-top:20px;background:#2563eb;color:#fff;padding:12px 24px;"
        "border-radius:10px;text-decoration:none}"
        "</style></head><body><div class='c'><h2>" + title + "</h2><p>" + msg + "</p>" + btn +
        "</div></body></html>")
    return page.encode("utf-8")

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

    def _html(self, code, title, msg, ok):
        data = pay_page(title, msg, ok)
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _pay_callback(self, u):
        try:
            oid = int(u.path.rstrip("/").rsplit("/", 1)[-1])
        except ValueError:
            return self._html(404, *GATE_PAGES["notfound"])
        if _BOT is None or _LOOP is None:
            return self._html(503, *GATE_PAGES["error"])
        try:
            fut = asyncio.run_coroutine_threadsafe(process_gateway(_BOT, oid), _LOOP)
            res = fut.result(timeout=90)
        except Exception as e:
            logger.error(f"gateway callback: {e}")
            res = "error"
        self._html(200, *GATE_PAGES.get(res, GATE_PAGES["notfound"]))

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith("/pay/callback/"):
            return self._pay_callback(u)
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - bot is running")

    def do_POST(self):
        try:
            u = urlparse(self.path)
            if u.path.rstrip("/") != "/sms" or not SMS_SECRET:
                return self._reply(404, {"ok": False})
            key = (parse_qs(u.query).get("key") or [""])[0] or self.headers.get("X-Secret", "")
            if not secrets.compare_digest(key, SMS_SECRET):
                return self._reply(403, {"ok": False})
            length = min(int(self.headers.get("Content-Length") or 0), 10000)
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            text, sender = raw, ""
            try:
                j = json.loads(raw)
                if isinstance(j, dict):
                    text = str(j.get("text") or j.get("message") or j.get("body") or "")
                    sender = str(j.get("sender") or j.get("from") or "")
            except ValueError:
                form = parse_qs(raw)
                if form.get("text") or form.get("message"):
                    text = (form.get("text") or form.get("message"))[0]
                    sender = (form.get("sender") or form.get("from") or [""])[0]
            if _BOT is None or _LOOP is None:
                return self._reply(503, {"ok": False})
            fut = asyncio.run_coroutine_threadsafe(process_sms(_BOT, text, sender), _LOOP)
            self._reply(200, {"ok": True, "result": fut.result(timeout=90)})
        except Exception as e:
            logger.error(f"webhook: {e}")
            self._reply(500, {"ok": False})

    def log_message(self, format, *args):
        pass

def run_web_server():
    try:
        server = ThreadingHTTPServer(("0.0.0.0", int(PORT)), WebHandler)
        logger.info(f"وب‌سرور روی پورت {PORT} فعال شد")
        server.serve_forever()
    except OSError as e:
        logger.warning(f"وب‌سرور اجرا نشد: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
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
            await update.message.reply_text("❌ مبلغ نامعتبر است", reply_markup=back_menu_kb())
            return
        oid = create_order(user_id, "credit", amount)
        context.user_data["order_id"] = oid
        await update.message.reply_text(pay_text(amount), parse_mode=HTML, reply_markup=pay_markup(amount))
        return

    if state == "waiting_gift_code":
        context.user_data.pop("state", None)
        amount = redeem_gift_safe(text, user_id)
        if amount:
            await update.message.reply_text(f"🎁 کد هدیه اعمال شد و {fmt(amount)} تومان اضافه گردید.", reply_markup=main_keyboard())
        else:
            await update.message.reply_text("❌ کد هدیه نامعتبر است.", reply_markup=back_menu_kb())
        return

    if state == "waiting_discount":
        context.user_data.pop("state", None)
        order = context.user_data.get("order")
        code = text.upper()
        pct = lookup_discount(code)
        if pct is None:
            await update.message.reply_text("❌ کد تخفیف نامعتبر است.")
        else:
            order["discount_code"], order["discount_percent"] = code, pct
        await update.message.reply_text(order_text(order), parse_mode=HTML, reply_markup=confirm_menu(order))
        return

    if state == "waiting_agent_msg":
        context.user_data.pop("state", None)
        await notify_admin(context.bot, f"🤝 درخواست نمایندگی:\nکاربر: {user.full_name}\nمتن:\n{text}")
        await update.message.reply_text("🚀 پیام ارسال شد.", reply_markup=main_keyboard())
        return

    if state == "waiting_username":
        username = text.lower()
        if not USERNAME_RE.match(username):
            await update.message.reply_text("❌ نام کاربری نامعتبر (فقط حروف انگلیسی، اعداد و _):")
            return
        order = context.user_data.get("order")
        if username_taken_local(username) or await panel_user_exists(username) is True:
            await update.message.reply_text("❌ این نام کاربری قبلاً گرفته شده:")
            return
        context.user_data.pop("state", None)
        order["username"] = username
        await update.message.reply_text(order_text(order), parse_mode=HTML, reply_markup=confirm_menu(order))
        return

    if text == BTN_NEW:
        context.user_data["order"] = {"kind": "new"}
        await update.message.reply_text("⏳ انتخاب مدت زمان:", reply_markup=duration_menu())
        return

    if text == BTN_RENEW:
        if not user_services(user_id):
            await update.message.reply_text("شما هنوز سرویسی ندارید.", reply_markup=main_keyboard())
            return
        await update.message.reply_text("♻️ سرویس مورد نظر برای تمدید:", reply_markup=await services_kb(user_id, "rn_"))
        return

    if text == BTN_TEST:
        await handle_test_request(update, context)
        return

    if text == BTN_MY:
        if not user_services(user_id):
            await update.message.reply_text("📲 شما هنوز سرویسی ندارید.", reply_markup=main_keyboard())
            return
        await update.message.reply_text("🔹 اطلاعات سرویس‌ها:", reply_markup=await services_kb(user_id, "serv_"))
        return

    if text == BTN_ORDERS:
        rows = user_orders(user_id)
        if not rows:
            await update.message.reply_text("🧾 سفارشی ندارید.", reply_markup=main_keyboard())
            return
        lines = ["🧾 <b>سفارش‌ها:</b>\n"]
        for r in rows:
            lines.append(f"#{r['id']} | {KIND_FA.get(r['kind'], r['kind'])} | {fmt(r['amount'])} تومان | {ORDER_STATUS_FA.get(r['status'], r['status'])}")
        await update.message.reply_text("\n".join(lines), parse_mode=HTML, reply_markup=main_keyboard())
        return

    if text == BTN_CREDIT:
        await update.message.reply_text(
            f"👤 <b>حساب کاربری:</b>\n\n💰 موجودی: {fmt(get_balance(user_id))} تومان",
            parse_mode=HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 افزایش موجودی", callback_data="add_credit")],
                [InlineKeyboardButton("🎁 اعمال کد هدیه", callback_data="gift_code")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="menu")]]))
        return

    if text == BTN_HELP:
        await update.message.reply_text("لطفا یک گزینه را انتخاب نمایید:", reply_markup=help_menu())
        return

    if text == BTN_SUPPORT:
        await update.message.reply_text(SUPPORT_TEXT, reply_markup=support_kb())
        return

    if text == BTN_INVITE:
        link = f"https://t.me/{context.bot.username}?start=ref_{user_id}"
        await update.message.reply_text(f"🎁 لینک دعوت:\n{link}", reply_markup=back_menu_kb())
        return

    if text == BTN_AGENT:
        context.user_data["state"] = "waiting_agent_msg"
        await update.message.reply_text(f"🤝 {SUPPORT_TEXT}", reply_markup=back_menu_kb())
        return

    await update.message.reply_text("یک گزینه انتخاب کنید:", reply_markup=main_keyboard())

_TEST_BUSY = set()

async def handle_test_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in _TEST_BUSY or not claim_test(user_id):
        await update.message.reply_text("❌ شما قبلاً اکانت تست گرفته‌اید.")
        return
    _TEST_BUSY.add(user_id)
    try:
        username = f"test_{user_id}_{secrets.token_hex(2)}"
        ok, result = await panel_create_user(username, gb=0.1, days=1)
        if ok:
            add_service(user_id, username, TEST_TITLE, 0.1, 1, result)
            await send_service_to_user(context.bot, user_id, username, result, 1, 0.1)
        else:
            unclaim_test(user_id)
            await update.message.reply_text("❌ خطا در ساخت اکانت تست.")
    finally:
        _TEST_BUSY.discard(user_id)

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
            await context.bot.send_message(chat_id=user_id, text=welcome_text(), reply_markup=main_keyboard())
        else:
            await ack(q, "هنوز عضو کانال نشده‌اید!", True)
        return

    if data == "send_receipt":
        oid = context.user_data.get("order_id")
        o = get_order(oid) if oid else None
        if not o or o["status"] != "awaiting_receipt":
            await ack(q, "سفارش نامعتبر است.", True)
            return
        await ack(q)
        context.user_data["state"] = "waiting_receipt"
        await context.bot.send_message(chat_id=user_id, text="👇 تصویر رسید پرداخت را بفرستید:", reply_markup=cancel_receipt_keyboard())
        return

    if data == "gatepay":
        if not GATEWAY_ENABLED:
            await ack(q, "درگاه فعال نیست.", True)
            return
        oid = context.user_data.get("order_id")
        o = get_order(oid) if oid else None
        if not o or o["status"] != "awaiting_receipt":
            await ack(q, "سفارش منقضی شده است.", True)
            return
        if not to_gateway(oid, user_id):
            await ack(q, "خطا در تغییر وضعیت سفارش.", True)
            return
        context.user_data.pop("order_id", None)
        await ack(q, "⏳ اتصال به درگاه...")
        await start_gateway_payment(q, context, oid, revert_to_card=True)
        return

    if data.startswith("gcheck_"):
        try:
            oid = int(data[7:])
        except ValueError:
            await ack(q)
            return
        o = get_order(oid)
        if not o or o["user_id"] != user_id:
            await ack(q, "سفارش پیدا نشد.", True)
            return
        await ack(q)
        res = await process_gateway(context.bot, oid)
        texts = {
            "ok": "✅ پرداخت شما تایید شد و کانفیگ ارسال گردید.",
            "already": "✅ این سفارش قبلاً پرداخت شده است.",
            "paid_pending": "✅ پرداخت ثبت شد.",
            "unpaid": "❌ هنوز پرداختی برای این سفارش تایید نشده است. اگر پرداخت کرده‌اید چند ثانیه دیگر بررسی کنید.",
            "error": "⚠️ خطا در ارتباط با درگاه.",
        }
        await q.message.reply_text(texts.get(res, "❌ تراکنش تایید نشد."))
        return

    if data.startswith("serv_"):
        svc = find_service(data[5:], user_id)
        if not svc:
            await ack(q, "سرویس پیدا نشد!", True)
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
            await ack(q, "وضعیت تغییر کرد.", True)
        return

    if data.startswith("reset_do_"):
        svc = find_service(data[9:], user_id)
        if not svc:
            return
        new_sub = await panel_revoke_sub(svc["username"])
        if not new_sub:
            await ack(q, "❌ خطا در دریافت لینک جدید.", True)
            return
        await ack(q)
        update_service_link(svc["username"], new_sub)
        await q.message.delete()
        await context.bot.send_message(chat_id=user_id, parse_mode=HTML, text=f"✅ لینک جدید:\n<code>{esc(new_sub)}</code>")
        return

    if data.startswith("rn_"):
        svc = find_service(data[3:], user_id)
        if not svc:
            return
        await ack(q)
        cancel_awaiting(context.user_data.get("order_id"))
        context.user_data["order"] = {"kind": "renew", "username": svc["username"]}
        await q.edit_message_text(f"♻️ تمدید {esc(svc['username'])}\n\n⏳ انتخاب مدت:", reply_markup=duration_menu())
        return

    if data in ("paycard", "paywallet", "paygate"):
        order = context.user_data.get("order")
        if not order:
            await ack(q, "نشست منقضی شد.", True)
            return
        amount = final_price(order)
        cancel_awaiting(context.user_data.get("order_id"))
        
        if data == "paygate":
            await ack(q, "⏳ اتصال به درگاه...")
            oid = create_order(user_id, order["kind"], amount, order["dk"], order["vk"],
                               order["username"], order.get("discount_code"), "gateway", "gateway_wait")
            await start_gateway_payment(q, context, oid)
            return

        if data == "paycard":
            await ack(q)
            oid = create_order(user_id, order["kind"], amount, order["dk"], order["vk"],
                               order["username"], order.get("discount_code"), "card", "awaiting_receipt")
            context.user_data["order_id"] = oid
            await q.edit_message_text(pay_text(amount), parse_mode=HTML, reply_markup=pay_markup(amount))
            return

        if not try_deduct(user_id, amount):
            await ack(q, "موجودی کافی نیست.", True)
            return
        oid = create_order(user_id, order["kind"], amount, order["dk"], order["vk"],
                           order["username"], order.get("discount_code"), "wallet", "processing")
        ok, err = await fulfill_order(context.bot, oid)
        if ok:
            clear_flow(context)
            await q.message.delete()
            await context.bot.send_message(chat_id=user_id, text="✅ از موجودی پرداخت شد.", reply_markup=main_keyboard())
        else:
            add_balance(user_id, amount)
            set_order_status(oid, "failed")
            await q.edit_message_text("❌ خطا در ساخت سرویس، وجه بازگشت داده شد.", reply_markup=back_menu_kb())
        return

    if data == "menu":
        clear_flow(context)
        try:
            await q.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=user_id, text=welcome_text(), reply_markup=main_keyboard())
        return

    if data == "faq":
        await q.edit_message_text(FAQ_TEXT, reply_markup=support_kb())
        return

    if data == "add_credit":
        amts = [(1000000, 750000), (500000, 250000), (200000, 150000), (125000, 100000), (75000, 50000)]
        rows = [[InlineKeyboardButton(f"{fmt(a)} تومان", callback_data=f"payamt_{a}"),
                 InlineKeyboardButton(f"{fmt(b)} تومان", callback_data=f"payamt_{b}")] for a, b in amts]
        rows.append([InlineKeyboardButton("✍️ رقم دلخواه", callback_data="payamt_custom")])
        rows.append([InlineKeyboardButton("❌ انصراف", callback_data="menu")])
        await q.edit_message_text("💰 مبلغ را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("payamt_"):
        key = data[7:]
        if key == "custom":
            context.user_data["state"] = "waiting_custom_credit"
            await q.edit_message_text("💸 مبلغ را وارد کنید:", reply_markup=back_menu_kb())
            return
        amount = int(key)
        oid = create_order(user_id, "credit", amount)
        context.user_data["order_id"] = oid
        await q.edit_message_text(pay_text(amount), parse_mode=HTML, reply_markup=pay_markup(amount))
        return

    if data == "gift_code":
        context.user_data["state"] = "waiting_gift_code"
        await q.message.reply_text("🎁 کد هدیه را وارد کنید:", reply_markup=back_menu_kb())
        return

    if data == "apply_discount":
        context.user_data["state"] = "waiting_discount"
        await q.message.reply_text("🎁 کد تخفیف را وارد کنید:")
        return

    if data.startswith("reset_confirm_"):
        uname = data[14:]
        await q.edit_message_text("⚠️ هشدار: لینک قبلی باطل می‌شود. مطمئن هستید؟",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ بله", callback_data=f"reset_do_{uname}"),
                InlineKeyboardButton("❌ خیر", callback_data=f"serv_{uname}")]]))
        return

    if data.startswith("qr_"):
        svc = find_service(data[3:], user_id)
        if svc:
            await q.message.reply_photo(photo=InputFile(generate_qr_buffer(svc["link"]), filename="qr.png"), caption="📥 QR Code", parse_mode=HTML)
        return

    if data.startswith("hlp_"):
        text = HELP_TEXTS.get(data[4:], "آموزش یافت نشد.")
        await q.edit_message_text(text, reply_markup=back_menu_kb())
        return

    if data == "dur_menu":
        await q.edit_message_text("⏳ انتخاب مدت زمان:", reply_markup=duration_menu())
        return

    if data.startswith("dur_"):
        dk = data[4:]
        order = context.user_data.get("order") or {"kind": "new"}
        order["dk"] = dk
        context.user_data["order"] = order
        await q.edit_message_text("📦 انتخاب حجم:", reply_markup=volume_menu(dk))
        return

    if data.startswith("vol_"):
        _, dk, vk = data.split("_", 2)
        order = context.user_data.get("order") or {"kind": "new"}
        order.update({"dk": dk, "vk": vk})
        context.user_data["order"] = order
        if order["kind"] == "renew" and order.get("username"):
            await q.edit_message_text(order_text(order), parse_mode=HTML, reply_markup=confirm_menu(order))
            return
        context.user_data["state"] = "waiting_username"
        await q.edit_message_text("✍️ نام کاربری مورد نظر را وارد کنید:")
        return

async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.message:
        return
    oid = context.user_data.get("order_id")
    o = get_order(oid) if oid else None
    if context.user_data.get("state") != "waiting_receipt" or not o:
        return
    msg = update.message
    f = msg.photo[-1] if msg.photo else msg.document
    if attach_receipt(oid, user.id, f.file_unique_id):
        caption = f"🧾 رسید جدید\nکاربر: {user.id}\nمبلغ: {fmt(o['amount'])}"
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=f.file_id, caption=caption, reply_markup=admin_keyboard(oid))
        clear_flow(context)
        await msg.reply_text("✅ رسید ثبت شد و پس از بررسی ادمین تایید می‌گردد.", reply_markup=main_keyboard())

async def cmd_adminhelp(update, context):
    await update.message.reply_text("/stats /orders /userinfo /addbalance /ban /unban /broadcast")

async def cmd_stats(update, context):
    with conn() as c:
        users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        rev = c.execute("SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='done'").fetchone()["s"]
    await update.message.reply_text(f"📊 آمار:\nکاربران: {users}\nکل فروش: {fmt(rev)} تومان")

async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("خطا:", exc_info=context.error)

_BG_TASKS = []

async def post_init(app: Application):
    global HTTP, _BOT, _LOOP
    HTTP = httpx.AsyncClient()
    _BOT = app.bot
    _LOOP = asyncio.get_running_loop()

async def post_shutdown(app: Application):
    for t in _BG_TASKS:
        t.cancel()
    if HTTP:
        await HTTP.aclose()

def main():
    init_db()
    if PORT:
        threading.Thread(target=run_web_server, daemon=True).start()
    
    persistence = PicklePersistence(filepath=PERSIST_PATH, update_interval=10)
    app = (Application.builder().token(TOKEN)
           .persistence(persistence)
           .concurrent_updates(True)
           .post_init(post_init).post_shutdown(post_shutdown)
           .build())

    private = filters.ChatType.PRIVATE
    admin_only = filters.User(ADMIN_ID) & private
    app.add_handler(CommandHandler("start", start, filters=private))
    app.add_handler(CommandHandler("adminhelp", cmd_adminhelp, filters=admin_only))
    app.add_handler(CommandHandler("stats", cmd_stats, filters=admin_only))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(private & (filters.PHOTO | filters.Document.IMAGE), receipt_handler))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(on_error)

    logger.info("بات در حال اجراست...")
    app.run_polling(bootstrap_retries=-1)

if __name__ == "__main__":
    main()
