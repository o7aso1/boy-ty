"""
⚡ هادر بوت — النسخة المستقرة كلياً (Aiogram)
تشتغل بالتوكن فقط وبدون الحاجة لـ API_ID أو API_HASH الشخصي نهائياً.
"""

import asyncio
import json
import os
import random
import logging
from copy import deepcopy
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
import config

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# تشغيل البوت بالتوكن فقط وتخطي تعقيدات تليجرام
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# ══════════════════════════════════════════════════════════════════════════════
#  الحالة الكاملة والملفات
# ══════════════════════════════════════════════════════════════════════════════
DEFAULT_STATE = {
    "running": False,
    "paused": False,
    "target_chat": None,
    "messages": [],       # [{text, repeat}]
    "done_counts": [],
    "interval_s": 2.0,
    "mode": "normal",     # normal | bullet | human
}

STATE = deepcopy(DEFAULT_STATE)
STATE_FILE = "bot_state.json"
MESSAGES_FILE = "messages.json"

def _load_all():
    global STATE
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                for k, v in saved.items():
                    if k in STATE: STATE[k] = v
        except Exception: pass
    if os.path.exists(MESSAGES_FILE):
        try:
            with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
                STATE["messages"] = json.load(f)
            STATE["done_counts"] = [0] * len(STATE["messages"])
        except Exception: pass

def _save_all():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(STATE, f, ensure_ascii=False, indent=2)
        with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump(STATE["messages"], f, ensure_ascii=False, indent=2)
    except Exception: pass

_load_all()

def is_admin(user_id: int) -> bool:
    return str(user_id) == str(config.ADMIN_ID)

# ══════════════════════════════════════════════════════════════════════════════
#  الأوامر ولوحة التحكم
# ══════════════════════════════════════════════════════════════════════════════
@dp.message(Command("start", "help"))
async def cmd_start(message: Message):
    if not is_admin(message.from_user.id): return
    help_text = """⚡ **لوحة تحكم هادر بوت (النسخة المستقرة)**

**📌 الإعدادات:**
`/chat @username` — تحديد القروب المستهدف (أو آيدي)
`/add تكرار | النص` — إضافة رسالة (مثال: `/add 5 | هلا بالعيال`)
`/clear_msg` — مسح كل الرسائل
`/list` — عرض الرسائل الحالية

**⏱️ خيارات متقدمة:**
`/interval 2` — الفاصل الزمني بالثواني
`/mode normal|bullet|human` — وضع الإرسال

**🟢 التشغيل والتحكم:**
`/start_send` — ابدأ الإرسال التلقائي
`/pause` — إيقاف مؤقت / استئناف
`/stop` — إيقاف نهائي وتصفير العدادات
`/status` — عرض الحالة الحالية
"""
    await message.reply(help_text, parse_mode="Markdown")

@dp.message(Command("chat"))
async def cmd_chat(message: Message):
    if not is_admin(message.from_user.id): return
    args = message.text.replace("/chat", "").strip()
    if not args:
        await message.reply("❌ اكتب يوزر الشات أو الآيدي بعد الأمر.")
        return
    STATE["target_chat"] = args
    _save_all()
    await message.reply(f"🎯 تم تحديد الشات المستهدف: `{args}`", parse_mode="Markdown")

@dp.message(Command("add"))
async def cmd_add(message: Message):
    if not is_admin(message.from_user.id): return
    args = message.text.replace("/add", "").strip()
    if " | " not in args:
        await message.reply("❌ الاستخدام: `/add التكرار | النص`")
        return
    rep_part, text_part = args.split(" | ", 1)
    try:
        repeat = int(rep_part)
        STATE["messages"].append({"text": text_part, "repeat": repeat})
        STATE["done_counts"].append(0)
        _save_all()
        await message.reply(f"✅ تمت الإضافة بنجاح وتكرارها: {repeat}")
    except ValueError:
        await message.reply("❌ التكرار يجب أن يكون رقماً.")

@dp.message(Command("clear_msg"))
async def cmd_clear(message: Message):
    if not is_admin(message.from_user.id): return
    STATE["messages"] = []
    STATE["done_counts"] = []
    _save_all()
    await message.reply("🗑️ تم مسح جميع الرسائل.")

@dp.message(Command("list"))
async def cmd_list(message: Message):
    if not is_admin(message.from_user.id): return
    if not STATE["messages"]:
        await message.reply("📋 القائمة فارغة حالياً.")
        return
    res = "📋 **الرسائل الحالية:**\n\n"
    for i, m in enumerate(STATE["messages"]):
        res += f"{i+1}. `{m['text']}` (تكرار: {m['repeat']})\n"
    await message.reply(res, parse_mode="Markdown")

@dp.message(Command("interval"))
async def cmd_interval(message: Message):
    if not is_admin(message.from_user.id): return
    args = message.text.replace("/interval", "").strip()
    try:
        val = float(args)
        STATE["interval_s"] = max(0.1, val)
        _save_all()
        await message.reply(f"⏱️ تم تعديل الفاصل إلى: {STATE['interval_s']} ثانية.")
    except ValueError:
        await message.reply("❌ أدخل رقماً صحيحاً.")

@dp.message(Command("mode"))
async def cmd_mode(message: Message):
    if not is_admin(message.from_user.id): return
    args = message.text.replace("/mode", "").strip()
    if args not in ["normal", "bullet", "human"]:
        await message.reply("❌ الأوضاع المتاحة: `normal`, `bullet`, `human`")
        return
    STATE["mode"] = args
    _save_all()
    await message.reply(f"⚙️ تم تغيير الوضع إلى: **{args}**", parse_mode="Markdown")

@dp.message(Command("start_send"))
async def cmd_start_send(message: Message):
    if not is_admin(message.from_user.id): return
    if not STATE["target_chat"]:
        await message.reply("❌ حدد الشات المستهدف أولاً بـ `/chat`")
        return
    if not STATE["messages"]:
        await message.reply("❌ القائمة فارغة! أضف رسائل بـ `/add`")
        return
    if STATE["running"]:
        await message.reply("🟢 الإرسال يعمل بالفعل.")
        return

    STATE["running"] = True
    STATE["paused"] = False
    _save_all()
    await message.reply("🚀 **بدأ الإرسال التلقائي الآن!**")
    asyncio.create_task(sending_loop())

@dp.message(Command("pause"))
async def cmd_pause(message: Message):
    if not is_admin(message.from_user.id): return
    if not STATE["running"]: return
    STATE["paused"] = not STATE["paused"]
    _save_all()
    await message.reply("⏸️ تم الإيقاف مؤقتاً." if STATE["paused"] else "▶️ تم الاستئناف.")

@dp.message(Command("stop"))
async def cmd_stop(message: Message):
    if not is_admin(message.from_user.id): return
    STATE["running"] = False
    await message.reply("🛑 جاري إيقاف عملية الإرسال وتصفير العدادات...")

@dp.message(Command("status"))
async def cmd_status(message: Message):
    if not is_admin(message.from_user.id): return
    status_str = "🟢 يعمل" if STATE["running"] else "🔴 متوقف"
    if STATE["running"] and STATE["paused"]: status_str = "⏸️ موقوف مؤقتاً"
    info = f"📊 **الحالة:** {status_str}\n🎯 **الهدف:** `{STATE['target_chat']}`\n⏱️ **الفاصل:** {STATE['interval_s']} ثانية"
    await message.reply(info, parse_mode="Markdown")

# ══════════════════════════════════════════════════════════════════════════════
#  حلقة الإرسال التلقائي المستقرة
# ══════════════════════════════════════════════════════════════════════════════
async def sending_loop():
    while STATE["running"]:
        if STATE["paused"]:
            await asyncio.sleep(1)
            continue

        active_indices = [i for i, m in enumerate(STATE["messages"]) if STATE["done_counts"][i] < m["repeat"]]
        if not active_indices:
            break

        current_idx = active_indices[0]
        msg_obj = STATE["messages"][current_idx]
        text_to_send = msg_obj["text"]
        target = STATE["target_chat"]

        try:
            if STATE["mode"] == "human":
                await bot.send_chat_action(chat_id=target, action="typing")
                await asyncio.sleep(len(text_to_send) * 0.1)

            await bot.send_message(chat_id=target, text=text_to_send)
            STATE["done_counts"][current_idx] += 1
            _save_all()
        except Exception as e:
            log.error(f"Error sending: {e}")
            await asyncio.sleep(4)

        if STATE["mode"] != "bullet" and STATE["running"]:
            await asyncio.sleep(STATE["interval_s"])

    STATE["running"] = False
    for i in range(len(STATE["done_counts"])): STATE["done_counts"][i] = 0
    _save_all()
    try:
        await bot.send_message(chat_id=config.CONTROL_CHAT, text="🏁 **تم الانتهاء من عملية الإرسال التلقائي بالكامل!**")
    except Exception: pass

# ══════════════════════════════════════════════════════════════════════════════
#  بدء التشغيل
# ══════════════════════════════════════════════════════════════════════════════
async def main():
    log.info("🟢 جاري تشغيل البوت عبر Aiogram بشكل مستقر تماماً...")
    try:
        bot_info = await bot.get_me()
        log.info(f"Bot Started: @{bot_info.username}")
        await bot.send_message(chat_id=config.CONTROL_CHAT, text=f"⚡ **هادر بوت شغّال بنجاح الآن عبر السيرفر المستقر! 🟢**\n\n👤 البوت: @{bot_info.username}")
        await dp.start_polling(bot)
    except Exception as e:
        log.error(f"Fatal Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())

async def main():
    log.info("🟢 جاري تشغيل البوت عبر Aiogram بشكل مستقر تماماً...")
    
    # فحص هل التوكن مقروء أصلاً أم لا
    token_check = getattr(config, "BOT_TOKEN", None)
    if not token_check:
        log.error("❌ خطأ كاشف: ملف config.py لا يحتوي على متغير باسم BOT_TOKEN أو قيمته فارغة!")
    else:
        log.info(f"🔍 التوكن المستخدم يبدأ بـ: {str(token_check)[:5]}... (طوله: {len(token_check)} حرف)")

    try:
        bot_info = await bot.get_me()
        log.info(f"Bot Started: @{bot_info.username}")
        await bot.send_message(chat_id=config.CONTROL_CHAT, text=f"⚡ **هادر بوت شغّال بنجاح الآن! 🟢**\n\n👤 البوت: @{bot_info.username}")
        await dp.start_polling(bot)
    except Exception as e:
        log.error(f"Fatal Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
