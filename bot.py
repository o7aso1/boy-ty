"""
⚡ هادر بوت — النسخة الخارقة المستقرة كلياً القائمة على Supabase
تخزين سحابي كامل للجلسات والإعدادات والرسائل لتفادي قفل الملفات نهائياً
"""

import asyncio
import json
import os
import random
import logging
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiohttp import web
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.sessions import StringSession  # الحل السحابي النهائي للجلسات دون ملفات
import config

# إعداد السجلات
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp  = Dispatcher()

# استخدام مكتبة aiohttp مدمجة للاتصال بـ Supabase بشكل متزامن وسريع
SUPABASE_URL = getattr(config, "SUPABASE_URL", "").strip().rstrip('/')
SUPABASE_KEY = getattr(config, "SUPABASE_KEY", "").strip()

# متغيرات لإدارة المهام في الذاكرة
PENDING_LOGINS = {}
ACTIVE_TASKS   = {}   # uid -> asyncio.Task
REPEATER_TASKS = {}   # uid -> TelegramClient
AWAITING       = {}   # uid -> str

# ══════════════════════════════════════════════════════════════════════════════
# -- بوابات ومحركات الاتصال بـ Supabase
# ══════════════════════════════════════════════════════════════════════════════

async def supabase_request(method: str, endpoint: str, payload=None):
    """ دالة مدمجة ومستقرة للتعامل مع REST API الخاص بـ Supabase مباشرة """
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            if method.upper() == "GET":
                async with session.get(url, headers=headers) as resp:
                    return await resp.json()
            elif method.upper() == "POST":
                headers["Prefer"] = "resolution=merge-duplicates,return=representation"
                async with session.post(url, headers=headers, json=payload) as resp:
                    return await resp.json()
            elif method.upper() == "PATCH":
                async with session.patch(url, headers=headers, json=payload) as resp:
                    return await resp.json()
    except Exception as e:
        log.error(f"Supabase API Error: {e}")
        return None

async def _db_get_user(uid: str):
    """ جلب بيانات المستخدم كاملة من Supabase وفي حال عدم وجوده يتم إنشاؤه """
    res = await supabase_request("GET", f"bot_users?user_id=eq.{uid}")
    if res and len(res) > 0:
        user_data = res[0]
        if isinstance(user_data.get("messages"), str):
            try: user_data["messages"] = json.loads(user_data["messages"])
            except: user_data["messages"] = []
        return user_data
    
    # إذا لم يكن موجوداً، قم بإنشائه فوراً بالإعدادات الافتراضية
    default_user = {
        "user_id": uid, "phone": "", "session_string": "", "target": "",
        "mode": "normal", "interval_s": 2.0, "messages": [],
        "rep_chat": "", "rep_target_user": "", "rep_active": False
    }
    await supabase_request("POST", "bot_users", default_user)
    return default_user

async def _db_update_user(uid: str, updates: dict):
    """ تحديث حقول معينة للمستخدم في قاعدة البيانات السحابية """
    if "messages" in updates and not isinstance(updates["messages"], str):
        updates["messages"] = updates["messages"]
    await supabase_request("PATCH", f"bot_users?user_id=eq.{uid}", updates)

def parse_repeater_text(text: str) -> str:
    text = text.strip()
    pattern = r"([^\s()]+)\s*\((\d+)\)"
    matches = re.findall(pattern, text)
    if matches:
        parts = []
        for word, count in matches: parts.extend([word] * int(count))
        return " ".join(parts)
    if "،" in text or "," in text:
        return " ".join([p.strip() for p in re.split(r"[،,]+", text) if p.strip()])
    return text

# ══════════════════════════════════════════════════════════════════════════════
#  الأزرار وقوائم التحكم
# ══════════════════════════════════════════════════════════════════════════════

def kb_main(uid: str):
    send_text = "⏹ إيقاف الإرسال" if uid in ACTIVE_TASKS else "▶️ بدء الإرسال"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=send_text, callback_data="toggle_send"),
         InlineKeyboardButton(text="📋 إدارة الرسائل", callback_data="menu_msgs")],
        [InlineKeyboardButton(text="⚙️ الإعدادات", callback_data="menu_settings"),
         InlineKeyboardButton(text="🔥 قسم التكرار الذكي", callback_data="menu_repeater")],
        [InlineKeyboardButton(text="🔙 العودة للقائمة", callback_data="main_menu")]
    ])

def kb_repeater(cfg: dict):
    chat_lbl = f"🎯 شات: {cfg.get('rep_chat') or 'لم يحدد'}"
    user_lbl = f"👤 الشخص: {cfg.get('rep_target_user') or 'لم يحدد'}"
    toggle_lbl = "🛑 إيقاف التشغيل" if cfg.get("rep_active") else "▶️ تشغيل القسم"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=chat_lbl, callback_data="rep_set_chat")],
        [InlineKeyboardButton(text=user_lbl, callback_data="rep_set_user")],
        [InlineKeyboardButton(text=toggle_lbl, callback_data="rep_toggle")],
        [InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="main_menu")]
    ])

def kb_msgs():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ إضافة رسالة", callback_data="add_msg"),
         InlineKeyboardButton(text="📜 عرض الرسائل", callback_data="list_msgs")],
        [InlineKeyboardButton(text="🗑 مسح الكل", callback_data="clear_msgs")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ])

def kb_settings(cfg: dict):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎯 المستهدف: {cfg['target'] or 'لا يوجد'}", callback_data="set_target")],
        [InlineKeyboardButton(text=f"⏱ الفاصل: {cfg['interval_s']}s", callback_data="set_interval")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ])

async def _status_text(uid: str):
    cfg = await _db_get_user(uid)
    status_icon = "🟢 مربوط ومخزن سحابياً" if cfg.get("session_string") else "🔴 غير مربوط"
    return (
        f"<b>⚡ لوحة تحكم هادر بوت (نسخة Supabase)</b>\n\n"
        f"• حالة الحساب: {status_icon}\n"
        f"• المستهدف الرئيسي: <code>{cfg['target'] or 'لم يحدد'}</code>\n"
        f"• الفاصل الحالي: <code>{cfg['interval_s']}s</code>\n"
        f"• الرسائل المخزنة: <code>{len(cfg.get('messages', []))}</code> رسالة\n"
        f"• التكرار الذكي: <b>{'🟢 نشط' if cfg.get('rep_active') else '🔴 متوقف'}</b>"
    )

# ══════════════════════════════════════════════════════════════════════════════
#  معالجة الرسائل والمدخلات
# ══════════════════════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid = str(message.from_user.id)
    cfg = await _db_get_user(uid)
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", getattr(config, "RAILWAY_PUBLIC_DOMAIN", "")).strip()
    web_url = f"https://{domain}/login-page?uid={uid}" if domain else f"https://worker-production-5580.up.railway.app/login-page?uid={uid}"
    
    if cfg.get("session_string"):
        await message.answer(await _status_text(uid), parse_mode="HTML", reply_markup=kb_main(uid))
    else:
        await message.answer("<b>⚡ أهلاً بك! يرجى ربط حسابك الشخصي سحابياً للبدء:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔗 ربط حسابك الآن", url=web_url)]]))

@dp.message(F.text)
async def handle_text(message: Message):
    uid = str(message.from_user.id)
    aw = AWAITING.get(uid)
    if not aw or message.text.startswith("/"): return
    
    text = message.text.strip()
    del AWAITING[uid]
    cfg = await _db_get_user(uid)

    if aw == "rep_chat":
        await _db_update_user(uid, {"rep_chat": text})
        cfg["rep_chat"] = text
        await message.answer("✅ تم حفظ شات الوجهة سحابياً.", reply_markup=kb_repeater(cfg))
    elif aw == "rep_set_user":
        await _db_update_user(uid, {"rep_target_user": text})
        cfg["rep_target_user"] = text
        await message.answer("✅ تم حفظ الشخص المستهدف سحابياً.", reply_markup=kb_repeater(cfg))
    elif aw == "add_msg":
        m = re.match(r"^(\d+)\s*\|\s*(.+)$", text, re.DOTALL)
        repeat, msg_text = (int(m.group(1)), m.group(2).strip()) if m else (1, text)
        msgs = cfg.get("messages", [])
        msgs.append({"text": msg_text, "repeat": repeat})
        await _db_update_user(uid, {"messages": msgs})
        await message.answer("✅ تم إضافة الرسالة وحفظها في سوبابيس.", reply_markup=kb_msgs())
    elif aw == "set_target":
        await _db_update_user(uid, {"target": text})
        cfg["target"] = text
        await message.answer("✅ تم تحديث المستهدف.", reply_markup=kb_settings(cfg))
    elif aw == "set_interval":
        try:
            s = float(text)
            await _db_update_user(uid, {"interval_s": s})
            cfg["interval_s"] = s
            await message.answer(f"✅ الفاصل أصبح {s} ثانية.", reply_markup=kb_settings(cfg))
        except: await message.answer("❌ قيمة خاطئة.")

# ══════════════════════════════════════════════════════════════════════════════
#  الأزرار التفاعلية وقسم التكرار السحابي الآمن كلياً
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query()
async def cb_handler(cb: CallbackQuery):
    uid = str(cb.from_user.id)
    data = cb.data
    await cb.answer()
    cfg = await _db_get_user(uid)

    if data == "main_menu":
        await cb.message.edit_text(await _status_text(uid), parse_mode="HTML", reply_markup=kb_main(uid))
    elif data == "menu_repeater":
        await cb.message.edit_text("🔥 <b>إعدادات قسم التكرار الذكي</b>", parse_mode="HTML", reply_markup=kb_repeater(cfg))
    elif data == "rep_set_chat":
        AWAITING[uid] = "rep_chat"; await cb.message.edit_text("🎯 أرسل يوزر أو آيدي شات الوجهة النشر:")
    elif data == "rep_set_user":
        AWAITING[uid] = "rep_set_user"; await cb.message.edit_text("👤 أرسل يوزر أو آيدي الشخص المراد نسخه:")
    elif data == "rep_toggle":
        if cfg.get("rep_active"):
            await _db_update_user(uid, {"rep_active": False})
            if uid in REPEATER_TASKS:
                try: await REPEATER_TASKS[uid].disconnect()
                except: pass
                REPEATER_TASKS.pop(uid, None)
            cfg["rep_active"] = False
            await cb.message.edit_text("🛑 تم إيقاف محرك التكرار بنجاح.", reply_markup=kb_repeater(cfg))
        else:
            if not cfg.get("rep_chat") or not cfg.get("rep_target_user"):
                await bot.send_message(int(uid), "❌ حدد الشات والشخص أولاً.")
                return
            await _db_update_user(uid, {"rep_active": True})
            asyncio.create_task(start_repeater_engine(uid))
            cfg["rep_active"] = True
            await cb.message.edit_text("🟢 تم تشغيل المحرك السحابي بنجاح بنمط الذاكرة والمزامنة!", reply_markup=kb_repeater(cfg))
    elif data == "toggle_send":
        if uid in ACTIVE_TASKS:
            ACTIVE_TASKS[uid].cancel(); ACTIVE_TASKS.pop(uid, None)
            await cb.message.edit_text("⏹ تم إيقاف الإرسال التلقائي.", reply_markup=kb_main(uid))
        else:
            if not cfg["target"] or not cfg.get("messages"):
                await bot.send_message(int(uid), "❌ اضبط المستهدف والرسائل أولاً.")
                return
            ACTIVE_TASKS[uid] = asyncio.create_task(_send_loop(uid))
            await cb.message.edit_text("▶️ بدأ الإرسال التلقائي المستمر من السحاب!", reply_markup=kb_main(uid))
    elif data == "menu_msgs":
        await cb.message.edit_text("📋 إدارة الرسائل المحفوظة سحابياً", reply_markup=kb_msgs())
    elif data == "list_msgs":
        msgs = cfg.get("messages", [])
        if not msgs: await cb.message.edit_text("🫙 لا توجد رسائل.", reply_markup=kb_msgs()); return
        out = "<b>📜 رسائلك في السحاب:</b>\n\n"
        for i, m in enumerate(msgs, 1): out += f"{i}. {m.get('text')} (×{m.get('repeat', 1)})\n"
        await cb.message.edit_text(out, parse_mode="HTML", reply_markup=kb_msgs())
    elif data == "clear_msgs":
        await _db_update_user(uid, {"messages": []})
        await cb.message.edit_text("🗑 تم تفريغ الرسائل بنجاح.", reply_markup=kb_msgs())
    elif data == "menu_settings":
        await cb.message.edit_text("⚙️ الإعدادات", reply_markup=kb_settings(cfg))

# ══════════════════════════════════════════════════════════════════════════════
#  محركات التشغيل المستقرة كلياً بدون أي استخدام للملفات المحلية
# ══════════════════════════════════════════════════════════════════════════════

async def start_repeater_engine(uid: str):
    if uid in REPEATER_TASKS:
        try: await REPEATER_TASKS[uid].disconnect()
        except: pass
        REPEATER_TASKS.pop(uid, None)

    cfg = await _db_get_user(uid)
    sess_str = cfg.get("session_string")
    if not sess_str: return

    # تشغيل الجلسة مباشرة من نص السلسلة السحابي دون فتح أي ملف محلي
    tg = TelegramClient(StringSession(sess_str), config.API_ID, config.API_HASH, timeout=30)
    try: await tg.connect()
    except Exception as e:
        log.error(f"Failed to connect repeater: {e}")
        return

    REPEATER_TASKS[uid] = tg
    log.info(f"🟢 [Supabase Engine] المحرك يعمل الآن بأمان تام للحساب: {uid}")

    me = await tg.get_me()
    my_id = str(me.id)
    target_user = cfg.get("rep_target_user", "").strip().replace("@", "")
    target_chat = cfg.get("rep_chat", "").strip()

    @tg.on(events.NewMessage)
    async def handler(event):
        current_cfg = await _db_get_user(uid)
        if not current_cfg.get("rep_active"): raise events.StopPropagation
        try:
            sender = await event.get_sender()
            sender_id = str(sender.id) if sender else ""
            sender_user = (getattr(sender, 'username', '') or '').lower()
            if event.out and sender_id == my_id: return

            chat = await event.get_chat()
            chat_id = str(chat.id) if chat else ""
            chat_user = (getattr(chat, 'username', '') or '').lower()

            user_match = (target_user == sender_id or target_user.lower() == sender_user)
            chat_match = (target_chat in chat_id or target_chat.lower() == chat_user or target_chat.replace("-100", "") in chat_id)

            if user_match and chat_match and event.text:
                proc = parse_repeater_text(event.text)
                if proc: await tg.send_message(chat, proc)
        except: pass

    try: await tg.run_until_disconnected()
    finally: REPEATER_TASKS.pop(uid, None)

async def _send_loop(uid: str):
    cfg = await _db_get_user(uid)
    sess_str = cfg.get("session_string")
    if not sess_str: return
    
    tg = TelegramClient(StringSession(sess_str), config.API_ID, config.API_HASH, timeout=30)
    try:
        await tg.connect()
        while uid in ACTIVE_TASKS:
            cfg = await _db_get_user(uid)
            msgs = cfg.get("messages", [])
            target = cfg.get("target")
            if not target or not msgs: break

            for msg_item in msgs:
                if uid not in ACTIVE_TASKS: break
                try: await tg.send_message(target, msg_item.get("text", ""))
                except FloodWaitError as e: await asyncio.sleep(e.seconds + 2)
                except: pass
                await asyncio.sleep(float(cfg.get("interval_s", 2)))
    finally:
        await tg.disconnect()
        ACTIVE_TASKS.pop(uid, None)

# ══════════════════════════════════════════════════════════════════════════════
#  واجهات الويب لربط الحسابات واستخراج الـ String Session وحفظها سحابياً
# ══════════════════════════════════════════════════════════════════════════════

async def handle_login_page(req):
    html = """
    <!DOCTYPE html><html><head><meta charset='utf-8'><title>ربط الحساب السحابي</title>
    <meta name='viewport' content='width=device-width, initial-scale=1.0'>
    <style>
      body { font-family: -apple-system, sans-serif; background: #0b0b0e; color: #fff; text-align: center; padding: 40px 20px; direction: rtl; }
      .card { background: #121216; padding: 30px; border-radius: 16px; max-width: 360px; margin: 0 auto; border: 1px solid #1e1e24; }
      h2 { color: #3498db; margin-top: 0; }
      p { color: #7f8c8d; font-size: 14px; }
      input { width: 100%; padding: 14px; margin: 12px 0; border-radius: 8px; border: 1px solid #2c3e50; background: #1a1a24; color: #fff; text-align: center; box-sizing: border-box; }
      button { width: 100%; padding: 14px; background: #3498db; color: white; border: none; border-radius: 8px; font-weight: bold; cursor: pointer; }
      .status { margin-top: 15px; font-size: 14px; font-weight: bold; }
    </style></head>
    <body>
    <div class='card'>
      <h2>☁️ ربط سحابي آمن كلياً</h2>
      <p>يتم الحفظ مباشرة في قاعدة بيانات مشفرة دون قفل الحساب</p>
      <div id='step1'><input id='phone' placeholder='+9665xxxxx' type='tel'><button onclick='sendPhone()'>طلب رمز التحقق 💬</button></div>
      <div id='step2' style='display:none;'><input id='code' placeholder='أدخل الرمز'><button onclick='sendCode()'>تأكيد الرمز 🔑</button></div>
      <div id='step3' style='display:none;'><input id='password' placeholder='كلمة السر بخطوتين' type='password'><button onclick='sendPassword()'>تأكيد كلمة السر 🛡️</button></div>
      <div id='status' class='status'></div>
    </div>
    <script>
      let uid = new URLSearchParams(window.location.search).get('uid');
      function showStatus(t, color='#3498db'){ let s=document.getElementById('status'); s.innerText=t; s.style.color=color; }
      async function sendPhone(){
          let p = document.getElementById('phone').value.trim();
          showStatus('جاري إرسال الطلب للسحاب...');
          let r = await fetch('/api/send-phone', {method:'POST', body: JSON.stringify({phone:p, user_id:uid}), headers:{'Content-Type':'application/json'}});
          let res = await r.json();
          if(res.success){
              document.getElementById('step1').style.display='none'; document.getElementById('step2').style.display='block';
              showStatus('وصلك الكود في تيليجرام الخاص بك! ✅', '#2ecc71');
          } else { showStatus('خطأ: ' + res.error, '#e74c3c'); }
      }
      async function sendCode(){
          let c = document.getElementById('code').value.trim();
          showStatus('جاري الحفظ والتحقق السحابي...');
          let r = await fetch('/api/send-code', {method:'POST', body: JSON.stringify({code:c, user_id:uid}), headers:{'Content-Type':'application/json'}});
          let res = await r.json();
          if(res.success){ showStatus('تم الربط الدائم وحفظ جلستك في سوبابيس بنجاح! 🟢', '#2ecc71'); alert('مبروك! يمكنك إغلاق الصفحة والعودة للبوت.'); }
          else if(res.error === 'PASSWORD_NEEDED'){
              document.getElementById('step2').style.display='none'; document.getElementById('step3').style.display='block';
              showStatus('الحساب محمي بكلمة سر خطوتين 🛡️', '#f1c40f');
          } else { showStatus('خطأ: ' + res.error, '#e74c3c'); }
      }
      async function sendPassword(){
          let pw = document.getElementById('password').value.trim();
          showStatus('جاري التحقق...');
          let r = await fetch('/api/send-password', {method:'POST', body: JSON.stringify({password:pw, user_id:uid}), headers:{'Content-Type':'application/json'}});
          let res = await r.json();
          if(res.success){ showStatus('تم الحفظ بنجاح! 🟢', '#2ecc71'); }
          else { showStatus('خطأ: ' + res.error, '#e74c3c'); }
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
        
        # نستخدم StringSession فارغ في الذاكرة تماماً لاستخراج النص فقط
        tg = TelegramClient(StringSession(), config.API_ID, config.API_HASH, timeout=20)
        await tg.connect()
        sent = await tg.send_code_request(phone)
        PENDING_LOGINS[uid] = {"client": tg, "phone": phone, "phone_code_hash": sent.phone_code_hash}
        return web.json_response({"success": True})
    except Exception as e: return web.json_response({"success": False, "error": str(e)})

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
            # استخراج النص السحري وحفظه في سوبابيس فوراً
            string_session_text = tg.session.save()
            await tg.disconnect()
            
            await _db_update_user(uid, {"phone": item["phone"], "session_string": string_session_text})
            del PENDING_LOGINS[uid]
            
            try: await bot.send_message(int(uid), "🟢 <b>تم حفظ حسابك وإعداداتك سحابياً في Supabase بنجاح! لن تحتاج للتسجيل مجدداً.</b>", parse_mode="HTML", reply_markup=kb_main(uid))
            except: pass
            return web.json_response({"success": True})
        except SessionPasswordNeededError: return web.json_response({"success": False, "error": "PASSWORD_NEEDED"})
    except Exception as e: return web.json_response({"success": False, "error": str(e)})

async def api_send_password(req):
    try:
        d = await req.json()
        uid = str(d.get("user_id"))
        pw = d.get("password","")
        if uid not in PENDING_LOGINS: return web.json_response({"success": False, "error": "انتهت الجلسة"})
        item = PENDING_LOGINS[uid]
        tg = item["client"]
        await tg.sign_in(password=pw)
        string_session_text = tg.session.save()
        await tg.disconnect()
        
        await _db_update_user(uid, {"phone": item["phone"], "session_string": string_session_text})
        del PENDING_LOGINS[uid]
        try: await bot.send_message(int(uid), "🟢 <b>تم الربط بنجاح!</b>", parse_mode="HTML", reply_markup=kb_main(uid))
        except: pass
        return web.json_response({"success": True})
    except Exception as e: return web.json_response({"success": False, "error": str(e)})

# ── تشغيل التطبيق السحابي ──────────────────────────────────────────────────
async def main_app():
    # محاولة تشغيل المحركات النشطة سلفاً للمستخدمين عند إقلاع السيرفر تلقائياً
    res = await supabase_request("GET", "bot_users?rep_active=eq.true")
    if res:
        for u in res:
            asyncio.create_task(start_repeater_engine(u["user_id"]))

    app = web.Application()
    app.router.add_get('/login-page', handle_login_page)
    app.router.add_post('/api/send-phone', api_send_phone)
    app.router.add_post('/api/send-code', api_send_code)
    app.router.add_post('/api/send-password', api_send_password)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8080).start()
    log.info("🌐 Cloud Web server active on port 8080 with Supabase Integration")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main_app())
