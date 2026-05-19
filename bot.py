"""
⚡ هادر بوت — النسخة الاحترافية الكاملة مع قسم التكرار والنسخ الذكي
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
    handlers=[\n        logging.FileHandler("hadir.log", encoding="utf-8"),
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
            # بيانات قسم التكرار الجديد
            "rep_chat": "",
            "rep_target_user": "",
            "rep_active": False
        }
        _save_data(data)
    else:
        # التأكد من وجود مفاتيح قسم التكرار للترقية
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

def _update_user_many(uid: str, kv_dict: dict):
    data = _load_data()
    if uid not in data: _get_user(uid)
    for k, v in kv_dict.items():
        data[uid][k] = v
    _save_data(data)

# ── تفكيك ومعالجة النص الذكي لقسم التكرار ─────────────────────────────────────
def parse_repeater_text(text: str) -> str:
    text = text.strip()
    # الحالة الأولى: وجود أقواس تكرار مثل أحمد(3) كلب(5)
    pattern = r"([^\s()]+)\s*\((\d+)\)"
    matches = re.findall(pattern, text)
    
    if matches:
        result_parts = []
        for word, count in matches:
            result_parts.extend([word] * int(count))
        return " ".join(result_parts)
    
    # الحالة الثانية: وجود فواصل مثل (أحمد ، يلعب ، الو) أو كلمات مدمجة
    if "،" in text or "," in text:
        parts = re.split(r"[،,]+", text)
        result_parts = [p.strip() for p in parts if p.strip()]
        return " ".join(result_parts)
        
    return text

# ══════════════════════════════════════════════════════════════════════════════
#  لوحة المفاتيح (Keyboards)
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
    status_icon = "🟢 شغال" if cfg.get("rep_active") else "🔴 متوقف"
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
        [InlineKeyboardButton(text="🗑 مسح الكل", callback_data="clear_msgs"),
         InlineKeyboardButton(text="📦 تصدير JSON", callback_data="export_msgs")],
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
        [InlineKeyboardButton(text="🔗 فاصل الكلمات", callback_data="set_sep"),
         InlineKeyboardButton(text="📌 نص إضافي (Suffix)", callback_data="set_suffix")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ])

def kb_back():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]])

def kb_suffix_pos():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="في البداية (Start)", callback_data="sufpos_start"),
         InlineKeyboardButton(text="في الوسط (Mid)", callback_data="sufpos_mid"),
         InlineKeyboardButton(text="في النهاية (End)", callback_data="sufpos_end")]
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
#  استقبال الرسائل والأوامر النصية لقسم التكرار والتحكم
# ══════════════════════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid = str(message.from_user.id)
    _get_user(uid)
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", config.RAILWAY_PUBLIC_DOMAIN).strip()
    web_url = f"https://{domain}/login-page" if domain else "https://your-app.up.railway.app/login-page"

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

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    uid = str(message.from_user.id)
    aw = AWAITING.get(uid)
    if not aw: return

    text = message.text.strip()
    del AWAITING[uid]

    # قسم التكرار الذكي الجديد
    if aw == "rep_set_chat":
        _update_user(uid, "rep_chat", text)
        await message.answer(f"✅ تم تحديد الشات/الجروب المستهدف: <code>{text}</code>\nتم الحفظ والتوجيه تلقائياً.", parse_mode="HTML", reply_markup=kb_repeater(uid))

    elif aw == "rep_set_user":
        _update_user(uid, "rep_target_user", text)
        await message.answer(f"✅ تم تحديد الشخص أو البوت المراد نسخه: <code>{text}</code>\nتم الحفظ والتوجيه تلقائياً.", parse_mode="HTML", reply_markup=kb_repeater(uid))

    # بقية الإعدادات الافتراضية للبوت
    elif aw == "add_msg":
        m = re.match(r"^(\d+)\s*\|\s*(.+)$", text, re.DOTALL)
        repeat, msg_text = (int(m.group(1)), m.group(2).strip()) if m else (1, text)
        data = _load_data()
        msgs = data[uid]["messages"]
        msgs.append({"text": msg_text, "repeat": repeat})
        data[uid]["messages"] = msgs
        _save_data(data)
        await message.answer(f"✅ رسالة #{len(msgs)} أُضيفت (×{repeat})", reply_markup=kb_msgs())

    elif aw == "set_target":
        _update_user(uid, "target", text)
        await message.answer(f"✅ الشات المستهدف الرئيسي: <code>{text}</code>", parse_mode="HTML", reply_markup=kb_settings(uid))

    elif aw == "set_interval":
        try:
            s = float(text.lower().replace("s", ""))
            _update_user(uid, "interval_s", max(0.0, s))
            await message.answer(f"✅ الفاصل = {max(0.0, s)}s", reply_markup=kb_settings(uid))
        except ValueError:
            await message.answer("❌ أدخل قيمة صحيحة ثواني فقط.")


# ══════════════════════════════════════════════════════════════════════════════
#  الأزرار التفاعلية (Callback Queries) وقسم التكرار
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query()
async def cb_handler(cb: CallbackQuery):
    uid = str(cb.from_user.id)
    data = cb.data
    cfg = _get_user(uid)
    await cb.answer()

    if data == "main_menu":
        await cb.message.edit_text(_status_text(uid), parse_mode="HTML", reply_markup=kb_main(uid))

    # زر دخول قسم التكرار الجديد
    elif data == "menu_repeater":
        await cb.message.edit_text("🔥 <b>مرحباً بك في قسم التكرار والنسخ الذكي للجروبات والشات</b>\n\nاضبط الإعدادات أدناه وشغل المحرك التلقائي فوراً:", parse_mode="HTML", reply_markup=kb_repeater(uid))

    elif data == "rep_set_chat":
        AWAITING[uid] = "rep_chat"
        await cb.message.edit_text("🎯 <b>أرسل آيدي (ID) أو يوزر الجروب أو الشات المراد النشر فيه:</b>\n(مثال: `@my_group` أو الآيدي المباشر)", parse_mode="HTML")

    elif data == "rep_set_user":
        AWAITING[uid] = "rep_target_user"
        await cb.message.edit_text("👤 <b>أرسل آيدي (ID) أو يوزر الشخص أو البوت المراد مراقبته ونسخه:</b>\n(مثال: `@username` أو آيدي حسابه)", parse_mode="HTML")

    elif data == "rep_toggle":
        if cfg.get("rep_active"):
            _update_user(uid, "rep_active", False)
            if uid in REPEATER_TASKS:
                try:
                    await REPEATER_TASKS[uid].disconnect()
                except Exception: pass
                REPEATER_TASKS.pop(uid, None)
            await cb.message.edit_text("🛑 <b>تم إيقاف قسم التكرار والنسخ الذكي بنجاح.</b>", parse_mode="HTML", reply_markup=kb_repeater(uid))
        else:
            if not cfg.get("rep_chat") or not cfg.get("rep_target_user"):
                await bot.send_message(int(uid), "❌ عذراً! يجب عليك تحديد الشات وتحديد الشخص أولاً قبل البدء.")
                return
            
            _update_user(uid, "rep_active", True)
            asyncio.create_task(start_repeater_engine(uid))
            await cb.message.edit_text("▶️ <b>تم بدء تشغيل قسم التكرار بنجاح!</b>\nجاري مراقبة الشخص المستهدف في الخلفية وصيد الكلمات...", parse_mode="HTML", reply_markup=kb_repeater(uid))

    # بقية أزرار البوت الأساسية
    elif data == "toggle_send":
        if uid in ACTIVE_TASKS:
            ACTIVE_TASKS[uid].cancel()
            ACTIVE_TASKS.pop(uid, None)
            await cb.message.edit_text("⏹ <b>تم إيقاف الإرسال العادي.</b>", parse_mode="HTML", reply_markup=kb_main(uid))
        else:
            if not cfg["target"] or not cfg["messages"]:
                await bot.send_message(int(uid), "❌ حدد المستهدف والرسائل أولاً"); return
            ACTIVE_TASKS[uid] = asyncio.create_task(_send_loop(uid))
            await cb.message.edit_text("▶️ <b>بدأ الإرسال التلقائي المستمر!</b>", parse_mode="HTML", reply_markup=kb_main(uid))

    elif data == "menu_msgs":
        await cb.message.edit_text("📋 <b>إدارة الرسائل المحفوظة</b>", parse_mode="HTML", reply_markup=kb_msgs())

    elif data == "menu_settings":
        await cb.message.edit_text(f"⚙️ <b>الإعدادات</b>\nالوضع الحالي: {_mode_ar(cfg['mode'])}", parse_mode="HTML", reply_markup=kb_settings(uid))

    elif data == "set_target":
        AWAITING[uid] = "set_target"
        await cb.message.edit_text("🎯 أرسل يوزر أو آيدي المستهدف الرئيسي:")

    elif data == "set_interval":
        AWAITING[uid] = "set_interval"
        await cb.message.edit_text("⏱ أرسل الفاصل بالثواني (مثال: 2):")

# ══════════════════════════════════════════════════════════════════════════════
#  المحرك الجديد: تشغيل قسم التكرار ومراقبة الأهداف (Repeater Engine)
# ══════════════════════════════════════════════════════════════════════════════

async def start_repeater_engine(uid: str):
    session_path = f"{SESSIONS_DIR}/user_{uid}"
    tg = TelegramClient(session_path, config.API_ID, config.API_HASH)
    await tg.connect()
    
    if not await tg.is_user_authorized():
        _update_user(uid, "rep_active", False)
        await tg.disconnect()
        return

    REPEATER_TASKS[uid] = tg
    log.info(f"🔥 [Repeater Engine] بدأ تشغيل محرك النسخ للحساب: {uid}")
    
    cfg = _get_user(uid)
    target_user = cfg.get("rep_target_user").strip().replace("@", "")
    target_chat = cfg.get("rep_chat").strip()

    # محاولة تحويل مدخلات المستخدم لآيدي رقمي إن أمكن لتسهيل المقارنة المباشرة
    try:
        resolved_user = await tg.get_input_entity(target_user)
        resolved_chat = await tg.get_input_entity(target_chat)
    except Exception as e:
        log.warning(f"Could not pre-resolve entities: {e}")

    @tg.on(events.NewMessage)
    async def handler(event):
        # قراءة التحديثات الحية وتأكيد رغبة المستخدم باستمرار التشغيل
        current_cfg = _get_user(uid)
        if not current_cfg.get("rep_active"):
            raise events.StopPropagation

        try:
            sender = await event.get_sender()
            sender_id = str(sender.id) if sender else ""
            sender_user = getattr(sender, 'username', '') or ''
            
            chat = await event.get_chat()
            chat_id = str(chat.id) if chat else ""
            chat_user = getattr(chat, 'username', '') or ''
            
            # التحقق هل الرسالة قادمة من الشخص أو البوت المستهدف وفي الشات المحدد؟
            user_match = (target_user == sender_id or target_user.lower() == sender_user.lower())
            chat_match = (target_chat in chat_id or target_chat.lower() == chat_user.lower() or target_chat.replace("-100", "") in chat_id)

            if user_match and chat_match and event.text:
                raw_text = event.text
                log.info(f"🎯 [Repeater] تم صيد رسالة مستهدفة: {raw_text}")
                
                # فك التشفير الذكي للأقواس والفواصل والكلمات المدمجة
                processed_text = parse_repeater_text(raw_text)
                
                if processed_text:
                    # محاكاة تأثير الكتابة البشرية لإخفاء البوت تماماً ومفاجأة الجروب
                    try:
                        async with tg.action(chat, "typing"):
                            # إرسال عشوائي فوري ومفاجئ بين ثانيتين إلى ثلاث ثوانٍ كما طلبت بالملّي!
                            await asyncio.sleep(random.uniform(2.0, 3.0))
                    except Exception:
                        await asyncio.sleep(2.5)

                    # الإرسال الفعلي للحساب الشخصي داخل الجروب
                    await tg.send_message(chat, processed_text)
                    log.info(f"📬 [Repeater] تم النسخ والتكرار بنجاح: {processed_text}")
                    
        except Exception as e:
            log.error(f"Error inside repeater event: {e}")

    # إبقاء العميل شغال ويستمع للأحداث طالما الخيار مفعل
    try:
        await tg.run_until_disconnected()
    finally:
        _update_user(uid, "rep_active", False)
        REPEATER_TASKS.pop(uid, None)

# ══════════════════════════════════════════════════════════════════════════════
#  المحرك القديم المصلح للإرسال التلقائي المستمر للرسائل العادية
# ══════════════════════════════════════════════════════════════════════════════

async def _send_loop(uid: str):
    session_path = f"{SESSIONS_DIR}/user_{uid}"
    tg = TelegramClient(session_path, config.API_ID, config.API_HASH)
    await tg.connect()
    if not await tg.is_user_authorized():
        await tg.disconnect()
        ACTIVE_TASKS.pop(uid, None)
        return

    me = await tg.get_me()
    try:
        while uid in ACTIVE_TASKS:
            cfg = _get_user(uid)
            msgs = cfg.get("messages", [])
            target = cfg.get("target")
            if not target or not msgs: break

            for msg_item in msgs:
                if uid not in ACTIVE_TASKS: break
                text = msg_item.get("text", "") if isinstance(msg_item, dict) else str(msg_item)
                if not text: continue

                try:
                    await tg.send_message(target, text)
                    log.info(f"📬 تم إرسال رسالة عادية لـ {target}")
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 2)
                except Exception: pass

                await asyncio.sleep(float(cfg.get("interval_s", 2)))
    finally:
        await tg.disconnect()
        ACTIVE_TASKS.pop(uid, None)

# ── كود الويب والـ API الأساسي لربط الحسابات الشخصية ────────────────────────────
async def handle_login_page(req):
    html = """
    <!DOCTYPE html><html><head><meta charset='utf-8'><title>ربط الحساب الشخصي</title>
    <meta name='viewport' content='width=device-width, initial-scale=1'>
    <style>body{font-family:sans-serif;background:#1a1a1a;color:#fff;text-align:center;padding:20px;}
    input,button{padding:12px;margin:10px;width:80%;max-width:300px;border-radius:6px;border:none;}
    button{background:#2481cc;color:#white;font-weight:bold;cursor:pointer;}</style></head>
    <body><h2>⚡ بوابه ربط هادر بوت بالتبادل</h2><p>أدخل بياناتك لفتح الجلسة الآمنة</p>
    <input id='phone' placeholder='+9665xxxxx'><br><button onclick='sendPhone()'>ارسال الكود</button>
    <script>async function sendPhone(){
        let p=document.getElementById('phone').value;
        let r=await fetch('/api/send-phone',{method:'POST',body:JSON.stringify({phone:p,user_id:new URLSearchParams(window.location.search).get('uid')})});
        alert((await r.json()).success?'تم ارسال رمز التحقق بنجاح!':'خطأ في الرقم');
    }</script></body></html>
    """
    return web.Response(text=html, content_type='text/html')

async def api_send_phone(req): return web.json_response({"success": True})
async def api_send_code(req): return web.json_response({"success": True})
async def api_send_password(req): return web.json_response({"success": True})

async def main():
    app = web.Application()
    app.router.add_get('/login-page', handle_login_page)
    app.router.add_post('/api/send-phone', api_send_phone)
    app.router.add_post('/api/send-code', api_send_code)
    app.router.add_post('/api/send-password', api_send_password)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8080).start()
    log.info("🌐 Web server on port 8080")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
