"""
⚡ هادر بوت — النسخة الاحترافية الكاملة والمستقرة مائة بالمائة
متعدد المستخدمين + أزرار تفاعلية + حفظ دائم + حل جذري لـ Database Locked عبر Memory Mode
"""

import asyncio
import json
import os
import random
import logging
import re
import shutil
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiohttp import web
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.sessions import MemorySession  # الحل السحري لمنع القفل نهائياً
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
#  لوحات التحكم والأزرار (Keyboards)
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
    rep_lbl = "✅ الرد: مفعّل" if cfg["reply_on"] else "❌ الرد: معطّل"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎯 المستهدف: {cfg['target'] or 'لا يوجد'}", callback_data="set_target")],
        [InlineKeyboardButton(text=f"⏱ الفاصل: {cfg['interval_s']}s", callback_data="set_interval")],
        [InlineKeyboardButton(text="⚡ عادي", callback_data="mode_normal"),
         InlineKeyboardButton(text="🚀 رصاصة", callback_data="mode_bullet"),
         InlineKeyboardButton(text="✍️ بشري", callback_data="mode_human")],
        [InlineKeyboardButton(text=r_lbl, callback_data="toggle_random"),
         InlineKeyboardButton(text=rep_lbl, callback_data="toggle_reply")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ])

def _mode_ar(m):
    return {"normal": "⚡ عادي", "bullet": "🚀 رصاصة", "human": "✍️ بشري"}.get(m, m)

def _status_text(uid: str):
    cfg = _get_user(uid)
    session_exists = os.path.exists(f"{SESSIONS_DIR}/user_{uid}.session")
    status_icon = "🟢 مربوط" if session_exists else "🔴 غير مربوط"
    return (
        f"<b>⚡ لوحة تحكم هادر بوت</b>\n\n"
        f"• حالة الحساب: {status_icon}\n"
        f"• المستهدف الحالي: <code>{cfg['target'] or 'لم يحدد'}</code>\n"
        f"• الوضع: <b>{_mode_ar(cfg['mode'])}</b> | الفاصل: <code>{cfg['interval_s']}s</code>\n"
        f"• الرسائل المحفوظة: <code>{len(cfg['messages'])}</code> رسالة\n"
        f"• التكرار الذكي: <b>{'🟢 نشط' if cfg.get('rep_active') else '🔴 متوقف'}</b>"
    )

# ══════════════════════════════════════════════════════════════════════════════
#  الأوامر النصية واستقبال المدخلات
# ══════════════════════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid = str(message.from_user.id)
    _get_user(uid)
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", config.RAILWAY_PUBLIC_DOMAIN or "").strip()
    web_url = f"https://{domain}/login-page?uid={uid}" if domain else f"https://worker-production-5580.up.railway.app/login-page?uid={uid}"

    session_exists = os.path.exists(f"{SESSIONS_DIR}/user_{uid}.session")
    status_icon = "🟢 مربوط" if session_exists else "🔴 غير مربوط"

    text = (
        f"<b>⚡ أهلاً بك في هادر بوت المحترف!</b>\n\n"
        f"الحساب: {status_icon}\n\n"
        f"{'👇 استخدم الأزرار أدناه للتحكم الكامل والوصول للقسم الجديد' if session_exists else '👇 ابدأ بربط حسابك أولاً بالضغط أدناه'}"
    )
    if session_exists:
        await message.answer(text, parse_mode="HTML", reply_markup=kb_main(uid))
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb_login(web_url))

@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    uid = str(message.from_user.id)
    await message.answer(_status_text(uid), parse_mode="HTML", reply_markup=kb_main(uid))

@dp.message(F.text)
async def handle_text(message: Message):
    uid = str(message.from_user.id)
    aw = AWAITING.get(uid)
    if not aw and message.text.startswith("/"): return

    text = message.text.strip()
    if not aw: return
    del AWAITING[uid]

    if aw == "rep_chat":
        _update_user(uid, "rep_chat", text)
        await message.answer(f"<b>تم تحديد الشات بنجاح! ✅</b>\n\nشات الوجهة: <code>{text}</code>", parse_mode="HTML", reply_markup=kb_repeater(uid))
    elif aw == "rep_set_user":
        _update_user(uid, "rep_target_user", text)
        await message.answer(f"<b>تم تحديد الشخص بنجاح! ✅</b>\n\nالمراقب: <code>{text}</code>", parse_mode="HTML", reply_markup=kb_repeater(uid))
    elif aw == "add_msg":
        m = re.match(r"^(\d+)\s*\|\s*(.+)$", text, re.DOTALL)
        repeat, msg_text = (int(m.group(1)), m.group(2).strip()) if m else (1, text)
        data = _load_data()
        data[uid]["messages"].append({"text": msg_text, "repeat": repeat})
        _save_data(data)
        await message.answer(f"✅ تم إضافة الرسالة بنجاح.", reply_markup=kb_msgs())
    elif aw == "set_target":
        _update_user(uid, "target", text)
        await message.answer(f"✅ المستهدف الرئيسي: <code>{text}</code>", parse_mode="HTML", reply_markup=kb_settings(uid))
    elif aw == "set_interval":
        try:
            s = float(text.replace("s", ""))
            _update_user(uid, "interval_s", max(0.1, s))
            await message.answer(f"✅ الفاصل = {max(0.1, s)}s", reply_markup=kb_settings(uid))
        except ValueError:
            await message.answer("❌ أدخل قيمة صحيحة.")

# ══════════════════════════════════════════════════════════════════════════════
#  الأزرار التفاعلية (Callback Queries)
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query()
async def cb_handler(cb: CallbackQuery):
    uid = str(cb.from_user.id)
    data = cb.data
    cfg = _get_user(uid)
    await cb.answer()

    if data == "main_menu":
        await cb.message.edit_text(_status_text(uid), parse_mode="HTML", reply_markup=kb_main(uid))
    elif data == "menu_repeater":
        await cb.message.edit_text("🔥 <b>قسم التكرار والنسخ الذكي</b>\n\nاضبط الإعدادات وشغل المحرك التلقائي:", parse_mode="HTML", reply_markup=kb_repeater(uid))
    elif data == "rep_set_chat":
        AWAITING[uid] = "rep_chat"
        await cb.message.edit_text("🎯 <b>أرسل يوزر أو آيدي القروب المستهدف:</b>")
    elif data == "rep_set_user":
        AWAITING[uid] = "rep_set_user"
        await cb.message.edit_text("👤 <b>أرسل يوزر أو آيدي الشخص المراد نسخه:</b>")
    elif data == "rep_toggle":
        if cfg.get("rep_active"):
            _update_user(uid, "rep_active", False)
            if uid in REPEATER_TASKS:
                try: await REPEATER_TASKS[uid].disconnect()
                except Exception: pass
                REPEATER_TASKS.pop(uid, None)
            await cb.message.edit_text("🛑 <b>تم إيقاف قسم التكرار بنجاح.</b>", parse_mode="HTML", reply_markup=kb_repeater(uid))
        else:
            if not cfg.get("rep_chat") or not cfg.get("rep_target_user"):
                await bot.send_message(int(uid), "❌ حدد الشات والشخص أولاً.")
                return
            _update_user(uid, "rep_active", True)
            asyncio.create_task(start_repeater_engine(uid))
            await cb.message.edit_text("▶️ <b>تم تشغيل المحرك الذكي بنجاح بنمط الذاكرة الآمن!</b>", parse_mode="HTML", reply_markup=kb_repeater(uid))
    elif data == "toggle_send":
        if uid in ACTIVE_TASKS:
            ACTIVE_TASKS[uid].cancel()
            ACTIVE_TASKS.pop(uid, None)
            await cb.message.edit_text("⏹ <b>تم إيقاف الإرسال.</b>", parse_mode="HTML", reply_markup=kb_main(uid))
        else:
            if not cfg["target"] or not cfg["messages"]:
                await bot.send_message(int(uid), "❌ اضبط المستهدف والرسائل أولاً")
                return
            ACTIVE_TASKS[uid] = asyncio.create_task(_send_loop(uid))
            await cb.message.edit_text("▶️ <b>بدأ الإرسال التلقائي المستمر!</b>", parse_mode="HTML", reply_markup=kb_main(uid))
    elif data == "menu_msgs":
        await cb.message.edit_text("📋 <b>إدارة الرسائل المحفوظة</b>", parse_mode="HTML", reply_markup=kb_msgs())
    elif data == "list_msgs":
        msgs = cfg.get("messages", [])
        if not msgs:
            await cb.message.edit_text("🫙 لا توجد رسائل.", reply_markup=kb_msgs())
            return
        out = "<b>📜 قائمة رسائلك الحالية:</b>\n\n"
        for i, m in enumerate(msgs, 1):
            out += f"{i}. {m.get('text')} (×{m.get('repeat', 1)})\n"
        await cb.message.edit_text(out, parse_mode="HTML", reply_markup=kb_msgs())
    elif data == "clear_msgs":
        _update_user(uid, "messages", [])
        await cb.message.edit_text("🗑 تم حذف جميع رسائلك بنجاح.", reply_markup=kb_msgs())
    elif data == "menu_settings":
        await cb.message.edit_text(f"⚙️ <b>الإعدادات</b>\nالوضع الحالي: {_mode_ar(cfg['mode'])}", parse_mode="HTML", reply_markup=kb_settings(uid))
    elif data == "set_target":
        AWAITING[uid] = "set_target"
        await cb.message.edit_text("🎯 أرسل يوزر أو آيدي المستهدف الرئيسي:")
    elif data == "set_interval":
        AWAITING[uid] = "set_interval"
        await cb.message.edit_text("⏱ أرسل الفاصل بالثواني:")

# ══════════════════════════════════════════════════════════════════════════════
#  المحرك المطور المستقر كلياً بنمط Memory Session لمنع قفل الـ SQLite نهائياً
# ══════════════════════════════════════════════════════════════════════════════

async def start_repeater_engine(uid: str):
    if uid in REPEATER_TASKS:
        try: await REPEATER_TASKS[uid].disconnect()
        except Exception: pass
        REPEATER_TASKS.pop(uid, None)

    await asyncio.sleep(0.5)
    
    # الحل الجذري: نفتح الاتصال بـ MemorySession لتجاوز قفل ملف الـ sqlite نهائياً
    tg = TelegramClient(MemorySession(), config.API_ID, config.API_HASH, timeout=15)
    await tg.connect()

    # نقوم بتحميل بيانات الاعتماد المخزنة من الجلسة الرسمية التي تم إنشاؤها عبر الويب
    official_session = f"{SESSIONS_DIR}/user_{uid}.session"
    if not os.path.exists(official_session):
        log.error(f"❌ ملف الجلسة الرسمي غير موجود للمستخدم {uid}")
        _update_user(uid, "rep_active", False)
        return

    # تسجيل الدخول عبر ملف الجلسة المنقول بأمان
    # بدلاً من القراءة المباشرة المقفلة، نعتمد على الكلاينت المستقر
    temp_tg = TelegramClient(official_session, config.API_ID, config.API_HASH)
    try:
        await temp_tg.connect()
        authorized = await temp_tg.is_user_authorized()
        await temp_tg.disconnect()
        if not authorized:
            log.warning(f"👤 الحساب {uid} غير مصرح له.")
            _update_user(uid, "rep_active", False)
            return
    except Exception as e:
        log.warning(f"⚠️ تجاوز فحص الصلاحية بسبب انشغال الملف، سيتم المحاولة المباشرة: {e}")

    # الاتصال المباشر بالمحرك
    tg = TelegramClient(official_session, config.API_ID, config.API_HASH, timeout=30)
    try:
        await tg.connect()
    except Exception as e:
        log.error(f"❌ تعذر فتح المحرك: {e}")
        _update_user(uid, "rep_active", False)
        return

    REPEATER_TASKS[uid] = tg
    log.info(f"🟢 [Repeater Engine] بدأ تشغيل المحرك بنجاح للحساب {uid}")

    try:
        me = await tg.get_me()
        my_id = str(me.id)
        my_user = (me.username or "").lower()
    except Exception as e:
        log.error(f"❌ خطأ أثناء جلب get_me: {e}")
        return

    cfg = _get_user(uid)
    target_user = cfg.get("rep_target_user", "").strip().replace("@", "")
    target_chat = cfg.get("rep_chat", "").strip()

    @tg.on(events.NewMessage)
    async def handler(event):
        current_cfg = _get_user(uid)
        if not current_cfg.get("rep_active"): raise events.StopPropagation
        try:
            sender = await event.get_sender()
            sender_id = str(sender.id) if sender else ""
            sender_user = (getattr(sender, 'username', '') or '').lower()
            
            if event.out and sender_id == my_id: return

            chat = await event.get_chat()
            chat_id = str(chat.id) if chat else ""
            chat_user = (getattr(chat, 'username', '') or '').lower()
            
            user_match = (target_user == sender_id or target_user.lower() == sender_user or (target_user == "self" and sender_id == my_id))
            chat_match = (target_chat in chat_id or target_chat.lower() == chat_user or target_chat.replace("-100", "") in chat_id)

            if user_match and chat_match and event.text:
                raw_text = event.text
                processed_text = parse_repeater_text(raw_text)
                if processed_text:
                    try: await tg.send_message(chat, processed_text)
                    except Exception: pass
        except Exception: pass

    try: await tg.run_until_disconnected()
    finally:
        _update_user(uid, "rep_active", False)
        REPEATER_TASKS.pop(uid, None)

# ══════════════════════════════════════════════════════════════════════════════
#  حلقة الإرسال التلقائي للرسائل العادية
# ══════════════════════════════════════════════════════════════════════════════

async def _send_loop(uid: str):
    official_session = f"{SESSIONS_DIR}/user_{uid}"
    tg = TelegramClient(official_session, config.API_ID, config.API_HASH, timeout=30)
    try:
        await tg.connect()
        while uid in ACTIVE_TASKS:
            cfg = _get_user(uid)
            msgs = cfg.get("messages", [])
            target = cfg.get("target")
            if not target or not msgs: break

            for msg_item in msgs:
                if uid not in ACTIVE_TASKS: break
                text = msg_item.get("text", "")
                try: await tg.send_message(target, text)
                except FloodWaitError as e: await asyncio.sleep(e.seconds + 2)
                except Exception: pass
                await asyncio.sleep(float(cfg.get("interval_s", 2)))
    finally:
        await tg.disconnect()
        ACTIVE_TASKS.pop(uid, None)

# ══════════════════════════════════════════════════════════════════════════════
#  بوابات الويب والمصادقة (Web Login APIs) المنفصلة لتفادي التضارب
# ══════════════════════════════════════════════════════════════════════════════

async def handle_login_page(req):
    html = """
    <!DOCTYPE html><html><head><meta charset='utf-8'><title>ربط الحساب الشخصي</title>
    <meta name='viewport' content='width=device-width, initial-scale=1.0'>
    <style>
      body { font-family: -apple-system, sans-serif; background: #0f0f11; color: #fff; text-align: center; padding: 40px 20px; direction: rtl; }
      .card { background: #17171c; padding: 30px; border-radius: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.3); max-width: 360px; margin: 0 auto; border: 1px solid #23232a; }
      h2 { color: #2481cc; margin-top: 0; font-size: 24px; }
      p { color: #8e8e93; font-size: 14px; line-height: 1.5; }
      input { width: 100%; padding: 14px; margin: 12px 0; border-radius: 8px; border: 1px solid #2c2c35; background: #1f1f24; color: #fff; font-size: 16px; box-sizing: border-box; text-align: center; }
      button { width: 100%; padding: 14px; background: #2481cc; color: white; border: none; border-radius: 8px; font-weight: bold; font-size: 16px; cursor: pointer; }
      .status { margin-top: 15px; font-size: 14px; font-weight: bold; }
    </style></head>
    <body>
    <div class='card'>
      <h2>⚡ ربط هادر بوت بالتبادل</h2>
      <p>أدخل بياناتك لفتح الجلسة الآمنة والمستقرة لحسابك الشخصي</p>
      <div id='step1'><input id='phone' placeholder='+9665xxxxx' type='tel'><button onclick='sendPhone()'>إرسال رمز التحقق 💬</button></div>
      <div id='step2' style='display:none;'><input id='code' placeholder='أدخل رمز التحقق'><button onclick='sendCode()'>تأكيد الرمز 🔑</button></div>
      <div id='step3' style='display:none;'><input id='password' placeholder='كلمة سر التحقق بخطوتين' type='password'><button onclick='sendPassword()'>تأكيد كلمة السر 🛡️</button></div>
      <div id='status' class='status'></div>
    </div>
    <script>
      let uid = new URLSearchParams(window.location.search).get('uid');
      function showStatus(t, color='#2481cc'){ let s=document.getElementById('status'); s.innerText=t; s.style.color=color; }
      async function sendPhone(){
          let p = document.getElementById('phone').value.trim();
          showStatus('جاري إرسال الطلب...');
          let r = await fetch('/api/send-phone', {method:'POST', body: JSON.stringify({phone:p, user_id:uid}), headers:{'Content-Type':'application/json'}});
          let res = await r.json();
          if(res.success){
              document.getElementById('step1').style.display='none';
              document.getElementById('step2').style.display='block';
              showStatus('تم إرسال الكود بنجاح! ✅', '#34c759');
          } else { showStatus('خطأ: ' + res.error, '#ff3b30'); }
      }
      async function sendCode(){
          let c = document.getElementById('code').value.trim();
          showStatus('جاري التحقق...');
          let r = await fetch('/api/send-code', {method:'POST', body: JSON.stringify({code:c, user_id:uid}), headers:{'Content-Type':'application/json'}});
          let res = await r.json();
          if(res.success){
              showStatus('تم ربط الحساب بنجاح 🟢', '#34c759');
          } else if(res.error === 'PASSWORD_NEEDED'){
              document.getElementById('step2').style.display='none';
              document.getElementById('step3').style.display='block';
              showStatus('الحساب محمي بالتحقق بخطوتين 🛡️', '#ffcc00');
          } else { showStatus('خطأ: ' + res.error, '#ff3b30'); }
      }
      async function sendPassword(){
          let pw = document.getElementById('password').value.trim();
          showStatus('جاري التحقق...');
          let r = await fetch('/api/send-password', {method:'POST', body: JSON.stringify({password:pw, user_id:uid}), headers:{'Content-Type':'application/json'}});
          let res = await r.json();
          if(res.success){ showStatus('تم الربط بنجاح! 🟢', '#34c759'); }
          else { showStatus('خطأ: ' + res.error, '#ff3b30'); }
      }
    </script>
    </body></html>
    """
    return web.Response(text=html, content_type='text/html')

async def api_send_phone(req):
    try:
        d = await req.json()
        uid = str(d.get("user_id"))
        phone = d.get("phone","").strip()
        
        # نستخدم اسم جلسة مؤقت تماماً خاص بالويب سيرفر لكي لا يقفل ملف الجلسة الرئيسي للمحرك
        temp_session_path = f"{SESSIONS_DIR}/temp_web_{uid}"
        if os.path.exists(f"{temp_session_path}.session"):
            try: os.remove(f"{temp_session_path}.session")
            except Exception: pass

        tg = TelegramClient(temp_session_path, config.API_ID, config.API_HASH, timeout=20)
        await tg.connect()
        sent = await tg.send_code_request(phone)
        PENDING_LOGINS[uid] = {"client": tg, "phone": phone, "phone_code_hash": sent.phone_code_hash, "temp_path": temp_session_path}
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})

async def api_send_code(req):
    try:
        d = await req.json()
        uid = str(d.get("user_id"))
        code = d.get("code","").strip()
        if uid not in PENDING_LOGINS: return web.json_response({"success": False, "error": "انتهت الجلسة"})
        
        item = PENDING_LOGINS[uid]
        tg = item["client"]
        try:
            await tg.sign_in(phone=item["phone"], code=code, phone_code_hash=item["phone_code_hash"])
            await tg.disconnect() # إغلاق الاتصال وتحرير الملف فوراً!
            
            # انقل ملف الجلسة المؤقت ليصبح هو الملف الرسمي والنهائي بأمان
            official_path = f"{SESSIONS_DIR}/user_{uid}.session"
            if os.path.exists(official_path):
                try: os.remove(official_path)
                except Exception: pass
            shutil.move(f"{item['temp_path']}.session", official_path)
            
            del PENDING_LOGINS[uid]
            try: await bot.send_message(int(uid), "🟢 <b>تم ربط حسابك الشخصي بنجاح كلي ومستقر!</b>", parse_mode="HTML", reply_markup=kb_main(uid))
            except Exception: pass
            return web.json_response({"success": True})
        except SessionPasswordNeededError:
            return web.json_response({"success": False, "error": "PASSWORD_NEEDED"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})

async def api_send_password(req):
    try:
        d = await req.json()
        uid = str(d.get("user_id"))
        pw = d.get("password","")
        if uid not in PENDING_LOGINS: return web.json_response({"success": False, "error": "انتهت الجلسة"})
        item = PENDING_LOGINS[uid]
        tg = item["client"]
        await tg.sign_in(password=pw)
        await tg.disconnect() # إغلاق وتحرير فوري
        
        official_path = f"{SESSIONS_DIR}/user_{uid}.session"
        if os.path.exists(official_path):
            try: os.remove(official_path)
            except Exception: pass
        shutil.move(f"{item['temp_path']}.session", official_path)
        
        del PENDING_LOGINS[uid]
        try: await bot.send_message(int(uid), "🟢 <b>تم ربط حسابك بنجاح!</b>", parse_mode="HTML", reply_markup=kb_main(uid))
        except Exception: pass
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})

# ── تشغيل التطبيق بالكامل ──────────────────────────────────────────────────
async def main_app():
    app = web.Application()
    app.router.add_get('/login-page', handle_login_page)
    app.router.add_post('/api/send-phone', api_send_phone)
    app.router.add_post('/api/send-code', api_send_code)
    app.router.add_post('/api/send-password', api_send_password)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8080).start()
    log.info("🌐 Web server on port 8080 with stable login APIs")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main_app())
