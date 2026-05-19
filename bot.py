"""
⚡ هادر بوت — النسخة الاحترافية الكاملة والمدمجة
متعدد المستخدمين + أزرار تفاعلية + حفظ دائم + قسم التكرار والنسخ الذكي الفوري
"""

import asyncio, json, os, random, logging, re
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiohttp import web
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.tl.functions.messages import SetTypingRequest
from telethon.tl.types import SendMessageTypingAction
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("hadir.log", encoding="utf-8"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp  = Dispatcher()

DATA_FILE    = "user_data.json"
SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

# حالة في الذاكرة
PENDING_LOGINS = {}
ACTIVE_TASKS   = {}   # uid -> asyncio.Task
GUARD_TASKS    = {}   # uid -> asyncio.Task
REPEATER_TASKS = {}   # uid -> TelegramClient (العميل النشط لقسم التكرار)
PAUSED         = set()
AWAITING       = {}   # uid -> str (ما ننتظره من المستخدم)

# ══════════════════════════════════════════════════════════════════════════════
#  الحفظ الدائم والبيانات
# ══════════════════════════════════════════════════════════════════════════════

def _load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"Error saving data: {e}")

def _get_user(uid: str):
    data = _load_data()
    if uid not in data:
        data[uid] = {
            "target": "",
            "mode": "normal",
            "interval_s": 2.0,
            "messages": [],
            "random_order": False,
            "separator": None,
            "suffix_text": "",
            "suffix_on": False,
            "suffix_pos": "end",
            "reply_on": False,
            "human_delay_ms": 80,
            "rep_chat": "",
            "rep_target_user": "",
            "rep_active": False
        }
        _save_data(data)
    else:
        updated = False
        for key, default in [("rep_chat", ""), ("rep_target_user", ""), ("rep_active", False)]:
            if key not in data[uid]:
                data[uid][key] = default
                updated = True
        if updated:
            _save_data(data)
    return data[uid]

def _update_user(uid: str, key: str, val):
    data = _load_data()
    if uid not in data: _get_user(uid)
    data[uid][key] = val
    _save_data(data)

# ── تفكيك ومعالجة النص الذكي لقسم التكرار ─────────────────────────────────────
def parse_repeater_text(text: str) -> str:
    text = text.strip()
    pattern = r"([^\s()]+)\s*\((\d+)\)"
    matches = re.findall(pattern, text)
    
    if matches:
        result_parts = []
        for word, count in matches:
            result_parts.extend([word] * int(count))
        return " ".join(result_parts)
    
    if "،" in text or "," in text:
        parts = re.split(r"[،,]+", text)
        result_parts = [p.strip() for p in parts if p.strip()]
        return " ".join(result_parts)
        
    return text

# ══════════════════════════════════════════════════════════════════════════════
#  لوحة المفاتيح والتحكم (Keyboards)
# ══════════════════════════════════════════════════════════════════════════════

def kb_main(uid: str):
    cfg = _get_user(uid)
    send_text = "⏹ إيقاف الإرسال" if uid in ACTIVE_TASKS else "▶️ بدء الإرسال"
    guard_text = "🛡 حارس الشات: شغال" if uid in GUARD_TASKS else "🛡 حارس الشات: معطل"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=send_text, callback_data="toggle_send"),
         InlineKeyboardButton(text="⏸ مؤقت", callback_data="pause")],
        [InlineKeyboardButton(text=guard_text, callback_data="toggle_guard")],
        [InlineKeyboardButton(text="📋 إدارة الرسائل", callback_data="menu_msgs"),
         InlineKeyboardButton(text="⚙️ الإعدادات", callback_data="menu_settings")],
        [InlineKeyboardButton(text="🔥 قسم التكرار الذكي", callback_data="menu_repeater")],
        [InlineKeyboardButton(text="🗑 احذف رسائلي", callback_data="delete_mine"),
         InlineKeyboardButton(text="❓ مساعدة", callback_data="help")]
    ])

def kb_repeater(uid: str):
    cfg = _get_user(uid)
    chat_lbl = f"🎯 شات: {cfg.get('rep_chat') or 'لم يحدد'}"
    user_lbl = f"👤 الشخص: {cfg.get('rep_target_user') or 'لم يحدد'}"
    toggle_lbl = "🛑 إيقاف التشغيل" if cfg.get("rep_active") else "▶️ تشغيل القسم"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=chat_lbl, callback_data="rep_set_chat")],
        [InlineKeyboardButton(text=user_lbl, callback_data="rep_set_user")],
        [InlineKeyboardButton(text=toggle_lbl, callback_data="rep_toggle")],
        [InlineKeyboardButton(text="🔙 العودة للقائمة الرئيسية", callback_data="main_menu")]
    ])

def kb_login(url: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 ربط حسابك الآن", url=url)]
    ])

def kb_msgs():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ إضافة رسالة", callback_data="add_msg"),
         InlineKeyboardButton(text="📜 عرض الرسائل", callback_data="list_msgs")],
        [InlineKeyboardButton(text="🗑 مسح الكل", callback_data="clear_msgs")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ])

def kb_settings(uid: str):
    cfg = _get_user(uid)
    r_lbl = "✅ عشوائي: مفعّل" if cfg["random_order"] else "❌ عشوائي: معطّل"
    rep_lbl = "✅ الرد: مفعّل" if cfg["reply_on"] else "❌ الرد:"
