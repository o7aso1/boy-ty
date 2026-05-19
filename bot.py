"""
⚡ هادر بوت — نظيف ومبسّط ومضمون
"""

import asyncio, json, os, random, logging
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
PAUSED         = set()
AWAITING       = {}   # uid -> str (ما ننتظره من المستخدم)


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

def _save_data(d: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

def _default_user() -> dict:
    return {
        "messages":      [],       # [{text, repeat}]
        "target":        None,
        "interval_s":    3.0,
        "mode":          "normal", # normal | bullet | human
        "human_ms":      80,
        "random_order":  False,
        "separator":     None,
        "suffix_on":     False,
        "suffix_text":   "",
        "suffix_pos":    "end",
        "reply_on":      False,
    }

def get_cfg(uid: str) -> dict:
    d = _load_data()
    if uid not in d:
        d[uid] = _default_user()
        _save_data(d)
    # تأكد كل المفاتيح موجودة
    defaults = _default_user()
    changed  = False
    for k, v in defaults.items():
        if k not in d[uid]:
            d[uid][k] = v
            changed = True
    if changed:
        _save_data(d)
    return d[uid]

def set_cfg(uid: str, key: str, val):
    d = _load_data()
    if uid not in d:
        d[uid] = _default_user()
    d[uid][key] = val
    _save_data(d)

def set_cfg_many(uid: str, updates: dict):
    d = _load_data()
    if uid not in d:
        d[uid] = _default_user()
    d[uid].update(updates)
    _save_data(d)


# ══════════════════════════════════════════════════════════════════════════════
#  دوال مساعدة
# ══════════════════════════════════════════════════════════════════════════════

def _mode_ar(m): return {"normal":"عادي ⚡","bullet":"رصاصة 🚀","human":"بشري ✍️"}.get(m,"عادي")

def _apply_msg(text: str, cfg: dict) -> str:
    if cfg.get("separator"):
        words = text.split()
        if len(words) > 1:
            text = f" {cfg['separator']} ".join(words)
    if cfg.get("suffix_on") and cfg.get("suffix_text"):
        extra = cfg["suffix_text"].strip()
        pos   = cfg.get("suffix_pos","end")
        if pos == "start":   text = extra + " " + text
        elif pos == "mid":
            w = text.split(); mid = len(w)//2; w.insert(mid,extra); text=" ".join(w)
        else:                text = text + " " + extra
    return text

def _has_session(uid: str) -> bool:
    return os.path.exists(f"{SESSIONS_DIR}/user_{uid}.session")

def _status_text(uid: str) -> str:
    cfg   = get_cfg(uid)
    msgs  = cfg["messages"]
    total = sum(m["repeat"] for m in msgs)
    run   = uid in ACTIVE_TASKS
    pause = uid in PAUSED
    guard = uid in GUARD_TASKS
    sess  = "🟢 مربوط" if _has_session(uid) else "🔴 غير مربوط"
    suf   = f"{cfg['suffix_text'][:15]} ({cfg['suffix_pos']})" if cfg["suffix_on"] else "—"
    state = "⏸ مؤقت" if pause else ("▶ شغّال" if run else "⏹ متوقف")
    return (
        f"<b>⚡ هادر بوت</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"الحساب : {sess}\n"
        f"الحالة : {state}\n"
        f"الشات  : <code>{cfg['target'] or '—'}</code>\n"
        f"الرسائل: {len(msgs)} نوع | {total} إجمالي\n"
        f"الوضع  : {_mode_ar(cfg['mode'])}\n"
        f"الفاصل : {cfg['interval_s']}s\n"
        f"عشوائي : {'✅' if cfg['random_order'] else '❌'}\n"
        f"فاصل   : {cfg['separator'] or '—'}\n"
        f"إضافي  : {suf}\n"
        f"ردّ    : {'✅' if cfg['reply_on'] else '❌'}\n"
        f"حارس   : {'🛡 شغّال' if guard else '💤 موقوف'}"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  لوحات المفاتيح — مبسّطة
# ══════════════════════════════════════════════════════════════════════════════

def kb_main(uid: str) -> InlineKeyboardMarkup:
    run   = uid in ACTIVE_TASKS
    pause = uid in PAUSED
    guard = uid in GUARD_TASKS

    start_lbl = "⏸ مؤقت" if pause else ("⏹ وقف" if run else "▶ ابدأ")
    start_cb  = "do_pause" if (run and not pause) else ("do_resume" if pause else "do_start")

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=start_lbl, callback_data=start_cb),
            InlineKeyboardButton(text="⏹ وقف نهائي" if run else "📋 الرسائل",
                                 callback_data="do_stop" if run else "menu_msgs"),
        ],
        [
            InlineKeyboardButton(text="⚙️ الإعدادات", callback_data="menu_settings"),
            InlineKeyboardButton(text="📊 الحالة",     callback_data="do_status"),
        ],
        [
            InlineKeyboardButton(
                text="🛡 حارس شغّال 🔴" if guard else "🛡 حارس الشات",
                callback_data="do_guard"),
            InlineKeyboardButton(text="🗑 احذف رسائلي", callback_data="do_delmymsgs"),
        ],
    ])

def kb_msgs() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ أضف رسالة",   callback_data="do_addmsg"),
            InlineKeyboardButton(text="📋 عرض الرسائل", callback_data="do_listmsgs"),
        ],
        [
            InlineKeyboardButton(text="🗑 امسح الكل",  callback_data="do_clearmsgs"),
            InlineKeyboardButton(text="🔙 رجوع",        callback_data="do_status"),
        ],
    ])

def kb_settings(uid: str) -> InlineKeyboardMarkup:
    cfg = get_cfg(uid)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎯 الشات",     callback_data="s_target"),
            InlineKeyboardButton(text="⏱ الفاصل",    callback_data="s_interval"),
        ],
        [
            InlineKeyboardButton(text="⚡ عادي"  + (" ✓" if cfg["mode"]=="normal" else ""),  callback_data="s_mode_normal"),
            InlineKeyboardButton(text="🚀 رصاصة" + (" ✓" if cfg["mode"]=="bullet" else ""),  callback_data="s_mode_bullet"),
            InlineKeyboardButton(text="✍️ بشري"  + (" ✓" if cfg["mode"]=="human"  else ""),  callback_data="s_mode_human"),
        ],
        [
            InlineKeyboardButton(
                text=f"🎲 عشوائي {'✅' if cfg['random_order'] else '❌'}",
                callback_data="s_toggle_random"),
            InlineKeyboardButton(text="🔗 فاصل الكلمات", callback_data="s_sep"),
        ],
        [
            InlineKeyboardButton(
                text=f"📌 نص إضافي {'✅' if cfg['suffix_on'] else '❌'}",
                callback_data="s_suffix"),
            InlineKeyboardButton(
                text=f"↩️ ردّ {'✅' if cfg['reply_on'] else '❌'}",
                callback_data="s_toggle_reply"),
        ],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="do_status")],
    ])

def kb_cancel(back_cb="do_status") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ إلغاء", callback_data=back_cb)]
    ])

def kb_suffix_pos() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="▶ بداية", callback_data="sufpos_start"),
            InlineKeyboardButton(text="⏺ وسط",  callback_data="sufpos_mid"),
            InlineKeyboardButton(text="◀ نهاية", callback_data="sufpos_end"),
        ],
        [
            InlineKeyboardButton(text="🚫 إيقاف النص الإضافي", callback_data="suffix_off"),
            InlineKeyboardButton(text="🔙 رجوع", callback_data="menu_settings"),
        ],
    ])


# ══════════════════════════════════════════════════════════════════════════════
#  مساعد آمن لتعديل الرسائل (يتجاهل خطأ "not modified")
# ══════════════════════════════════════════════════════════════════════════════

async def safe_edit(cb: CallbackQuery, text: str, kb=None, parse_mode="HTML"):
    try:
        await cb.message.edit_text(text, reply_markup=kb, parse_mode=parse_mode)
    except Exception as e:
        if "not modified" not in str(e).lower():
            log.warning(f"edit_text error: {e}")

async def send_main(cb: CallbackQuery, uid: str):
    await safe_edit(cb, _status_text(uid), kb=kb_main(uid))


# ══════════════════════════════════════════════════════════════════════════════
#  أوامر
# ══════════════════════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid = str(message.from_user.id)
    get_cfg(uid)  # أنشئ بيانات المستخدم
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN","").strip()
    url    = f"https://{domain}/login-page" if domain else "https://your-app.up.railway.app/login-page"
    if url.startswith("https://https://"):
        url = url[8:]

    if _has_session(uid):
        await message.answer(_status_text(uid), parse_mode="HTML", reply_markup=kb_main(uid))
    else:
        await message.answer(
            "<b>⚡ أهلاً في هادر بوت!</b>\n\n"
            "حسابك غير مربوط بعد.\n"
            "اضغط الزر أدناه لربط حسابك الشخصي:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 ربط حسابك الشخصي",
                                      web_app=types.WebAppInfo(url=url))],
                [InlineKeyboardButton(text="📊 الحالة", callback_data="do_status")],
            ])
        )

@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    uid = str(message.from_user.id)
    await message.answer(_status_text(uid), parse_mode="HTML", reply_markup=kb_main(uid))


# ══════════════════════════════════════════════════════════════════════════════
#  استقبال الإدخال النصي
# ══════════════════════════════════════════════════════════════════════════════

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    uid = str(message.from_user.id)
    aw  = AWAITING.pop(uid, None)
    if not aw:
        return

    text = message.text.strip()

    if aw == "add_msg":
        import re
        m = re.match(r"^(\d+)\s*\|\s*(.+)$", text, re.DOTALL)
        repeat, msg_text = (int(m.group(1)), m.group(2).strip()) if m else (1, text)
        d = _load_data(); d[uid]["messages"].append({"text":msg_text,"repeat":repeat}); _save_data(d)
        await message.answer(
            f"✅ رسالة #{len(d[uid]['messages'])} أُضيفت (×{repeat})\n<code>{msg_text[:80]}</code>",
            parse_mode="HTML", reply_markup=kb_msgs())

    elif aw == "set_target":
        set_cfg(uid, "target", text)
        await message.answer(
            f"✅ الشات المستهدف: <code>{text}</code>",
            parse_mode="HTML", reply_markup=kb_settings(uid))

    elif aw == "set_interval":
        try:
            raw = text.lower()
            if raw.endswith("ms"):   s = float(raw[:-2])/1000
            elif raw.endswith("m"):  s = float(raw[:-1])*60
            elif raw.endswith("s"):  s = float(raw[:-1])
            else:                    s = float(raw)
            set_cfg(uid, "interval_s", max(0.0, s))
            await message.answer(
                f"✅ الفاصل = <code>{max(0.0,s)}s</code>",
                parse_mode="HTML", reply_markup=kb_settings(uid))
        except ValueError:
            await message.answer("❌ مثال: <code>2s</code> أو <code>500ms</code>", parse_mode="HTML")
            AWAITING[uid] = "set_interval"

    elif aw == "set_sep":
        val = None if text.lower()=="off" else text
        set_cfg(uid, "separator", val)
        await message.answer(
            f"✅ فاصل الكلمات = <code>{val or 'off'}</code>",
            parse_mode="HTML", reply_markup=kb_settings(uid))

    elif aw == "set_suffix_text":
        set_cfg_many(uid, {"suffix_text": text, "suffix_on": True})
        await message.answer(
            f"✅ النص الإضافي = <code>{text}</code>\nاختر الموضع:",
            parse_mode="HTML", reply_markup=kb_suffix_pos())

    elif aw == "set_human_ms":
        try:
            ms = max(0, int(text))
            set_cfg(uid, "human_ms", ms)
            await message.answer(
                f"✅ سرعة البشري = <code>{ms}ms</code>",
                parse_mode="HTML", reply_markup=kb_settings(uid))
        except ValueError:
            await message.answer("❌ أدخل رقماً (0 = أقصى سرعة)")
            AWAITING[uid] = "set_human_ms"

    elif aw == "del_msg_num":
        try:
            idx = int(text) - 1
            d = _load_data()
            msgs = d[uid]["messages"]
            if 0 <= idx < len(msgs):
                removed = msgs.pop(idx); _save_data(d)
                await message.answer(
                    f"✅ حُذفت: <code>{removed['text'][:50]}</code>",
                    parse_mode="HTML", reply_markup=kb_msgs())
            else:
                await message.answer("❌ رقم غير صحيح.", reply_markup=kb_msgs())
        except ValueError:
            await message.answer("❌ أدخل رقماً.", reply_markup=kb_msgs())


# ── ملف JSON ─────────────────────────────────────────────────────────────────
@dp.message(F.document)
async def handle_file(message: Message):
    uid = str(message.from_user.id)
    if not message.document.file_name.endswith(".json"):
        return
    file_bytes = await bot.download(message.document)
    try:
        raw = json.loads(file_bytes.read().decode("utf-8"))
        normalized = []
        for m in raw:
            if isinstance(m, dict):
                t = m.get("text") or m.get("message","")
                if t: normalized.append({"text":t,"repeat":m.get("repeat",1)})
            elif isinstance(m, str):
                normalized.append({"text":m,"repeat":1})
        if not normalized: raise ValueError
        AWAITING[uid] = {"type":"file","msgs":normalized}
        await message.answer(
            f"📂 <b>{len(normalized)} رسالة</b> — أضف أم استبدل؟",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ أضف",    callback_data="file_append"),
                 InlineKeyboardButton(text="🔄 استبدل", callback_data="file_replace")],
            ]))
    except Exception:
        await message.answer("❌ صيغة الملف غير صحيحة.")


# ══════════════════════════════════════════════════════════════════════════════
#  Callbacks
# ══════════════════════════════════════════════════════════════════════════════

@dp.callback_query()
async def cb_handler(cb: CallbackQuery):
    uid = str(cb.from_user.id)
    d   = cb.data
    cfg = get_cfg(uid)
    await cb.answer()  # أجب دائماً لإزالة loading

    # ── الحالة والقائمة الرئيسية ──────────────────────────────────────────
    if d == "do_status":
        await send_main(cb, uid)

    # ── الإرسال ───────────────────────────────────────────────────────────
    elif d == "do_start":
        if not _has_session(uid):
            await cb.answer("❌ ربط حسابك أولاً عبر /start", show_alert=True); return
        if not cfg["target"]:
            await cb.answer("❌ حدد الشات من الإعدادات أولاً", show_alert=True); return
        if not cfg["messages"]:
            await cb.answer("❌ أضف رسائل أولاً من قائمة الرسائل", show_alert=True); return
        if uid in ACTIVE_TASKS:
            await cb.answer("⚠️ الإرسال شغّال بالفعل", show_alert=True); return

        task = asyncio.create_task(_send_loop(uid))
        ACTIVE_TASKS[uid] = task
        await safe_edit(cb,
            "▶️ <b>بدأ الإرسال!</b>\nاضغط ⏸ مؤقت أو ⏹ وقف نهائي.",
            kb=kb_main(uid))

    elif d == "do_pause":
        if uid in ACTIVE_TASKS:
            PAUSED.add(uid)
            await safe_edit(cb, _status_text(uid), kb=kb_main(uid))
        else:
            await cb.answer("❌ لا يوجد إرسال نشط")

    elif d == "do_resume":
        PAUSED.discard(uid)
        await safe_edit(cb, _status_text(uid), kb=kb_main(uid))

    elif d == "do_stop":
        PAUSED.discard(uid)
        if uid in ACTIVE_TASKS:
            ACTIVE_TASKS[uid].cancel()
            del ACTIVE_TASKS[uid]
        await safe_edit(cb, _status_text(uid), kb=kb_main(uid))

    # ── قائمة الرسائل ─────────────────────────────────────────────────────
    elif d == "menu_msgs":
        msgs = cfg["messages"]
        text = f"📋 <b>الرسائل ({len(msgs)})</b>\n"
        if msgs:
            for i, m in enumerate(msgs, 1):
                text += f"\n{i}. ×{m['repeat']} — {m['text'][:40].replace(chr(10),' ')}"
        else:
            text += "\n<i>لا توجد رسائل بعد</i>"
        await safe_edit(cb, text, kb=kb_msgs(), parse_mode="HTML")

    elif d == "do_addmsg":
        AWAITING[uid] = "add_msg"
        await safe_edit(cb,
            "✏️ <b>أرسل نص الرسالة:</b>\n\n"
            "• رسالة عادية: <code>مرحبا</code>\n"
            "• مع تكرار: <code>5 | مرحبا</code>",
            kb=kb_cancel("menu_msgs"))

    elif d == "do_listmsgs":
        msgs = cfg["messages"]
        if not msgs:
            await cb.answer("لا توجد رسائل", show_alert=True); return
        lines = ["📋 <b>الرسائل:</b>\n"]
        for i, m in enumerate(msgs, 1):
            lines.append(f"<code>{i}.</code> ×{m['repeat']} — {m['text'][:50].replace(chr(10),' ')}")
        lines.append("\nلحذف رسالة اضغط الزر:")
        await safe_edit(cb, "\n".join(lines),
            kb=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 احذف رسالة", callback_data="do_delmsg")],
                [InlineKeyboardButton(text="🔙 رجوع",       callback_data="menu_msgs")],
            ]))

    elif d == "do_delmsg":
        AWAITING[uid] = "del_msg_num"
        await safe_edit(cb, "🗑 أرسل رقم الرسالة للحذف:", kb=kb_cancel("do_listmsgs"))

    elif d == "do_clearmsgs":
        set_cfg(uid, "messages", [])
        await safe_edit(cb, "✅ تم مسح جميع الرسائل.", kb=kb_msgs())

    # ── الإعدادات ──────────────────────────────────────────────────────────
    elif d == "menu_settings":
        await safe_edit(cb,
            f"⚙️ <b>الإعدادات</b>\n"
            f"الوضع: {_mode_ar(cfg['mode'])} | الفاصل: {cfg['interval_s']}s\n"
            f"الشات: <code>{cfg['target'] or 'غير محدد'}</code>",
            kb=kb_settings(uid))

    elif d == "s_target":
        AWAITING[uid] = "set_target"
        await safe_edit(cb,
            "🎯 أرسل <b>@username</b> أو <b>رقم ID</b> للشات المستهدف:",
            kb=kb_cancel("menu_settings"))

    elif d == "s_interval":
        AWAITING[uid] = "set_interval"
        await safe_edit(cb,
            "⏱ أرسل الفاصل الزمني:\n"
            "<code>2s</code> = ثانيتان\n"
            "<code>500ms</code> = نصف ثانية\n"
            "<code>1m</code> = دقيقة\n"
            "<code>0</code> = بلا فاصل (رصاصة)",
            kb=kb_cancel("menu_settings"))

    elif d in ("s_mode_normal","s_mode_bullet","s_mode_human"):
        mode = d.replace("s_mode_","")
        set_cfg(uid, "mode", mode)
        if mode == "human":
            await safe_edit(cb,
                f"✅ الوضع = {_mode_ar(mode)}\n"
                "أرسل سرعة الكتابة بالـ ms\n"
                "<b>0</b> = أقصى سرعة | <b>80</b> = افتراضي | <b>300</b> = بطيء",
                kb=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="0ms أقصى سرعة", callback_data="hms_0"),
                     InlineKeyboardButton(text="80ms افتراضي",  callback_data="hms_80"),
                     InlineKeyboardButton(text="تخصيص ✏️",      callback_data="hms_custom")],
                    [InlineKeyboardButton(text="🔙 رجوع", callback_data="menu_settings")],
                ]))
        else:
            await safe_edit(cb, f"✅ الوضع = {_mode_ar(mode)}", kb=kb_settings(uid))

    elif d.startswith("hms_"):
        if d == "hms_custom":
            AWAITING[uid] = "set_human_ms"
            await safe_edit(cb, "✏️ أرسل القيمة بالـ ms (0–2000):",
                            kb=kb_cancel("menu_settings"))
        else:
            ms = int(d.replace("hms_",""))
            set_cfg(uid, "human_ms", ms)
            await safe_edit(cb, f"✅ سرعة البشري = <code>{ms}ms</code>",
                            kb=kb_settings(uid))

    elif d == "s_toggle_random":
        new = not cfg["random_order"]
        set_cfg(uid, "random_order", new)
        await safe_edit(cb,
            f"✅ الترتيب العشوائي = {'مفعّل ✅' if new else 'معطّل ❌'}",
            kb=kb_settings(uid))

    elif d == "s_sep":
        AWAITING[uid] = "set_sep"
        await safe_edit(cb,
            "🔗 أرسل الفاصل بين الكلمات:\n"
            "مثال: <code>*</code> أو <code>#</code>\n"
            "لإيقافه أرسل: <code>off</code>",
            kb=kb_cancel("menu_settings"))

    elif d == "s_suffix":
        AWAITING[uid] = "set_suffix_text"
        await safe_edit(cb,
            "📌 أرسل النص الإضافي:\n"
            "مثال: <code>#هادر</code>",
            kb=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚫 إيقاف النص الإضافي", callback_data="suffix_off")],
                [InlineKeyboardButton(text="❌ إلغاء", callback_data="menu_settings")],
            ]))

    elif d == "suffix_off":
        set_cfg_many(uid, {"suffix_on": False, "suffix_text": ""})
        await safe_edit(cb, "✅ النص الإضافي معطّل.", kb=kb_settings(uid))

    elif d.startswith("sufpos_"):
        pos = d.replace("sufpos_","")
        set_cfg(uid, "suffix_pos", pos)
        cfg2 = get_cfg(uid)
        await safe_edit(cb,
            f"✅ النص الإضافي: <code>{cfg2['suffix_text']}</code> في <b>{pos}</b>",
            kb=kb_settings(uid))

    elif d == "s_toggle_reply":
        new = not cfg["reply_on"]
        set_cfg(uid, "reply_on", new)
        await safe_edit(cb,
            f"✅ الرد على آخر رسالة = {'مفعّل ✅' if new else 'معطّل ❌'}",
            kb=kb_settings(uid))

    # ── حارس الشات ────────────────────────────────────────────────────────
    elif d == "do_guard":
        if not _has_session(uid):
            await cb.answer("❌ ربط حسابك أولاً", show_alert=True); return
        if not cfg["target"]:
            await cb.answer("❌ حدد الشات أولاً", show_alert=True); return
        if uid in GUARD_TASKS:
            GUARD_TASKS[uid].cancel()
            del GUARD_TASKS[uid]
            await safe_edit(cb, "🛡 حارس الشات: <b>أُوقف.</b>", kb=kb_main(uid))
        else:
            task = asyncio.create_task(_guard_loop(uid))
            GUARD_TASKS[uid] = task
            await safe_edit(cb,
                "🛡 حارس الشات: <b>شغّال 🔴</b>\nيحذف رسائل الطرف الثاني فوراً.",
                kb=kb_main(uid))

    # ── حذف رسائلي ────────────────────────────────────────────────────────
    elif d == "do_delmymsgs":
        if not _has_session(uid):
            await cb.answer("❌ ربط حسابك أولاً", show_alert=True); return
        if not cfg["target"]:
            await cb.answer("❌ حدد الشات أولاً", show_alert=True); return
        await safe_edit(cb, "🗑 جاري حذف رسائلك...", kb=None)
        asyncio.create_task(_delete_mine(uid, cb.message))

    # ── ملف JSON ──────────────────────────────────────────────────────────
    elif d in ("file_append","file_replace"):
        pending = AWAITING.pop(uid, None)
        if isinstance(pending, dict) and pending.get("type") == "file":
            new_msgs = pending["msgs"]
            data = _load_data()
            if d == "file_append":
                data[uid]["messages"].extend(new_msgs); txt = f"✅ أُضيف {len(new_msgs)} رسائل."
            else:
                data[uid]["messages"] = new_msgs;       txt = f"✅ استُبدل بـ {len(new_msgs)} رسائل."
            _save_data(data)
            await safe_edit(cb, txt, kb=kb_msgs())


# ══════════════════════════════════════════════════════════════════════════════
#  حلقة الإرسال — مضمونة وبدون أخطاء
# ══════════════════════════════════════════════════════════════════════════════

async def _send_loop(uid: str):
    path = f"{SESSIONS_DIR}/user_{uid}"
    tg   = TelegramClient(path, config.API_ID, config.API_HASH)
    await tg.connect()

    if not await tg.is_user_authorized():
        await bot.send_message(int(uid),
            "⚠️ <b>انتهت صلاحية جلسة حسابك!</b>\nأعد الربط عبر /start",
            parse_mode="HTML")
        await tg.disconnect()
        ACTIVE_TASKS.pop(uid, None); return

    log.info(f"[{uid}] Send loop started")
    total_sent = 0

    try:
        me = await tg.get_me()

        while True:
            # انتظر رفع الإيقاف المؤقت
            while uid in PAUSED:
                await asyncio.sleep(0.3)

            # أعد قراءة البيانات في كل دورة (للتعديلات الحية)
            cfg  = get_cfg(uid)
            msgs = cfg["messages"]
            if not msgs:
                break

            # اختر الرسالة
            i = random.randint(0, len(msgs)-1) if cfg["random_order"] else 0
            # تقليل التكرار مؤقتاً — نتتبع بـ counter منفصل
            text = _apply_msg(msgs[i]["text"], cfg)

            try:
                reply_to = None
                if cfg["reply_on"]:
                    async for msg in tg.iter_messages(cfg["target"], limit=20):
                        if msg.sender_id != me.id:
                            reply_to = msg.id; break

                if cfg["mode"] == "human" and cfg["human_ms"] > 0:
                    try:
                        await tg(SetTypingRequest(
                            peer=cfg["target"], action=SendMessageTypingAction()))
                        await asyncio.sleep(min(len(text)*cfg["human_ms"]/1000, 5.0))
                    except Exception:
                        pass

                await tg.send_message(cfg["target"], text, reply_to=reply_to)
                total_sent += 1
                log.info(f"[{uid}] Sent #{total_sent}: {text[:30]}")

                if cfg["mode"] == "bullet":
                    await asyncio.sleep(0.02)
                elif cfg["interval_s"] > 0:
                    await asyncio.sleep(cfg["interval_s"])

            except FloodWaitError as e:
                log.warning(f"[{uid}] FloodWait {e.seconds}s")
                await bot.send_message(int(uid),
                    f"⚠️ Telegram طلب انتظار <b>{e.seconds}s</b>...",
                    parse_mode="HTML")
                await asyncio.sleep(e.seconds + 2)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(f"[{uid}] Send error: {e}")
                await asyncio.sleep(2)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.error(f"[{uid}] Loop error: {e}")
        await bot.send_message(int(uid), f"❌ خطأ في الإرسال: {e}")
    finally:
        await tg.disconnect()
        ACTIVE_TASKS.pop(uid, None)
        PAUSED.discard(uid)
        await bot.send_message(int(uid),
            f"✅ <b>انتهى الإرسال!</b>\nأُرسلت <code>{total_sent}</code> رسالة.",
            parse_mode="HTML", reply_markup=kb_main(uid))
        log.info(f"[{uid}] Send loop ended — sent {total_sent}")


# ══════════════════════════════════════════════════════════════════════════════
#  حارس الشات
# ══════════════════════════════════════════════════════════════════════════════

async def _guard_loop(uid: str):
    path = f"{SESSIONS_DIR}/user_{uid}"
    tg   = TelegramClient(path, config.API_ID, config.API_HASH)
    await tg.connect()
    if not await tg.is_user_authorized():
        await bot.send_message(int(uid), "⚠️ جلسة منتهية! أعد الربط.")
        await tg.disconnect(); GUARD_TASKS.pop(uid, None); return

    me     = await tg.get_me()
    target = get_cfg(uid)["target"]
    deleted = 0

    await bot.send_message(int(uid), "🛡 جاري حذف رسائل الطرف الثاني الموجودة...")

    # احذف الموجودين
    try:
        async for msg in tg.iter_messages(target, limit=300):
            if uid not in GUARD_TASKS: break
            if msg.sender_id and msg.sender_id != me.id and not msg.service:
                try:
                    await tg.delete_messages(target, [msg.id], revoke=True)
                    deleted += 1; await asyncio.sleep(0.25)
                except Exception: pass
    except Exception as e:
        log.error(f"[{uid}] Guard pre: {e}")

    await bot.send_message(int(uid), f"🛡 حُذف {deleted} رسالة موجودة. يراقب الجدد...")

    @tg.on(events.NewMessage(chats=target))
    async def _on_new(ev):
        nonlocal deleted
        if uid not in GUARD_TASKS:
            tg.remove_event_handler(_on_new); return
        if ev.sender_id != me.id and not ev.message.service:
            try:
                await asyncio.sleep(0.3)
                await tg.delete_messages(target, [ev.message.id], revoke=True)
                deleted += 1
            except Exception as e:
                log.warning(f"[{uid}] Guard new: {e}")

    try:
        while uid in GUARD_TASKS:
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass
    finally:
        tg.remove_event_handler(_on_new)
        await tg.disconnect()
        GUARD_TASKS.pop(uid, None)
        await bot.send_message(int(uid),
            f"🛡 حارس الشات توقف. حُذف {deleted} رسالة إجمالاً.",
            reply_markup=kb_main(uid))


# ══════════════════════════════════════════════════════════════════════════════
#  حذف رسائلي
# ══════════════════════════════════════════════════════════════════════════════

async def _delete_mine(uid: str, progress_msg=None):
    path = f"{SESSIONS_DIR}/user_{uid}"
    tg   = TelegramClient(path, config.API_ID, config.API_HASH)
    await tg.connect()
    if not await tg.is_user_authorized():
        await tg.disconnect(); return

    me     = await tg.get_me()
    target = get_cfg(uid)["target"]
    ids    = []

    try:
        async for msg in tg.iter_messages(target, limit=1000, from_user=me.id):
            ids.append(msg.id)
        for i in range(0, len(ids), 100):
            try: await tg.delete_messages(target, ids[i:i+100], revoke=False)
            except Exception as e: log.warning(f"[{uid}] Del mine: {e}")
            await asyncio.sleep(0.4)
        result = f"✅ تم حذف <code>{len(ids)}</code> رسالة."
        await bot.send_message(int(uid), result, parse_mode="HTML", reply_markup=kb_main(uid))
        if progress_msg:
            try: await progress_msg.edit_text(result, parse_mode="HTML")
            except: pass
    except Exception as e:
        await bot.send_message(int(uid), f"❌ خطأ: {e}")
    finally:
        await tg.disconnect()


# ══════════════════════════════════════════════════════════════════════════════
#  صفحة الربط (WebApp)
# ══════════════════════════════════════════════════════════════════════════════

HTML_PAGE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>⚡ هادر — ربط الحساب</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0d0d0d;--card:#161616;--acc:#00c6ff;--acc2:#7b2ff7;--green:#00e676;--red:#ff1744;--txt:#e0e0e0;--muted:#555;--bdr:#2a2a2a;--inp:#1a1a1a}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--txt);min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:16px}
.logo{font-size:2rem;font-weight:900;background:linear-gradient(135deg,var(--acc),var(--acc2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin:20px 0 4px}
.sub{color:var(--muted);font-size:.85rem;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--bdr);border-radius:16px;padding:22px 18px;width:100%;max-width:400px}
.dots{display:flex;gap:6px;justify-content:center;margin-bottom:18px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--bdr);transition:background .3s}
.dot.active{background:var(--acc)}.dot.done{background:var(--green)}
.step-title{font-size:1rem;font-weight:700;color:var(--acc);margin-bottom:14px;text-align:center}
.field{margin-bottom:12px}
label{font-size:.8rem;color:var(--muted);display:block;margin-bottom:5px}
input,select{width:100%;padding:12px;background:var(--inp);border:1px solid var(--bdr);border-radius:10px;color:var(--txt);font-size:1rem;outline:none;transition:border-color .2s}
input:focus,select:focus{border-color:var(--acc)}
.phone-row{display:flex;gap:8px}
.phone-row select{width:44%;font-size:.9rem}
.phone-row input{flex:1}
optgroup{background:#111;color:var(--acc);font-size:.8rem}
option{background:#1a1a1a;color:var(--txt)}
.btn{width:100%;padding:13px;background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff;border:none;border-radius:12px;font-size:1rem;font-weight:700;cursor:pointer;margin-top:4px;transition:opacity .2s,transform .1s}
.btn:active{transform:scale(.98);opacity:.9}
.btn:disabled{opacity:.4;cursor:not-allowed}
.err{color:var(--red);font-size:.85rem;margin-top:8px;text-align:center;min-height:1em}
.step{display:none}.step.active{display:block}
.ok-icon{font-size:3rem;text-align:center;margin-bottom:10px}
.ok-text{text-align:center;color:var(--green);font-weight:700;font-size:1.1rem}
.ok-sub{text-align:center;color:#888;margin-top:10px;font-size:.9rem}
</style>
</head>
<body>
<div class="logo">⚡ هادر</div>
<div class="sub">بوابة الربط الآمنة</div>
<div class="card">
  <div class="dots">
    <div class="dot active" id="d1"></div>
    <div class="dot" id="d2"></div>
    <div class="dot" id="d3"></div>
  </div>

  <div class="step active" id="s1">
    <div class="step-title">📱 رقم الجوال</div>
    <div class="field">
      <label>اختر الدولة وأدخل رقمك</label>
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
            <option value="+20">🇪🇬 +20 مصر</option>
            <option value="+218">🇱🇾 +218 ليبيا</option>
            <option value="+213">🇩🇿 +213 الجزائر</option>
            <option value="+216">🇹🇳 +216 تونس</option>
            <option value="+212">🇲🇦 +212 المغرب</option>
            <option value="+249">🇸🇩 +249 السودان</option>
            <option value="+252">🇸🇴 +252 الصومال</option>
            <option value="+222">🇲🇷 +222 موريتانيا</option>
            <option value="+970">🇵🇸 +970 فلسطين</option>
          </optgroup>
          <optgroup label="── دول أخرى ──">
            <option value="+1">🇺🇸 +1 أمريكا</option>
            <option value="+44">🇬🇧 +44 بريطانيا</option>
            <option value="+49">🇩🇪 +49 ألمانيا</option>
            <option value="+33">🇫🇷 +33 فرنسا</option>
            <option value="+7">🇷🇺 +7 روسيا</option>
            <option value="+86">🇨🇳 +86 الصين</option>
            <option value="+91">🇮🇳 +91 الهند</option>
            <option value="+81">🇯🇵 +81 اليابان</option>
            <option value="+90">🇹🇷 +90 تركيا</option>
            <option value="+98">🇮🇷 +98 إيران</option>
            <option value="+92">🇵🇰 +92 باكستان</option>
            <option value="+62">🇮🇩 +62 إندونيسيا</option>
            <option value="+55">🇧🇷 +55 البرازيل</option>
            <option value="+61">🇦🇺 +61 أستراليا</option>
            <option value="+39">🇮🇹 +39 إيطاليا</option>
            <option value="+34">🇪🇸 +34 إسبانيا</option>
          </optgroup>
        </select>
        <input type="tel" id="ph" placeholder="5xxxxxxxx" inputmode="numeric">
      </div>
    </div>
    <button class="btn" id="b1" onclick="doPhone()">إرسال الرمز 📩</button>
    <div class="err" id="e1"></div>
  </div>

  <div class="step" id="s2">
    <div class="step-title">🔐 رمز التحقق</div>
    <div class="field">
      <label>الرمز الذي وصلك على تيليجرام</label>
      <input type="text" id="co" placeholder="1 2 3 4 5" inputmode="numeric"
             maxlength="7" style="letter-spacing:6px;font-size:1.5rem;text-align:center">
    </div>
    <button class="btn" id="b2" onclick="doCode()">تأكيد ✅</button>
    <div class="err" id="e2"></div>
  </div>

  <div class="step" id="s3">
    <div class="step-title">🔑 كلمة المرور</div>
    <div class="field">
      <label>حسابك محمي بالتحقق بخطوتين</label>
      <input type="password" id="pw" placeholder="كلمة المرور">
    </div>
    <button class="btn" id="b3" onclick="doPass()">دخول 🚀</button>
    <div class="err" id="e3"></div>
  </div>

  <div class="step" id="s4">
    <div class="ok-icon">🟢</div>
    <div class="ok-text">تم ربط حسابك بنجاح!</div>
    <div class="ok-sub">ارجع للبوت وابدأ الإرسال</div>
  </div>
</div>

<script>
const tg = window.Telegram.WebApp;
tg.expand();
const uid = tg.initDataUnsafe?.user?.id || "test";

function go(n){
  [1,2,3,4].forEach(i=>{
    document.getElementById('s'+i)?.classList.remove('active');
    const d=document.getElementById('d'+i);
    if(d){d.className='dot'+(i<n?' done':i===n?' active':'');}
  });
  document.getElementById('s'+n).classList.add('active');
}
function err(id,msg){document.getElementById('e'+id).textContent=msg;}
function busy(id,on,txt){
  const b=document.getElementById('b'+id);
  b.disabled=on; b.textContent=on?'جاري...':txt;
}
async function post(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return r.json();
}
async function doPhone(){
  err(1,'');
  let num=document.getElementById('ph').value.trim().replace(/^0+/,'');
  if(!num){err(1,'❌ أدخل رقم الجوال');return;}
  const phone=document.getElementById('cc').value+num;
  busy(1,true);
  const r=await post('/api/send-phone',{user_id:uid,phone});
  busy(1,false,'إرسال الرمز 📩');
  r.success?go(2):err(1,'❌ '+r.error);
}
async function doCode(){
  err(2,'');
  const code=document.getElementById('co').value.trim();
  if(!code){err(2,'❌ أدخل الرمز');return;}
  busy(2,true);
  const r=await post('/api/send-code',{user_id:uid,code});
  busy(2,false,'تأكيد ✅');
  if(r.success){r.need_password?go(3):(go(4),setTimeout(()=>tg.close(),2000));}
  else err(2,'❌ '+r.error);
}
async function doPass(){
  err(3,'');
  const pw=document.getElementById('pw').value.trim();
  if(!pw){err(3,'❌ أدخل كلمة المرور');return;}
  busy(3,true);
  const r=await post('/api/send-password',{user_id:uid,password:pw});
  busy(3,false,'دخول 🚀');
  r.success?(go(4),setTimeout(()=>tg.close(),2000)):err(3,'❌ '+r.error);
}
</script>
</body></html>"""


async def handle_login_page(req):
    return web.Response(text=HTML_PAGE, content_type='text/html')

async def api_send_phone(req):
    d     = await req.json()
    uid   = str(d.get("user_id"))
    phone = d.get("phone","").replace(" ","")
    path  = f"{SESSIONS_DIR}/user_{uid}"
    tg    = TelegramClient(path, config.API_ID, config.API_HASH)
    await tg.connect()
    try:
        r = await tg.send_code_request(phone)
        PENDING_LOGINS[uid] = {"client":tg,"phone":phone,"hash":r.phone_code_hash}
        return web.json_response({"success":True})
    except Exception as e:
        await tg.disconnect()
        return web.json_response({"success":False,"error":str(e)})

async def api_send_code(req):
    d    = await req.json()
    uid  = str(d.get("user_id"))
    code = d.get("code","")
    if uid not in PENDING_LOGINS:
        return web.json_response({"success":False,"error":"انتهت الجلسة، حاول مرة أخرى"})
    login = PENDING_LOGINS[uid]; tg = login["client"]
    try:
        await tg.sign_in(phone=login["phone"],code=code,phone_code_hash=login["hash"])
        del PENDING_LOGINS[uid]
        me = await tg.get_me()
        try:
            await bot.send_message(int(uid),
                f"🟢 <b>تم ربط حسابك!</b>\nالاسم: {me.first_name}\n\n"
                "استخدم الأزرار للتحكم 👇",
                parse_mode="HTML", reply_markup=kb_main(uid))
        except Exception: pass
        return web.json_response({"success":True,"need_password":False})
    except SessionPasswordNeededError:
        return web.json_response({"success":True,"need_password":True})
    except Exception as e:
        return web.json_response({"success":False,"error":str(e)})

async def api_send_password(req):
    d   = await req.json()
    uid = str(d.get("user_id"))
    pw  = d.get("password","")
    if uid not in PENDING_LOGINS:
        return web.json_response({"success":False,"error":"انتهت الجلسة"})
    tg = PENDING_LOGINS[uid]["client"]
    try:
        await tg.sign_in(password=pw)
        del PENDING_LOGINS[uid]
        try:
            await bot.send_message(int(uid),
                "🟢 <b>تم ربط حسابك بنجاح!</b>",
                parse_mode="HTML", reply_markup=kb_main(uid))
        except Exception: pass
        return web.json_response({"success":True})
    except Exception as e:
        return web.json_response({"success":False,"error":str(e)})


# ══════════════════════════════════════════════════════════════════════════════
#  التشغيل
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    app = web.Application()
    app.router.add_get('/login-page',        handle_login_page)
    app.router.add_post('/api/send-phone',    api_send_phone)
    app.router.add_post('/api/send-code',     api_send_code)
    app.router.add_post('/api/send-password', api_send_password)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    await web.TCPSite(runner, '0.0.0.0', port).start()
    log.info(f"🌐 Web server on port {port}")
    log.info("⚡ هادر بوت — بدأ التشغيل...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
