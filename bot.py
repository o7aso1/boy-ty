"""
⚡ هادر بوت — النسخة الكاملة والمعدلة للسيرفر
جميع مميزات أداة الإرسال التلقائي داخل بوت تيليجرام.
"""

import asyncio
import json
import os
import random
import re
import logging
from copy import deepcopy
from telethon import TelegramClient, events, functions, types
from telethon.errors import (
    FloodWaitError, MessageNotModifiedError, MessageDeleteForbiddenError
)
from telethon.tl.functions.messages import SetTypingRequest
from telethon.tl.types import SendMessageTypingAction
import config

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("hadir.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# تعريف الكلاينت للبوت الرسمي وتجاوز تعارض الـ API الشخصي
client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)

# ══════════════════════════════════════════════════════════════════════════════
#  الحالة الكاملة
# ══════════════════════════════════════════════════════════════════════════════
DEFAULT_STATE = {
    "running":        False,
    "paused":         False,
    "stop_requested": False,
    "target_chat":    None,
    "messages":       [],    # [{text, repeat}]
    "done_counts":    [],
    "interval_s":     2.0,
    "mode":           "normal",  # normal | bullet | human
    "human_speed_ms": 100,       # السرعة في وضع human
    "random_order":   False,
    "separator":      None,      # الفاصل بين الكلمات
    "suffix_type":    None,      # start | mid | end | None
    "suffix_text":    "",
    "reply_last":     False,     # الرد على آخر رسالة
    "reply_filter":   None,      # فلتر المرسل للرد
    "guard_active":   False,     # حارس الشات
    "accounts":       [],        # الحسابات المتعددة
    "current_acc_idx":0,
}

STATE = deepcopy(DEFAULT_STATE)
STATE_FILE = "bot_state.json"

def _load_state():
    global STATE
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                for k, v in saved.items():
                    if k in STATE:
                        STATE[k] = v
            log.info("💾 تم تحميل حالة البوت بنجاح.")
        except Exception as e:
            log.error(f"❌ خطأ في تحميل الحالة: {e}")

def _save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(STATE, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"❌ خطأ في حفظ الحالة: {e}")

MESSAGES_FILE = "messages.json"

def _load_messages():
    if os.path.exists(MESSAGES_FILE):
        try:
            with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
                STATE["messages"] = json.load(f)
            STATE["done_counts"] = [0] * len(STATE["messages"])
            log.info(f"📋 تم تحميل {len(STATE['messages'])} رسالة جاهزة.")
        except Exception as e:
            log.error(f"❌ خطأ في تحميل الرسائل: {e}")

def _save_messages_to_file():
    try:
        with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump(STATE["messages"], f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"❌ خطأ في حفظ الرسائل للملف: {e}")

_load_state()

# ══════════════════════════════════════════════════════════════════════════════
#  الفلاتر والمساعدين
# ══════════════════════════════════════════════════════════════════════════════
def is_admin(sender_id):
    return str(sender_id) == str(config.ADMIN_ID)

async def get_chat_id(target):
    if not target:
        return None
    if isinstance(target, int):
        return target
    try:
        entity = await client.get_input_entity(target)
        if hasattr(entity, "chat_id"):
            return entity.chat_id
        if hasattr(entity, "channel_id"):
            return entity.channel_id
        if hasattr(entity, "user_id"):
            return entity.user_id
    except Exception:
        pass
    return target

# ══════════════════════════════════════════════════════════════════════════════
#  أوامر لوحة التحكم (Control Chat)
# ══════════════════════════════════════════════════════════════════════════════
@client.on(events.NewMessage(incoming=True))
async def control_handler(event):
    if not is_admin(event.sender_id):
        return

    is_control = False
    if config.CONTROL_CHAT == "me" and event.is_private:
        me = await client.get_me()
        if event.chat_id == me.id:
            is_control = True
    else:
        try:
            c_chat = int(config.CONTROL_CHAT)
            if event.chat_id == c_chat:
                is_control = True
        except ValueError:
            if event.chat and hasattr(event.chat, "username") and event.chat.username:
                if event.chat.username.lower() == config.CONTROL_CHAT.lstrip("@").lower():
                    is_control = True

    if not is_control:
        return

    text = event.raw_text.strip()
    if not text.startswith("/"):
        return

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ["/start", "/help"]:
        help_text = """⚡ **لوحة تحكم هادر بوت جاهزة**

**📌 إعداد الهدف والرسائل:**
`/chat @username` — تحديد القروب المستهدف
`/add تكرار | النص` — إضافة رسالة (مثال: `/add 5 | هلا بالعيال`)
`/clear_msg` — مسح كل الرسائل المضافة
`/list` — عرض قائمة الرسائل الحالية

**⏱️ الإعدادات المتقدمة:**
`/interval 2` — الفاصل الزمني بالثواني
`/mode normal|bullet|human` — وضع الإرسال
`/random on|off` — ترتيب عشوائي للرسائل
`/reset` — استعادة الضبط الافتراضي

**🟢 التشغيل:**
`/start_send` — ابدأ الإرسال التلقائي فوراً
`/pause` — إيقاف مؤقت / استئناف
`/stop` — إيقاف نهائي وتصفير العدادات
`/status` — عرض حالة البوت الحالية
"""
        await event.reply(help_text, parse_mode="md")
        return

    if cmd == "/chat":
        if not args:
            await event.reply("❌ يرجى كتابة يوزر أو آيدي الشات بعد الأمر. مثال:\n`/chat @username`")
            return
        STATE["target_chat"] = args
        _save_state()
        await event.reply(f"🎯 تم تحديد الشات المستهدف بنجاح:\n`{args}`")
        return

    if cmd == "/add":
        if " | " not in args:
            await event.reply("❌ الطريقة الصحيحة للاستخدام:\n`/add التكرار | النص`\nمثال: `/add 3 | السلام عليكم`")
            return
        rep_part, text_part = args.split(" | ", 1)
        try:
            repeat = int(rep_part)
        except ValueError:
            await event.reply("❌ يجب أن يكون التكرار رقماً صحيحاً!")
            return
        STATE["messages"].append({"text": text_part, "repeat": repeat})
        STATE["done_counts"].append(0)
        _save_messages_to_file()
        _save_state()
        await event.reply(f"✅ تمت إضافة الرسالة بنجاح وتكرارها: {repeat}")
        return

    if cmd == "/clear_msg":
        STATE["messages"] = []
        STATE["done_counts"] = []
        _save_messages_to_file()
        _save_state()
        await event.reply("🗑️ تم مسح جميع الرسائل من القائمة.")
        return

    if cmd == "/list":
        if not STATE["messages"]:
            await event.reply("📋 قائمة الرسائل فارغة حالياً.")
            return
        res = "📋 **قائمة الرسائل المضافة الحالية:**\n\n"
        for i, m in enumerate(STATE["messages"]):
            res += f"{i+1}. `{m['text']}` (تكرار: {m['repeat']})\n"
        await event.reply(res, parse_mode="md")
        return

    if cmd == "/interval":
        if not args:
            await event.reply(f"⏱️ الفاصل الحالي: {STATE['interval_s']} ثانية.")
            return
        try:
            val = float(args)
            if val < 0.1:
                val = 0.1
            STATE["interval_s"] = val
            _save_state()
            await event.reply(f"⏱️ تم تعديل الفاصل الزمني إلى: {val} ثانية.")
        except ValueError:
            await event.reply("❌ يرجى إدخال رقم صحيح.")
        return

    if cmd == "/mode":
        if args not in ["normal", "bullet", "human"]:
            await event.reply("❌ الأوضاع المتاحة هي: `normal` أو `bullet` أو `human`")
            return
        STATE["mode"] = args
        _save_state()
        await event.reply(f"⚙️ تم تغيير وضع الإرسال إلى: **{args}**")
        return

    if cmd == "/random":
        if args == "on":
            STATE["random_order"] = True
        elif args == "off":
            STATE["random_order"] = False
        else:
            await event.reply("❌ اختر `on` لتفعيل الوضع العشوائي أو `off` لإلغائه.")
            return
        _save_state()
        await event.reply(f"🔀 وضع الترتيب العشوائي: **{'مفعّل 🟢' if STATE['random_order'] else 'معطّل 🔴'}**")
        return

    if cmd == "/start_send":
        if not STATE["target_chat"]:
            await event.reply("❌ يجب تحديد الشات المستهدف أولاً باستخدام الأمر:\n`/chat @username`")
            return
        if not STATE["messages"]:
            await event.reply("❌ قائمة الرسائل فارغة! أضف رسائل أولاً عبر الأمر `/add`")
            return
        if STATE["running"]:
            await event.reply("🟢 الإرسال التلقائي يعمل بالفعل حالياً.")
            return

        STATE["running"] = True
        STATE["paused"] = False
        STATE["stop_requested"] = False
        _save_state()
        await event.reply("🚀 **بدأ الإرسال التلقائي الآن!** تابع النتيجة في الشات المستهدف.")
        asyncio.create_task(sending_loop())
        return

    if cmd == "/pause":
        if not STATE["running"]:
            await event.reply("🔴 البوت ليس في وضع إرسال حالياً لكي تقوم بإيقافه.")
            return
        STATE["paused"] = not STATE["paused"]
        _save_state()
        status_msg = "⏸️ تم إيقاف الإرسال مؤقتاً." if STATE["paused"] else "▶️ تم استئناف الإرسال التلقائي."
        await event.reply(status_msg)
        return

    if cmd == "/stop":
        if not STATE["running"]:
            await event.reply("🔴 البوت متوقف بالفعل.")
            return
        STATE["stop_requested"] = True
        await event.reply("🛑 جاري إيقاف عملية الإرسال وتصفير العدادات...")
        return

    if cmd == "/status":
        status_str = "🟢 يعمل" if STATE["running"] else "🔴 متوقف"
        if STATE["running"] and STATE["paused"]:
            status_str = "⏸️ موقوف مؤقتاً"
        
        info = f"📊 **حالة البوت الحالية:**\n\n"
        info += f"▪️ **الوضعية:** {status_str}\n"
        info += f"▪️ **الشات المستهدف:** `{STATE['target_chat'] or 'لم يحدد'}`\n"
        info += f"▪️ **الوضع الحالي:** `{STATE['mode']}`\n"
        info += f"▪️ **الفاصل الزمني:** {STATE['interval_s']} ثانية\n"
        info += f"▪️ **عدد الرسائل المضافة:** {len(STATE['messages'])}\n"
        await event.reply(info, parse_mode="md")
        return

    if cmd == "/reset":
        global DEFAULT_STATE
        STATE = deepcopy(DEFAULT_STATE)
        _save_state()
        _save_messages_to_file()
        await event.reply("⚙️ تم إعادة إعدادات البوت والرسائل إلى الوضع الافتراضي.")
        return

# ══════════════════════════════════════════════════════════════════════════════
#  حارس الشات (Guard)
# ══════════════════════════════════════════════════════════════════════════════
@client.on(events.NewMessage(incoming=True))
async def guard_handler(event):
    if not STATE["guard_active"] or not STATE["target_chat"]:
        return
    
    t_id = await get_chat_id(STATE["target_chat"])
    if not t_id or event.chat_id != t_id:
        return
        
    if is_admin(event.sender_id):
        return

    try:
        await event.delete()
    except MessageDeleteForbiddenError:
        pass
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════════════
#  حلقة الإرسال التلقائي الرئيسية (Sending Loop)
# ══════════════════════════════════════════════════════════════════════════════
async def sending_loop():
    log.info("🚀 بدأت حلقة الإرسال التلقائي التابعة لـ هادر بوت...")
    
    while STATE["running"]:
        if STATE["stop_requested"]:
            break
            
        if STATE["paused"]:
            await asyncio.sleep(1)
            continue

        all_done = True
        indices = list(range(len(STATE["messages"])))
        
        active_indices = []
        for idx in indices:
            msg_data = STATE["messages"][idx]
            if STATE["done_counts"][idx] < msg_data["repeat"]:
                all_done = False
                active_indices.append(idx)

        if all_done or not active_indices:
            log.info("✅ تم الانتهاء من إرسال جميع الرسائل المحددة وتكراراتها.")
            break

        if STATE["random_order"]:
            current_idx = random.choice(active_indices)
        else:
            current_idx = active_indices[0]

        msg_obj = STATE["messages"][current_idx]
        text_to_send = msg_obj["text"]
        target = STATE["target_chat"]

        try:
            if STATE["mode"] == "human":
                await client(SetTypingRequest(
                    peer=target,
                    action=SendMessageTypingAction(progress=0)
                ))
                await asyncio.sleep(len(text_to_send) * (STATE["human_speed_ms"] / 1000.0))

            await client.send_message(target, text_to_send)
            
            STATE["done_counts"][current_idx] += 1
            _save_state()

        except FloodWaitError as e:
            log.warning(f"⚠️ حظر مؤقت من تليجرام (FloodWait)، جاري الانتظار لـ {e.seconds} ثانية...")
            await asyncio.sleep(e.seconds)
            continue
        except Exception as e:
            log.error(f"❌ فشل إرسال الرسالة إلى الشات: {e}")
            await asyncio.sleep(2)

        if STATE["mode"] != "bullet" and STATE["running"] and not STATE["stop_requested"]:
            await asyncio.sleep(STATE["interval_s"])

    STATE["running"] = False
    STATE["stop_requested"] = False
    for i in range(len(STATE["done_counts"])):
        STATE["done_counts"][i] = 0
    _save_state()
    
    try:
        await client.send_message(config.CONTROL_CHAT, "🏁 **تم الانتهاء من عملية الإرسال التلقائي بالكامل وتصفير العدادات بنجاح!**")
    except Exception:
        pass
    log.info("🏁 انتهت عملية الإرسال التلقائي بنجاح وتم تصفير العدادات.")

# ══════════════════════════════════════════════════════════════════════════════
#  دالة التشغيل الرئيسية الحكيمة والمقاومة للكراش
# ══════════════════════════════════════════════════════════════════════════════
async def main():
    _load_messages()
    log.info("⚡ هادر بوت — بدأ التشغيل والاتصال بأمان...")
    
    try:
        # 1. الاتصال بالسيرفر
        await client.connect()
        
        # 2. تسجيل الدخول الصريح بالتوكن
        log.info("🔐 جاري تسجيل الدخول بالتوكن...")
        await client.sign_in(bot_token=config.BOT_TOKEN)
        
        me = await client.get_me()
        log.info(f"Logged in successfully: {me.first_name} (@{me.username})")
        
        # 3. إرسال تقرير التفعيل لجروب التحكم
        await client.send_message(
            config.CONTROL_CHAT,
            f"⚡ **هادر بوت شغّال بنجاح الآن! 🟢**\n\n"
            f"👤 **حساب البوت:** {me.first_name} (@{me.username})\n"
            f"⚙️ **التحكم:** أرسل أمر `/start` في هذا الشات لفتح لوحة الأوامر وتوجيه البوت.",
            parse_mode="md"
        )
        
        # بقاء السيرفر شغال يستقبل الأوامر بدون انقطاع
        await client.run_until_disconnected()

    except Exception as e:
        log.error(f"❌ حدث خطأ داخلي أثناء التشغيل: {e}")
        log.info("ℹ️ يرجى التأكد من صحة التوكن وآيدي CONTROL_CHAT المكتوبين لديك.")
        await asyncio.sleep(15)


if __name__ == "__main__":
    asyncio.run(main())
