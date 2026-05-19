"""
⚡ هادر بوت — النسخة الاحترافية الكاملة
متعدد المستخدمين + أزرار تفاعلية + حفظ دائم + جميع المميزات
"""

import asyncio
import json
import os
import random
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery
)
from aiohttp import web
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, FloodWaitError
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

# ── ملفات الحفظ الدائم ──────────────────────────────────────────────────────
DATA_FILE    = "user_data.json"
SESSIONS_DIR = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

# ── بيانات في الذاكرة ───────────────────────────────────────────────────────
PENDING_LOGINS = {}    # بيانات تسجيل الدخول المؤقتة
ACTIVE_TASKS   = {}    # مهام الإرسال الشغّالة
GUARD_TASKS    = {}    # مهام حارس الشات
AWAITING       = {}    # انتظار إدخال من المستخدم

# ══════════════════════════════════════════════════════════════════════════════
#  الحفظ الدائم
# ══════════════════════════════════════════════════════════════════════════════

def _load_data() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _get_user(uid: str) -> dict:
    data = _load_data()
    if uid not in data:
        data[uid] = {
            "messages":      [],     # [{text, repeat}]
            "target":        None,   # الشات المستهدف
            "interval_s":    3.0,    # الفاصل الزمني
            "mode":          "normal",# normal | bullet | human
            "human_delay_ms":80,
            "random_order":  False,
            "separator":     None,
            "suffix_on":     False,
            "suffix_text":   "",
            "suffix_pos":    "end",  # start | mid | end
            "reply_on":      False,
        }
        _save_data(data)
    return data[uid]

def _update_user(uid: str, key: str, value):
    data = _load_data()
    if uid not in data:
        _get_user(uid)
        data = _load_data()
    data[uid][key] = value
    _save_data(data)

def _update_user_many(uid: str, updates: dict):
    data = _load_data()
    if uid not in data:
        _get_user(uid)
        data = _load_data()
    data[uid].update(updates)
    _save_data(data)

# ══════════════════════════════════════════════════════════════════════════════
#  دوال مساعدة
# ══════════════════════════════════════════════════════════════════════════════

def _mode_ar(mode: str) -> str:
    return {"normal":"عادي ⚡","bullet":"رصاصة 🚀","human":"بشري ✍️"}.get(mode, "عادي")

def _apply_msg(text: str, cfg: dict) -> str:
    if cfg.get("separator"):
        words = text.split()
        if len(words) > 1:
            text = f" {cfg['separator']} ".join(words)
    if cfg.get("suffix_on") and cfg.get("suffix_text"):
        extra = cfg["suffix_text"].strip()
        pos   = cfg.get("suffix_pos", "end")
        if pos == "start":
            text = extra + " " + text
        elif pos == "mid":
            words = text.split()
            mid   = len(words) // 2
            words.insert(mid, extra)
            text  = " ".join(words)
        else:
            text = text + " " + extra
    return text

def _status_text(uid: str) -> str:
    cfg   = _get_user(uid)
    msgs  = cfg["messages"]
    total = sum(m["repeat"] for m in msgs)
    running = uid in ACTIVE_TASKS
    guard   = uid in GUARD_TASKS
    suf = (
        f"{cfg['suffix_text'][:20]} ({cfg['suffix_pos']})"
        if cfg["suffix_on"] else "—"
    )
    lines = [
        "┌─ <b>⚡ هادر بوت — لوحة التحكم</b>",
        f"│ الحالة  : {'▶ شغّال' if running else '⏹ متوقف'}",
        f"│ الشات   : <code>{cfg['target'] or '—'}</code>",
        f"│ الرسائل : {len(msgs)} نوع | {total} إجمالي",
        f"│ الوضع   : {_mode_ar(cfg['mode'])}",
        f"│ الفاصل  : {cfg['interval_s']}s",
        f"│ عشوائي  : {'✅' if cfg['random_order'] else '❌'}",
        f"│ فاصل    : {cfg['separator'] or '—'}",
        f"│ نص إضافي: {suf}",
        f"│ ردّ     : {'✅' if cfg['reply_on'] else '❌'}",
        f"└ حارس    : {'🛡 شغّال' if guard else '💤 موقوف'}",
    ]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
#  لوحات المفاتيح
# ══════════════════════════════════════════════════════════════════════════════

def kb_main(uid: str) -> InlineKeyboardMarkup:
    cfg     = _get_user(uid)
    running = uid in ACTIVE_TASKS
    guard   = uid in GUARD_TASKS
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="▶ ابدأ" if not running else "⏹ وقف",
                                 callback_data="toggle_send"),
            InlineKeyboardButton(text="⏸ مؤقت",  callback_data="pause"),
        ],
        [
            InlineKeyboardButton(text="📋 الرسائل",   callback_data="menu_msgs"),
            InlineKeyboardButton(text="⚙️ الإعدادات", callback_data="menu_settings"),
        ],
        [
            InlineKeyboardButton(
                text="🛡 حارس شغّال 🔴" if guard else "🛡 حارس الشات",
                callback_data="toggle_guard"),
            InlineKeyboardButton(text="🗑 احذف رسائلي", callback_data="delete_mine"),
        ],
        [
            InlineKeyboardButton(text="📊 الحالة", callback_data="status"),
            InlineKeyboardButton(text="❓ مساعدة", callback_data="help"),
        ],
    ])

def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")]
    ])

def kb_msgs() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ أضف رسالة",   callback_data="add_msg"),
            InlineKeyboardButton(text="📋 عرض الرسائل", callback_data="list_msgs"),
        ],
        [
            InlineKeyboardButton(text="🗑 امسح الكل",  callback_data="clear_msgs"),
            InlineKeyboardButton(text="💾 تصدير JSON", callback_data="export_msgs"),
        ],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")],
    ])

def kb_settings(uid: str) -> InlineKeyboardMarkup:
    cfg = _get_user(uid)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎯 الشات المستهدف",  callback_data="set_target"),
            InlineKeyboardButton(text="⏱ الفاصل الزمني",   callback_data="set_interval"),
        ],
        [
            InlineKeyboardButton(text="⚡ عادي",   callback_data="mode_normal"),
            InlineKeyboardButton(text="🚀 رصاصة",  callback_data="mode_bullet"),
            InlineKeyboardButton(text="✍️ بشري",  callback_data="mode_human"),
        ],
        [
            InlineKeyboardButton(
                text=f"🎲 عشوائي {'✅' if cfg['random_order'] else '❌'}",
                callback_data="toggle_random"),
            InlineKeyboardButton(text="🔗 فاصل الكلمات", callback_data="set_sep"),
        ],
        [
            InlineKeyboardButton(text="📌 نص إضافي",      callback_data="set_suffix"),
            InlineKeyboardButton(
                text=f"↩️ ردّ {'✅' if cfg['reply_on'] else '❌'}",
                callback_data="toggle_reply"),
        ],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="main_menu")],
    ])

def kb_suffix_pos() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="▶ بداية", callback_data="sufpos_start"),
            InlineKeyboardButton(text="⏺ وسط",  callback_data="sufpos_mid"),
            InlineKeyboardButton(text="◀ نهاية", callback_data="sufpos_end"),
        ],
        [InlineKeyboardButton(text="🚫 إيقاف النص الإضافي", callback_data="suffix_off")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="menu_settings")],
    ])

def kb_login(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔗 ربط حسابك الشخصي",
            web_app=types.WebAppInfo(url=url)
        )],
        [InlineKeyboardButton(text="📊 الحالة", callback_data="status")],
    ])

# ══════════════════════════════════════════════════════════════════════════════
#  أوامر البوت
# ══════════════════════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid    = str(message.from_user.id)
    _get_user(uid)  # يُنشئ بيانات المستخدم إذا ما كانت موجودة
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if domain and not domain.startswith("http"):
        web_url = f"https://{domain}/login-page"
    elif domain:
        web_url = f"{domain}/login-page"
    else:
        web_url = "https://your-app.up.railway.app/login-page"

    session_exists = os.path.exists(f"{SESSIONS_DIR}/user_{uid}.session")
    status_icon    = "🟢 مربوط" if session_exists else "🔴 غير مربوط"

    text = (
        f"<b>⚡ أهلاً بك في هادر بوت!</b>\n\n"
        f"الحساب: {status_icon}\n\n"
        f"{'👇 استخدم الأزرار أدناه للتحكم الكامل' if session_exists else '👇 ابدأ بربط حسابك أولاً'}"
    )

    if session_exists:
        await message.answer(text, parse_mode="HTML", reply_markup=kb_main(uid))
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb_login(web_url))


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    uid = str(message.from_user.id)
    await message.answer(_status_text(uid), parse_mode="HTML", reply_markup=kb_main(uid))


# ── استقبال الإدخال النصي ────────────────────────────────────────────────────
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    uid = str(message.from_user.id)
    aw  = AWAITING.get(uid)
    if not aw:
        return

    text = message.text.strip()
    del AWAITING[uid]

    # ── إضافة رسالة ──
    if aw == "add_msg":
        import re
        m = re.match(r"^(\d+)\s*\|\s*(.+)$", text, re.DOTALL)
        if m:
            repeat, msg_text = int(m.group(1)), m.group(2).strip()
        else:
            repeat, msg_text = 1, text

        data   = _load_data()
        msgs   = data[uid]["messages"]
        msgs.append({"text": msg_text, "repeat": repeat})
        data[uid]["messages"] = msgs
        _save_data(data)
        await message.answer(
            f"✅ رسالة #{len(msgs)} أُضيفت (×{repeat})\n<code>{msg_text[:80]}</code>",
            parse_mode="HTML",
            reply_markup=kb_msgs()
        )

    elif aw == "set_target":
        _update_user(uid, "target", text)
        await message.answer(
            f"✅ الشات المستهدف: <code>{text}</code>",
            parse_mode="HTML",
            reply_markup=kb_settings(uid)
        )

    elif aw == "set_interval":
        try:
            raw = text.lower()
            if raw.endswith("ms"):
                s = float(raw[:-2]) / 1000
            elif raw.endswith("m"):
                s = float(raw[:-1]) * 60
            elif raw.endswith("s"):
                s = float(raw[:-1])
            else:
                s = float(raw)
            _update_user(uid, "interval_s", max(0.0, s))
            await message.answer(
                f"✅ الفاصل = <code>{max(0.0,s)}s</code>",
                parse_mode="HTML",
                reply_markup=kb_settings(uid)
            )
        except ValueError:
            await message.answer("❌ مثال: <code>2s</code> أو <code>500ms</code> أو <code>1m</code>",
                                 parse_mode="HTML")
            AWAITING[uid] = "set_interval"

    elif aw == "set_sep":
        val = None if text.lower() == "off" else text
        _update_user(uid, "separator", val)
        await message.answer(
            f"✅ فاصل الكلمات = <code>{val or 'off'}</code>",
            parse_mode="HTML",
            reply_markup=kb_settings(uid)
        )

    elif aw == "set_suffix_text":
        _update_user(uid, "suffix_text", text)
        _update_user(uid, "suffix_on",   True)
        await message.answer(
            f"✅ النص الإضافي = <code>{text}</code>\nاختر الموضع:",
            parse_mode="HTML",
            reply_markup=kb_suffix_pos()
        )

    elif aw == "del_msg_idx":
        try:
            idx = int(text) - 1
            data = _load_data()
            msgs = data[uid]["messages"]
            if 0 <= idx < len(msgs):
                removed = msgs.pop(idx)
                data[uid]["messages"] = msgs
                _save_data(data)
                await message.answer(
                    f"✅ حُذفت: <code>{removed['text'][:50]}</code>",
                    parse_mode="HTML",
                    reply_markup=kb_msgs()
                )
            else:
                await message.answer("❌ رقم غير صحيح.", reply_markup=kb_msgs())
        except ValueError:
            await message.answer("❌ أدخل رقماً صحيحاً.", reply_markup=kb_msgs())

    elif aw == "human_speed":
        try:
            ms = max(0, int(text))
            _update_user(uid, "human_delay_ms", ms)
            await message.answer(
                f"✅ سرعة البشري = <code>{ms}ms</code>",
                parse_mode="HTML",
                reply_markup=kb_settings(uid)
            )
        except ValueError:
            await message.answer("❌ أدخل رقماً (0 = أقصى سرعة)")
            AWAITING[uid] = "human_speed"


# ── استقبال ملف JSON ─────────────────────────────────────────────────────────
@dp.message(F.document)
async def handle_file(message: Message):
    uid = str(message.from_user.id)
    if not message.document.file_name.endswith(".json"):
        return
    file = await bot.download(message.document)
    try:
        msgs_raw = json.loads(file.read().decode("utf-8"))
        normalized = []
        for m in msgs_raw:
            if isinstance(m, dict):
                t = m.get("text") or m.get("message", "")
                r = m.get("repeat", 1)
                if t: normalized.append({"text": t, "repeat": r})
            elif isinstance(m, str):
                normalized.append({"text": m, "repeat": 1})
        if not normalized:
            raise ValueError

        await message.answer(
            f"📂 <b>{len(normalized)} رسالة</b> — أضف أم استبدل؟",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="➕ أضف للموجودين", callback_data=f"file_append"),
                    InlineKeyboardButton(text="🔄 استبدل الكل",   callback_data=f"file_replace"),
                ]
            ])
        )
        AWAITING[uid] = {"type": "file", "msgs": normalized}
    except Exception:
        await message.answer("❌ صيغة الملف غير صحيحة.")


# ══════════════════════════════════════════════════════════════════════════════
#  Callback Handlers (الأزرار)
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query()
async def cb_handler(cb: CallbackQuery):
    uid  = str(cb.from_user.id)
    data = cb.data
    cfg  = _get_user(uid)

    await cb.answer()

    # ── القائمة الرئيسية ──
    if data == "main_menu":
        await cb.message.edit_text(
            _status_text(uid), parse_mode="HTML", reply_markup=kb_main(uid))

    elif data == "status":
        await cb.message.edit_text(
            _status_text(uid), parse_mode="HTML", reply_markup=kb_main(uid))

    # ── تشغيل/إيقاف الإرسال ──
    elif data == "toggle_send":
        if uid in ACTIVE_TASKS:
            ACTIVE_TASKS[uid].cancel()
            del ACTIVE_TASKS[uid]
            await cb.message.edit_text(
                "⏹ <b>تم الإيقاف.</b>", parse_mode="HTML", reply_markup=kb_main(uid))
        else:
            if not cfg["target"]:
                await cb.answer("❌ حدد الشات أولاً من الإعدادات", show_alert=True); return
            if not cfg["messages"]:
                await cb.answer("❌ أضف رسائل أولاً", show_alert=True); return
            if not os.path.exists(f"{SESSIONS_DIR}/user_{uid}.session"):
                await cb.answer("❌ ربط حسابك أولاً عبر /start", show_alert=True); return
            task = asyncio.create_task(_send_loop(uid))
            ACTIVE_TASKS[uid] = task
            await cb.message.edit_text(
                "▶️ <b>بدأ الإرسال!</b>\nاضغط ⏹ وقف للإيقاف.",
                parse_mode="HTML", reply_markup=kb_main(uid))

    elif data == "pause":
        if uid in ACTIVE_TASKS:
            key = f"paused_{uid}"
            if AWAITING.get(key):
                del AWAITING[key]
                await cb.answer("▶️ استئناف")
            else:
                AWAITING[key] = True
                await cb.answer("⏸ إيقاف مؤقت")
        else:
            await cb.answer("❌ لا يوجد إرسال نشط")

    # ── حارس الشات ──
    elif data == "toggle_guard":
        if not cfg["target"]:
            await cb.answer("❌ حدد الشات أولاً", show_alert=True); return
        if uid in GUARD_TASKS:
            GUARD_TASKS[uid].cancel()
            del GUARD_TASKS[uid]
            await cb.message.edit_text(
                "🛡 حارس الشات: <b>أُوقف.</b>",
                parse_mode="HTML", reply_markup=kb_main(uid))
        else:
            task = asyncio.create_task(_guard_loop(uid))
            GUARD_TASKS[uid] = task
            await cb.message.edit_text(
                "🛡 حارس الشات: <b>شغّال 🔴</b>\nيحذف رسائل الطرف الثاني فوراً.",
                parse_mode="HTML", reply_markup=kb_main(uid))

    # ── حذف رسائلي ──
    elif data == "delete_mine":
        if not cfg["target"]:
            await cb.answer("❌ حدد الشات أولاً", show_alert=True); return
        await cb.message.edit_text("🗑 جاري حذف رسائلك...", parse_mode="HTML")
        asyncio.create_task(_delete_mine_task(uid, cb.message))

    # ── قائمة الرسائل ──
    elif data == "menu_msgs":
        await cb.message.edit_text(
            "📋 <b>إدارة الرسائل</b>", parse_mode="HTML", reply_markup=kb_msgs())

    elif data == "add_msg":
        AWAITING[uid] = "add_msg"
        await cb.message.edit_text(
            "✏️ أرسل نص الرسالة:\n"
            "• رسالة عادية: <code>مرحبا</code>\n"
            "• مع تكرار: <code>5 | مرحبا</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ إلغاء", callback_data="menu_msgs")]
            ])
        )

    elif data == "list_msgs":
        msgs = cfg["messages"]
        if not msgs:
            await cb.message.edit_text(
                "📭 لا توجد رسائل.", reply_markup=kb_msgs()); return
        lines = ["📋 <b>الرسائل المحفوظة:</b>\n"]
        for i, m in enumerate(msgs, 1):
            lines.append(f"<code>{i}.</code> ×{m['repeat']} — {m['text'][:45].replace(chr(10),' ')}")
        lines.append("\nلحذف رسالة: اضغط زر الحذف أدناه")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🗑 احذف رسالة رقم...", callback_data="prompt_del_msg")],
            [InlineKeyboardButton(text="🔙 رجوع", callback_data="menu_msgs")]
        ])
        await cb.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)

    elif data == "prompt_del_msg":
        AWAITING[uid] = "del_msg_idx"
        await cb.message.edit_text(
            "🗑 أرسل رقم الرسالة المراد حذفها:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ إلغاء", callback_data="list_msgs")]
            ])
        )

    elif data == "clear_msgs":
        _update_user(uid, "messages", [])
        await cb.message.edit_text(
            "✅ تم مسح جميع الرسائل.", reply_markup=kb_msgs())

    elif data == "export_msgs":
        msgs = cfg["messages"]
        if not msgs:
            await cb.answer("❌ لا توجد رسائل", show_alert=True); return
        text = json.dumps(msgs, ensure_ascii=False, indent=2)
        fname = f"/tmp/msgs_{uid}.json"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(text)
        await bot.send_document(cb.from_user.id, types.FSInputFile(fname),
                                caption="📦 ملف رسائلك")
        os.remove(fname)
        await cb.answer("✅ تم الإرسال")

    # ── قائمة الإعدادات ──
    elif data == "menu_settings":
        await cb.message.edit_text(
            f"⚙️ <b>الإعدادات</b>\n"
            f"الوضع الحالي: {_mode_ar(cfg['mode'])} | الفاصل: {cfg['interval_s']}s",
            parse_mode="HTML",
            reply_markup=kb_settings(uid)
        )

    elif data == "set_target":
        AWAITING[uid] = "set_target"
        await cb.message.edit_text(
            "🎯 أرسل <b>@username</b> أو رقم ID للشات المستهدف:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ إلغاء", callback_data="menu_settings")]
            ])
        )

    elif data == "set_interval":
        AWAITING[uid] = "set_interval"
        await cb.message.edit_text(
            "⏱ أرسل الفاصل الزمني:\n"
            "<code>2s</code> = ثانيتان\n"
            "<code>500ms</code> = نصف ثانية\n"
            "<code>1m</code> = دقيقة\n"
            "<code>0</code> = بلا فاصل",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ إلغاء", callback_data="menu_settings")]
            ])
        )

    elif data in ("mode_normal", "mode_bullet", "mode_human"):
        mode = data.replace("mode_", "")
        _update_user(uid, "mode", mode)
        if mode == "human":
            await cb.message.edit_text(
                f"✅ الوضع = {_mode_ar(mode)}\n"
                "أرسل سرعة الكتابة بالـ ms (0 = أقصى سرعة):",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="80ms (افتراضي)", callback_data="hs_80"),
                     InlineKeyboardButton(text="0ms (أقصى)",     callback_data="hs_0"),
                     InlineKeyboardButton(text="تخصيص",          callback_data="hs_custom")],
                    [InlineKeyboardButton(text="🔙 رجوع", callback_data="menu_settings")],
                ])
            )
        else:
            await cb.message.edit_text(
                f"✅ الوضع = {_mode_ar(mode)}",
                parse_mode="HTML",
                reply_markup=kb_settings(uid)
            )

    elif data in ("hs_80", "hs_0"):
        ms = 80 if data == "hs_80" else 0
        _update_user(uid, "human_delay_ms", ms)
        await cb.message.edit_text(
            f"✅ سرعة البشري = <code>{ms}ms</code>",
            parse_mode="HTML",
            reply_markup=kb_settings(uid)
        )

    elif data == "hs_custom":
        AWAITING[uid] = "human_speed"
        await cb.message.edit_text(
            "✏️ أرسل قيمة المللي ثانية (0–2000):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ إلغاء", callback_data="menu_settings")]
            ])
        )

    elif data == "toggle_random":
        new_val = not cfg["random_order"]
        _update_user(uid, "random_order", new_val)
        await cb.message.edit_text(
            f"✅ الترتيب العشوائي = {'✅ مفعّل' if new_val else '❌ معطّل'}",
            parse_mode="HTML",
            reply_markup=kb_settings(uid)
        )

    elif data == "set_sep":
        AWAITING[uid] = "set_sep"
        await cb.message.edit_text(
            "🔗 أرسل الفاصل بين الكلمات:\n"
            "مثال: <code>*</code> أو <code>#</code>\n"
            "لإيقافه: <code>off</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ إلغاء", callback_data="menu_settings")]
            ])
        )

    elif data == "set_suffix":
        AWAITING[uid] = "set_suffix_text"
        await cb.message.edit_text(
            "📌 أرسل النص الإضافي الذي تريد إضافته:\n"
            "مثال: <code>#هادر</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚫 إيقاف النص الإضافي", callback_data="suffix_off")],
                [InlineKeyboardButton(text="❌ إلغاء", callback_data="menu_settings")],
            ])
        )

    elif data == "suffix_off":
        _update_user_many(uid, {"suffix_on": False, "suffix_text": ""})
        await cb.message.edit_text(
            "✅ النص الإضافي معطّل.",
            reply_markup=kb_settings(uid)
        )

    elif data.startswith("sufpos_"):
        pos = data.replace("sufpos_", "")
        _update_user(uid, "suffix_pos", pos)
        cfg = _get_user(uid)
        await cb.message.edit_text(
            f"✅ النص الإضافي: <code>{cfg['suffix_text']}</code> في <b>{pos}</b>",
            parse_mode="HTML",
            reply_markup=kb_settings(uid)
        )

    elif data == "toggle_reply":
        new_val = not cfg["reply_on"]
        _update_user(uid, "reply_on", new_val)
        await cb.message.edit_text(
            f"✅ الرد على آخر رسالة = {'✅ مفعّل' if new_val else '❌ معطّل'}",
            parse_mode="HTML",
            reply_markup=kb_settings(uid)
        )

    # ── ملف JSON ──
    elif data in ("file_append", "file_replace"):
        pending = AWAITING.get(uid)
        if isinstance(pending, dict) and pending.get("type") == "file":
            new_msgs = pending["msgs"]
            del AWAITING[uid]
            d = _load_data()
            if data == "file_append":
                d[uid]["messages"].extend(new_msgs)
                txt = f"✅ أُضيف {len(new_msgs)} رسائل."
            else:
                d[uid]["messages"] = new_msgs
                txt = f"✅ استُبدل بـ {len(new_msgs)} رسائل."
            _save_data(d)
            await cb.message.edit_text(txt, reply_markup=kb_msgs())

    # ── مساعدة ──
    elif data == "help":
        await cb.message.edit_text(HELP_TEXT, parse_mode="HTML", reply_markup=kb_back())


# ══════════════════════════════════════════════════════════════════════════════
#  حلقة الإرسال
# ══════════════════════════════════════════════════════════════════════════════

async def _send_loop(uid: str):
    session_path = f"{SESSIONS_DIR}/user_{uid}"
    tg = TelegramClient(session_path, config.API_ID, config.API_HASH)
    await tg.connect()
    if not await tg.is_user_authorized():
        await bot.send_message(uid,
            "⚠️ <b>انتهت صلاحية جلسة حسابك! أعد الربط من /start</b>",
            parse_mode="HTML")
        await tg.disconnect()
        ACTIVE_TASKS.pop(uid, None)
        return

    log.info(f"Send loop started for {uid}")
    cfg        = _get_user(uid)
    msgs       = cfg["messages"]
    done       = [0] * len(msgs)
    total      = sum(m["repeat"] for m in msgs)
    me         = await tg.get_me()

    try:
        while True:
            # إيقاف مؤقت
            while AWAITING.get(f"paused_{uid}"):
                await asyncio.sleep(0.3)

            cfg   = _get_user(uid)  # أعد القراءة (للتعديلات الحية)
            msgs  = cfg["messages"]
            total = sum(m["repeat"] for m in msgs)

            if sum(done) >= total or not msgs:
                break

            available = [i for i, m in enumerate(msgs) if done[i] < m["repeat"]]
            if not available:
                break

            i    = random.choice(available) if cfg["random_order"] else available[0]
            text = _apply_msg(msgs[i]["text"], cfg)

            try:
                reply_to = None
                if cfg["reply_on"]:
                    async for msg in tg.iter_messages(cfg["target"], limit=20):
                        if msg.sender_id != me.id:
                            reply_to = msg.id; break

                if cfg["mode"] == "human" and cfg["human_delay_ms"] > 0:
                    await tg.action(cfg["target"], "typing")
                    delay = min(len(text) * cfg["human_delay_ms"] / 1000, 6.0)
                    await asyncio.sleep(delay)

                await tg.send_message(cfg["target"], text, reply_to=reply_to)
                done[i] += 1
                log.info(f"[{uid}] Sent [{i+1}] ({done[i]}/{msgs[i]['repeat']})")

                if cfg["mode"] == "bullet":
                    await asyncio.sleep(0.02)
                elif cfg["interval_s"] > 0:
                    await asyncio.sleep(cfg["interval_s"])

            except FloodWaitError as e:
                log.warning(f"[{uid}] FloodWait {e.seconds}s")
                await bot.send_message(uid,
                    f"⚠️ Telegram طلب انتظار <b>{e.seconds} ثانية</b>...",
                    parse_mode="HTML")
                await asyncio.sleep(e.seconds + 2)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(f"[{uid}] Send error: {e}")
                await asyncio.sleep(2)

    except asyncio.CancelledError:
        pass
    finally:
        await tg.disconnect()
        ACTIVE_TASKS.pop(uid, None)
        total_done = sum(done)
        await bot.send_message(uid,
            f"✅ <b>انتهى الإرسال!</b> أُرسلت <code>{total_done}</code> رسالة.",
            parse_mode="HTML",
            reply_markup=kb_main(uid))
        log.info(f"Send loop ended for {uid} — sent {total_done}")


# ══════════════════════════════════════════════════════════════════════════════
#  حارس الشات
# ══════════════════════════════════════════════════════════════════════════════

async def _guard_loop(uid: str):
    session_path = f"{SESSIONS_DIR}/user_{uid}"
    tg = TelegramClient(session_path, config.API_ID, config.API_HASH)
    await tg.connect()
    if not await tg.is_user_authorized():
        await bot.send_message(uid, "⚠️ جلسة منتهية! أعد الربط.", parse_mode="HTML")
        await tg.disconnect()
        GUARD_TASKS.pop(uid, None); return

    me     = await tg.get_me()
    target = _get_user(uid)["target"]
    deleted = 0

    await bot.send_message(uid, "🛡 حارس الشات: يحذف الرسائل الموجودة...")

    # احذف الموجودين أولاً
    try:
        async for msg in tg.iter_messages(target, limit=300):
            if uid not in GUARD_TASKS: break
            if msg.sender_id and msg.sender_id != me.id and not msg.service:
                try:
                    await tg.delete_messages(target, [msg.id], revoke=True)
                    deleted += 1
                    await asyncio.sleep(0.25)
                except Exception:
                    pass
    except Exception as e:
        log.error(f"Guard pre-loop [{uid}]: {e}")

    await bot.send_message(uid, f"🛡 حُذف {deleted} موجود. يراقب الجدد الآن...")

    # راقب الجدد
    @tg.on(events.NewMessage(chats=target))
    async def _on_new(ev):
        nonlocal deleted
        if uid not in GUARD_TASKS:
            tg.remove_event_handler(_on_new); return
        me2 = await tg.get_me()
        if ev.sender_id != me2.id and not ev.message.service:
            try:
                await asyncio.sleep(0.4)
                await tg.delete_messages(target, [ev.message.id], revoke=True)
                deleted += 1
            except Exception as e:
                log.warning(f"Guard new [{uid}]: {e}")

    try:
        while uid in GUARD_TASKS:
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass
    finally:
        tg.remove_event_handler(_on_new)
        await tg.disconnect()
        GUARD_TASKS.pop(uid, None)
        await bot.send_message(uid,
            f"🛡 حارس الشات توقف. حُذف {deleted} رسالة إجمالاً.",
            reply_markup=kb_main(uid))
        log.info(f"Guard loop ended for {uid} — deleted {deleted}")


# ══════════════════════════════════════════════════════════════════════════════
#  حذف رسائلي
# ══════════════════════════════════════════════════════════════════════════════

async def _delete_mine_task(uid: str, progress_msg=None):
    session_path = f"{SESSIONS_DIR}/user_{uid}"
    tg = TelegramClient(session_path, config.API_ID, config.API_HASH)
    await tg.connect()
    if not await tg.is_user_authorized():
        await bot.send_message(uid, "⚠️ جلسة منتهية! أعد الربط.")
        await tg.disconnect(); return

    me     = await tg.get_me()
    target = _get_user(uid)["target"]
    ids    = []

    try:
        async for msg in tg.iter_messages(target, limit=1000, from_user=me.id):
            ids.append(msg.id)

        for i in range(0, len(ids), 100):
            try:
                await tg.delete_messages(target, ids[i:i+100], revoke=False)
            except Exception as e:
                log.warning(f"Batch del [{uid}]: {e}")
            await asyncio.sleep(0.4)

        result = f"✅ تم حذف <code>{len(ids)}</code> رسالة من رسائلك."
        await bot.send_message(uid, result, parse_mode="HTML", reply_markup=kb_main(uid))
        if progress_msg:
            try: await progress_msg.edit_text(result, parse_mode="HTML")
            except: pass
    except Exception as e:
        await bot.send_message(uid, f"❌ خطأ: {e}")
    finally:
        await tg.disconnect()


# ══════════════════════════════════════════════════════════════════════════════
#  سيرفر الويب — صفحة الربط
# ══════════════════════════════════════════════════════════════════════════════

HTML_LOGIN = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>⚡ هادر — ربط الحساب</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  :root {
    --bg:      #0d0d0d;
    --card:    #161616;
    --accent:  #00c6ff;
    --accent2: #7b2ff7;
    --green:   #00e676;
    --red:     #ff1744;
    --text:    #e0e0e0;
    --muted:   #555;
    --border:  #2a2a2a;
    --input:   #1a1a1a;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 16px;
  }
  .logo {
    font-size: 2.2rem;
    font-weight: 900;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin: 20px 0 4px;
  }
  .sub { color: var(--muted); font-size: .85rem; margin-bottom: 24px; }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 24px 20px;
    width: 100%;
    max-width: 400px;
  }
  .step-title {
    font-size: 1rem;
    font-weight: 700;
    color: var(--accent);
    margin-bottom: 16px;
    text-align: center;
  }
  .field-group { margin-bottom: 14px; }
  label { font-size: .8rem; color: var(--muted); display: block; margin-bottom: 6px; }
  input, select {
    width: 100%;
    padding: 12px 14px;
    background: var(--input);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text);
    font-size: 1rem;
    outline: none;
    transition: border-color .2s;
  }
  input:focus, select:focus { border-color: var(--accent); }
  select option { background: #1a1a1a; }
  .phone-row { display: flex; gap: 8px; }
  .phone-row select { width: 46%; flex-shrink: 0; font-size: .9rem; }
  .phone-row input { flex: 1; }
  .btn {
    width: 100%;
    padding: 14px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    color: white;
    border: none;
    border-radius: 12px;
    font-size: 1.05rem;
    font-weight: 700;
    cursor: pointer;
    margin-top: 6px;
    transition: opacity .2s, transform .1s;
  }
  .btn:active { transform: scale(.98); opacity: .9; }
  .btn:disabled { opacity: .5; cursor: not-allowed; }
  .error {
    color: var(--red);
    font-size: .85rem;
    margin-top: 10px;
    text-align: center;
    min-height: 1.2em;
  }
  .step { display: none; }
  .step.active { display: block; }
  .progress {
    display: flex;
    gap: 6px;
    justify-content: center;
    margin-bottom: 20px;
  }
  .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--border);
    transition: background .3s;
  }
  .dot.active { background: var(--accent); }
  .dot.done   { background: var(--green); }
  .success-icon { font-size: 3rem; text-align: center; margin-bottom: 12px; }
  .success-text { text-align: center; color: var(--green); font-weight: 700; font-size: 1.1rem; }
</style>
</head>
<body>

<div class="logo">⚡ هادر</div>
<div class="sub">بوابة الربط الآمنة</div>

<div class="card">
  <div class="progress">
    <div class="dot active" id="d1"></div>
    <div class="dot" id="d2"></div>
    <div class="dot" id="d3"></div>
  </div>

  <!-- خطوة 1: رقم الجوال -->
  <div class="step active" id="step1">
    <div class="step-title">📱 رقم الجوال</div>
    <div class="field-group">
      <label>اختر الدولة</label>
      <div class="phone-row">
        <select id="cc">
          <optgroup label="── الدول العربية ──">
            <option value="+966" selected>🇸🇦 +966 السعودية</option>
            <option value="+971">🇦🇪 +971 الإمارات</option>
            <option value="+965">🇰🇼 +965 الكويت</option>
            <option value="+974">🇶🇦 +974 قطر</option>
            <option value="+973">🇧🇭 +973 البحرين</option>
            <option value="+968">🇴🇲 +968 عمان</option>
            <option value="+967">🇾🇪 +967 اليمن</option>
            <option value="+962">🇯🇴 +962 الأردن</option>
            <option value="+961">🇱🇧 +961 لبنان</option>
            <option value="+963">🇸🇾 +963 سوريا</option>
            <option value="+964">🇮🇶 +964 العراق</option>
            <option value="+20">🇪🇬  +20 مصر</option>
            <option value="+218">🇱🇾 +218 ليبيا</option>
            <option value="+213">🇩🇿 +213 الجزائر</option>
            <option value="+216">🇹🇳 +216 تونس</option>
            <option value="+212">🇲🇦 +212 المغرب</option>
            <option value="+249">🇸🇩 +249 السودان</option>
            <option value="+252">🇸🇴 +252 الصومال</option>
            <option value="+222">🇲🇷 +222 موريتانيا</option>
            <option value="+253">🇩🇯 +253 جيبوتي</option>
            <option value="+269">🇰🇲 +269 جزر القمر</option>
            <option value="+970">🇵🇸 +970 فلسطين</option>
          </optgroup>
          <optgroup label="── دول أخرى ──">
            <option value="+1">🇺🇸 +1 أمريكا / كندا</option>
            <option value="+44">🇬🇧 +44 بريطانيا</option>
            <option value="+49">🇩🇪 +49 ألمانيا</option>
            <option value="+33">🇫🇷 +33 فرنسا</option>
            <option value="+39">🇮🇹 +39 إيطاليا</option>
            <option value="+34">🇪🇸 +34 إسبانيا</option>
            <option value="+31">🇳🇱 +31 هولندا</option>
            <option value="+7">🇷🇺 +7 روسيا</option>
            <option value="+86">🇨🇳 +86 الصين</option>
            <option value="+91">🇮🇳 +91 الهند</option>
            <option value="+81">🇯🇵 +81 اليابان</option>
            <option value="+82">🇰🇷 +82 كوريا الجنوبية</option>
            <option value="+62">🇮🇩 +62 إندونيسيا</option>
            <option value="+92">🇵🇰 +92 باكستان</option>
            <option value="+90">🇹🇷 +90 تركيا</option>
            <option value="+98">🇮🇷 +98 إيران</option>
            <option value="+55">🇧🇷 +55 البرازيل</option>
            <option value="+52">🇲🇽 +52 المكسيك</option>
            <option value="+61">🇦🇺 +61 أستراليا</option>
            <option value="+27">🇿🇦 +27 جنوب أفريقيا</option>
            <option value="+234">🇳🇬 +234 نيجيريا</option>
            <option value="+254">🇰🇪 +254 كينيا</option>
            <option value="+880">🇧🇩 +880 بنغلاديش</option>
            <option value="+63">🇵🇭 +63 الفلبين</option>
            <option value="+66">🇹🇭 +66 تايلاند</option>
            <option value="+84">🇻🇳 +84 فيتنام</option>
            <option value="+60">🇲🇾 +60 ماليزيا</option>
            <option value="+65">🇸🇬 +65 سنغافورة</option>
            <option value="+380">🇺🇦 +380 أوكرانيا</option>
            <option value="+48">🇵🇱 +48 بولندا</option>
            <option value="+46">🇸🇪 +46 السويد</option>
            <option value="+47">🇳🇴 +47 النرويج</option>
            <option value="+45">🇩🇰 +45 الدنمارك</option>
            <option value="+358">🇫🇮 +358 فنلندا</option>
            <option value="+41">🇨🇭 +41 سويسرا</option>
            <option value="+43">🇦🇹 +43 النمسا</option>
            <option value="+32">🇧🇪 +32 بلجيكا</option>
            <option value="+351">🇵🇹 +351 البرتغال</option>
            <option value="+30">🇬🇷 +30 اليونان</option>
          </optgroup>
        </select>
        <input type="tel" id="phone" placeholder="5xxxxxxxx" inputmode="numeric">
      </div>
    </div>
    <button class="btn" onclick="sendPhone()" id="btn1">إرسال الرمز 📩</button>
    <div class="error" id="err1"></div>
  </div>

  <!-- خطوة 2: رمز التحقق -->
  <div class="step" id="step2">
    <div class="step-title">🔐 رمز التحقق</div>
    <div class="field-group">
      <label>الرمز الذي وصلك على تيليجرام</label>
      <input type="text" id="code" placeholder="12345" inputmode="numeric"
             maxlength="7" style="letter-spacing:4px;font-size:1.4rem;text-align:center">
    </div>
    <button class="btn" onclick="sendCode()" id="btn2">تأكيد الرمز ✅</button>
    <div class="error" id="err2"></div>
  </div>

  <!-- خطوة 3: كلمة المرور -->
  <div class="step" id="step3">
    <div class="step-title">🔑 كلمة المرور</div>
    <div class="field-group">
      <label>حسابك محمي بالتحقق بخطوتين</label>
      <input type="password" id="pass" placeholder="••••••••">
    </div>
    <button class="btn" onclick="sendPass()" id="btn3">دخول 🚀</button>
    <div class="error" id="err3"></div>
  </div>

  <!-- نجاح -->
  <div class="step" id="step4">
    <div class="success-icon">🟢</div>
    <div class="success-text">تم ربط حسابك بنجاح!</div>
    <p style="text-align:center;color:#888;margin-top:12px;font-size:.9rem">
      ارجع للبوت وابدأ الإرسال من لوحة التحكم
    </p>
  </div>
</div>

<script>
const tg = window.Telegram.WebApp;
tg.expand();
tg.enableClosingConfirmation();
const userId = tg.initDataUnsafe?.user?.id || "test";

function setStep(n) {
  [1,2,3,4].forEach(i => document.getElementById('step'+i)?.classList.remove('active'));
  document.getElementById('step'+n).classList.add('active');
  [1,2,3].forEach(i => {
    const d = document.getElementById('d'+i);
    if(i < n) { d.className='dot done'; }
    else if(i === n) { d.className='dot active'; }
    else { d.className='dot'; }
  });
}

function showErr(id, msg) { document.getElementById('err'+id).textContent = msg; }
function clearErr(id) { document.getElementById('err'+id).textContent = ''; }

async function sendPhone() {
  clearErr(1);
  let cc  = document.getElementById('cc').value;
  let num = document.getElementById('phone').value.trim().replace(/^0+/, '');
  if(!num) return showErr(1, '❌ أدخل رقم الجوال');
  const full = cc + num;
  document.getElementById('btn1').disabled = true;
  document.getElementById('btn1').textContent = 'جاري الإرسال...';
  const res  = await fetch('/api/send-phone', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ user_id: userId, phone: full })
  });
  const data = await res.json();
  document.getElementById('btn1').disabled = false;
  document.getElementById('btn1').textContent = 'إرسال الرمز 📩';
  if(data.success) { setStep(2); }
  else { showErr(1, '❌ ' + data.error); }
}

async function sendCode() {
  clearErr(2);
  const code = document.getElementById('code').value.trim();
  if(!code) return showErr(2, '❌ أدخل الرمز');
  document.getElementById('btn2').disabled = true;
  document.getElementById('btn2').textContent = 'جاري التحقق...';
  const res  = await fetch('/api/send-code', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ user_id: userId, code })
  });
  const data = await res.json();
  document.getElementById('btn2').disabled = false;
  document.getElementById('btn2').textContent = 'تأكيد الرمز ✅';
  if(data.success) {
    if(data.need_password) { setStep(3); }
    else { setStep(4); setTimeout(()=>tg.close(), 2000); }
  } else { showErr(2, '❌ ' + data.error); }
}

async function sendPass() {
  clearErr(3);
  const pass = document.getElementById('pass').value.trim();
  if(!pass) return showErr(3, '❌ أدخل كلمة المرور');
  document.getElementById('btn3').disabled = true;
  document.getElementById('btn3').textContent = 'جاري التحقق...';
  const res  = await fetch('/api/send-password', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ user_id: userId, password: pass })
  });
  const data = await res.json();
  document.getElementById('btn3').disabled = false;
  document.getElementById('btn3').textContent = 'دخول 🚀';
  if(data.success) { setStep(4); setTimeout(()=>tg.close(), 2000); }
  else { showErr(3, '❌ ' + data.error); }
}
</script>
</body>
</html>"""


async def handle_login_page(request):
    return web.Response(text=HTML_LOGIN, content_type='text/html')

async def api_send_phone(request):
    d     = await request.json()
    uid   = str(d.get("user_id"))
    phone = d.get("phone", "").replace(" ", "")
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    path  = f"{SESSIONS_DIR}/user_{uid}"
    tg    = TelegramClient(path, config.API_ID, config.API_HASH)
    await tg.connect()
    try:
        r = await tg.send_code_request(phone)
        PENDING_LOGINS[uid] = {"client": tg, "phone": phone, "hash": r.phone_code_hash}
        return web.json_response({"success": True})
    except Exception as e:
        await tg.disconnect()
        return web.json_response({"success": False, "error": str(e)})

async def api_send_code(request):
    d    = await request.json()
    uid  = str(d.get("user_id"))
    code = d.get("code", "")
    if uid not in PENDING_LOGINS:
        return web.json_response({"success": False, "error": "انتهت الجلسة، حاول مرة أخرى"})
    login = PENDING_LOGINS[uid]
    tg    = login["client"]
    try:
        await tg.sign_in(phone=login["phone"], code=code, phone_code_hash=login["hash"])
        del PENDING_LOGINS[uid]
        me = await tg.get_me()
        try:
            await bot.send_message(
                int(uid),
                f"🟢 <b>تم ربط حسابك بنجاح!</b>\n"
                f"الاسم: {me.first_name}\n\n"
                f"استخدم الأزرار أدناه للتحكم الكامل 👇",
                parse_mode="HTML",
                reply_markup=kb_main(uid)
            )
        except Exception: pass
        return web.json_response({"success": True, "need_password": False})
    except SessionPasswordNeededError:
        return web.json_response({"success": True, "need_password": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})

async def api_send_password(request):
    d    = await request.json()
    uid  = str(d.get("user_id"))
    pw   = d.get("password", "")
    if uid not in PENDING_LOGINS:
        return web.json_response({"success": False, "error": "انتهت الجلسة"})
    tg = PENDING_LOGINS[uid]["client"]
    try:
        await tg.sign_in(password=pw)
        del PENDING_LOGINS[uid]
        try:
            await bot.send_message(
                int(uid),
                "🟢 <b>تم التحقق وربط حسابك بنجاح!</b>",
                parse_mode="HTML",
                reply_markup=kb_main(uid)
            )
        except Exception: pass
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})


# ══════════════════════════════════════════════════════════════════════════════
#  نص المساعدة
# ══════════════════════════════════════════════════════════════════════════════

HELP_TEXT = (
    "⚡ <b>هادر بوت — دليل الاستخدام</b>\n\n"
    "<b>🚀 البداية:</b>\n"
    "1. اضغط <b>ربط حسابك</b> من /start\n"
    "2. اختر دولتك وأدخل رقمك\n"
    "3. أدخل رمز التحقق\n"
    "4. استخدم الأزرار للتحكم الكامل\n\n"
    "<b>📋 الرسائل:</b>\n"
    "• أضف رسائل مع تكرار (مثال: 5 | مرحبا)\n"
    "• أو أرسل ملف JSON لاستيراد رسائل\n\n"
    "<b>⚙️ الأوضاع:</b>\n"
    "• ⚡ عادي — إرسال بفاصل زمني\n"
    "• 🚀 رصاصة — أقصى سرعة بلا توقف\n"
    "• ✍️ بشري — محاكاة الكتابة البشرية\n\n"
    "<b>🛡 حارس الشات:</b>\n"
    "يحذف رسائل الطرف الثاني فوراً\n\n"
    "<b>🗑 احذف رسائلي:</b>\n"
    "يحذف جميع رسائلك من الشات المستهدف\n\n"
    "<b>💾 الحفظ الدائم:</b>\n"
    "جميع إعداداتك ورسائلك محفوظة ولا تضيع عند إعادة التشغيل"
)

# ══════════════════════════════════════════════════════════════════════════════
#  التشغيل
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    app = web.Application()
    app.router.add_get('/login-page',       handle_login_page)
    app.router.add_post('/api/send-phone',   api_send_phone)
    app.router.add_post('/api/send-code',    api_send_code)
    app.router.add_post('/api/send-password',api_send_password)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    log.info(f"🌐 Web server on port {port}")

    log.info("⚡ هادر بوت — بدأ التشغيل...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
