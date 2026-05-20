"""
⚡ هادر بوت — Supabase Edition (مصلّح بالكامل)
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
from telethon.sessions import StringSession
import aiohttp as aiohttp_lib
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp  = Dispatcher()

SUPABASE_URL = os.getenv("SUPABASE_URL", getattr(config,"SUPABASE_URL","")).strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", getattr(config,"SUPABASE_KEY","")).strip()

PENDING_LOGINS = {}
ACTIVE_TASKS   = {}
GUARD_TASKS    = {}
AWAITING       = {}
PAUSED         = set()

# ══════════════════════════════════════════════════════════════════════════════
#  Supabase — طبقة البيانات المصلّحة
# ══════════════════════════════════════════════════════════════════════════════

def _sb_headers():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }

async def _sb_get(uid: str) -> dict | None:
    """جلب صف المستخدم من Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/bot_users?user_id=eq.{uid}"
    try:
        async with aiohttp_lib.ClientSession() as s:
            async with s.get(url, headers=_sb_headers()) as r:
                rows = await r.json()
                if isinstance(rows, list) and rows:
                    return rows[0]
    except Exception as e:
        log.error(f"[SB GET] {e}")
    return None

async def _sb_upsert(uid: str, data: dict) -> bool:
    """
    INSERT أو UPDATE في Supabase باستخدام POST + upsert.
    هذا الأسلوب يعمل سواء كان السجل موجوداً أم لا.
    """
    url = f"{SUPABASE_URL}/rest/v1/bot_users"
    headers = {**_sb_headers(),
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    payload = {"user_id": uid, **data}

    # تأكد إن messages محفوظة كـ JSON string مو list مباشرة
    if "messages" in payload and isinstance(payload["messages"], list):
        payload["messages"] = json.dumps(payload["messages"], ensure_ascii=False)

    try:
        async with aiohttp_lib.ClientSession() as s:
            async with s.post(url, headers=headers, json=payload) as r:
                if r.status in (200, 201, 204):
                    log.info(f"[SB UPSERT] uid={uid} ok (status={r.status})")
                    return True
                body = await r.text()
                log.error(f"[SB UPSERT] uid={uid} status={r.status} body={body[:200]}")
                return False
    except Exception as e:
        log.error(f"[SB UPSERT] {e}")
        return False

# ── قيم افتراضية ─────────────────────────────────────────────────────────────
_DEFAULTS = {
    "phone": "", "session_string": "", "target": "",
    "interval_s": 2.0, "messages": "[]",
    "mode": "normal", "human_ms": 80,
    "random_order": False, "separator": "",
    "suffix_on": False, "suffix_text": "", "suffix_pos": "end",
    "reply_on": False,
    "guard_active": False,
}

async def get_user(uid: str) -> dict:
    """
    جلب بيانات المستخدم.
    إذا ما كان موجوداً → أنشئه بالقيم الافتراضية.
    إذا كانت messages نص JSON → حوّلها لـ list.
    """
    row = await _sb_get(uid)
    if not row:
        await _sb_upsert(uid, _DEFAULTS.copy())
        row = {**_DEFAULTS, "user_id": uid}

    # تحويل messages من string لـ list
    if isinstance(row.get("messages"), str):
        try:    row["messages"] = json.loads(row["messages"] or "[]")
        except: row["messages"] = []
    if not isinstance(row.get("messages"), list):
        row["messages"] = []

    # ملء المفاتيح الناقصة بالقيم الافتراضية
    for k, v in _DEFAULTS.items():
        if k not in row:
            row[k] = (json.loads(v) if k == "messages" else v)

    return row

async def save_user(uid: str, cfg: dict) -> bool:
    """حفظ بيانات المستخدم كاملة في Supabase."""
    return await _sb_upsert(uid, cfg)


# ══════════════════════════════════════════════════════════════════════════════
#  دوال مساعدة
# ══════════════════════════════════════════════════════════════════════════════

def _mode_ar(m): return {"normal":"عادي ⚡","bullet":"رصاصة 🚀","human":"بشري ✍️"}.get(m,"عادي")

def _apply_msg(text: str, cfg: dict) -> str:
    sep = cfg.get("separator","")
    if sep:
        words = text.split()
        if len(words) > 1:
            text = f" {sep} ".join(words)
    if cfg.get("suffix_on") and cfg.get("suffix_text"):
        extra = cfg["suffix_text"].strip()
        pos   = cfg.get("suffix_pos","end")
        if pos == "start":   text = extra + " " + text
        elif pos == "mid":
            w = text.split(); mid = len(w)//2; w.insert(mid,extra); text = " ".join(w)
        else:                text = text + " " + extra
    return text

def _has_session(cfg: dict) -> bool:
    return bool(cfg.get("session_string","").strip())

async def _status_text(uid: str) -> str:
    cfg   = await get_user(uid)
    msgs  = cfg["messages"]
    total = sum(m.get("repeat",1) for m in msgs)
    run   = uid in ACTIVE_TASKS
    pause = uid in PAUSED
    guard = uid in GUARD_TASKS
    sess  = "🟢 مربوط" if _has_session(cfg) else "🔴 غير مربوط"
    suf   = f"{cfg.get('suffix_text','')[:15]} ({cfg.get('suffix_pos','end')})" if cfg.get("suffix_on") else "—"
    state = "⏸ مؤقت" if pause else ("▶ شغّال" if run else "⏹ متوقف")
    return (
        f"<b>⚡ هادر بوت</b>\n"
        f"━━━━━━━━━━━━━\n"
        f"الحساب : {sess}\n"
        f"الحالة : {state}\n"
        f"الشات  : <code>{cfg.get('target') or '—'}</code>\n"
        f"الرسائل: {len(msgs)} نوع | {total} إجمالي\n"
        f"الوضع  : {_mode_ar(cfg.get('mode','normal'))}\n"
        f"الفاصل : {cfg.get('interval_s',2)}s\n"
        f"عشوائي : {'✅' if cfg.get('random_order') else '❌'}\n"
        f"فاصل   : {cfg.get('separator') or '—'}\n"
        f"إضافي  : {suf}\n"
        f"ردّ    : {'✅' if cfg.get('reply_on') else '❌'}\n"
        f"حارس   : {'🛡 شغّال' if guard else '💤 موقوف'}"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  لوحات المفاتيح
# ══════════════════════════════════════════════════════════════════════════════

def kb_main(uid: str) -> InlineKeyboardMarkup:
    run   = uid in ACTIVE_TASKS
    pause = uid in PAUSED
    guard = uid in GUARD_TASKS
    start_lbl = "⏸ مؤقت" if (run and not pause) else ("▶ استئناف" if pause else "▶ ابدأ")
    start_cb  = "do_pause" if (run and not pause) else ("do_resume" if pause else "do_start")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=start_lbl,    callback_data=start_cb),
         InlineKeyboardButton(text="⏹ وقف",     callback_data="do_stop") if run
         else InlineKeyboardButton(text="📋 الرسائل", callback_data="menu_msgs")],
        [InlineKeyboardButton(text="⚙️ الإعدادات", callback_data="menu_settings"),
         InlineKeyboardButton(text="📊 الحالة",     callback_data="do_status")],
        [InlineKeyboardButton(text="🛡 حارس 🔴" if guard else "🛡 حارس الشات",
                              callback_data="do_guard"),
         InlineKeyboardButton(text="🗑 احذف رسائلي", callback_data="do_delmymsgs")],
    ])

def kb_msgs() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ أضف رسالة",   callback_data="do_addmsg"),
         InlineKeyboardButton(text="📋 عرض الرسائل", callback_data="do_listmsgs")],
        [InlineKeyboardButton(text="🗑 امسح الكل",   callback_data="do_clearmsgs"),
         InlineKeyboardButton(text="🔙 رجوع",         callback_data="do_status")],
    ])

def kb_settings(cfg: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 الشات المستهدف", callback_data="s_target"),
         InlineKeyboardButton(text="⏱ الفاصل الزمني",  callback_data="s_interval")],
        [InlineKeyboardButton(text="⚡ عادي"   + (" ✓" if cfg.get("mode")=="normal" else ""), callback_data="s_normal"),
         InlineKeyboardButton(text="🚀 رصاصة" + (" ✓" if cfg.get("mode")=="bullet" else ""), callback_data="s_bullet"),
         InlineKeyboardButton(text="✍️ بشري"  + (" ✓" if cfg.get("mode")=="human"  else ""), callback_data="s_human")],
        [InlineKeyboardButton(text=f"🎲 عشوائي {'✅' if cfg.get('random_order') else '❌'}",
                              callback_data="s_random"),
         InlineKeyboardButton(text="🔗 فاصل الكلمات", callback_data="s_sep")],
        [InlineKeyboardButton(text=f"📌 نص إضافي {'✅' if cfg.get('suffix_on') else '❌'}",
                              callback_data="s_suffix"),
         InlineKeyboardButton(text=f"↩️ ردّ {'✅' if cfg.get('reply_on') else '❌'}",
                              callback_data="s_reply")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data="do_status")],
    ])

def kb_cancel(back="do_status") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ إلغاء", callback_data=back)]
    ])

def kb_suffix_pos() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶ بداية", callback_data="sp_start"),
         InlineKeyboardButton(text="⏺ وسط",  callback_data="sp_mid"),
         InlineKeyboardButton(text="◀ نهاية", callback_data="sp_end")],
        [InlineKeyboardButton(text="🚫 إيقاف", callback_data="sp_off"),
         InlineKeyboardButton(text="🔙 رجوع",  callback_data="menu_settings")],
    ])

async def safe_edit(cb: CallbackQuery, text: str, kb=None):
    try:
        await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        if "not modified" not in str(e).lower():
            log.warning(f"safe_edit: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  أوامر
# ══════════════════════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid    = str(message.from_user.id)
    cfg    = await get_user(uid)
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", getattr(config,"RAILWAY_PUBLIC_DOMAIN","")).strip()
    url    = f"https://{domain}/login-page?uid={uid}" if domain else f"https://your-app.up.railway.app/login-page?uid={uid}"
    if url.startswith("https://https://"):
        url = url[8:]

    if _has_session(cfg):
        await message.answer(await _status_text(uid), parse_mode="HTML", reply_markup=kb_main(uid))
    else:
        await message.answer(
            "<b>⚡ أهلاً في هادر بوت!</b>\n\n"
            "حسابك غير مربوط بعد.\n"
            "اضغط أدناه لربط حسابك الشخصي:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 ربط حسابك",
                                      web_app=types.WebAppInfo(url=url))],
            ])
        )

@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    uid = str(message.from_user.id)
    await message.answer(await _status_text(uid), parse_mode="HTML", reply_markup=kb_main(uid))


# ══════════════════════════════════════════════════════════════════════════════
#  استقبال النصوص
# ══════════════════════════════════════════════════════════════════════════════

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    uid = str(message.from_user.id)
    aw  = AWAITING.pop(uid, None)
    if not aw:
        return
    text = message.text.strip()
    cfg  = await get_user(uid)

    if aw == "add_msg":
        m = re.match(r"^(\d+)\s*\|\s*(.+)$", text, re.DOTALL)
        repeat, msg_text = (int(m.group(1)), m.group(2).strip()) if m else (1, text)
        cfg["messages"].append({"text": msg_text, "repeat": repeat})
        if await save_user(uid, cfg):
            await message.answer(
                f"✅ رسالة #{len(cfg['messages'])} أُضيفت (×{repeat})\n<code>{msg_text[:80]}</code>",
                parse_mode="HTML", reply_markup=kb_msgs())
        else:
            await message.answer("❌ خطأ في الحفظ. حاول مرة أخرى.", reply_markup=kb_msgs())

    elif aw == "set_target":
        cfg["target"] = text
        if await save_user(uid, cfg):
            await message.answer(f"✅ الشات: <code>{text}</code>",
                                 parse_mode="HTML", reply_markup=kb_settings(cfg))
        else:
            await message.answer("❌ خطأ في الحفظ.")

    elif aw == "set_interval":
        try:
            raw = text.lower()
            if raw.endswith("ms"):   s = float(raw[:-2])/1000
            elif raw.endswith("m"):  s = float(raw[:-1])*60
            elif raw.endswith("s"):  s = float(raw[:-1])
            else:                    s = float(raw)
            cfg["interval_s"] = max(0.0, s)
            if await save_user(uid, cfg):
                await message.answer(f"✅ الفاصل = <code>{cfg['interval_s']}s</code>",
                                     parse_mode="HTML", reply_markup=kb_settings(cfg))
            else:
                await message.answer("❌ خطأ في الحفظ.")
        except ValueError:
            await message.answer("❌ مثال: <code>2s</code> أو <code>500ms</code>", parse_mode="HTML")
            AWAITING[uid] = "set_interval"

    elif aw == "set_sep":
        val = None if text.lower() == "off" else text
        cfg["separator"] = val or ""
        if await save_user(uid, cfg):
            await message.answer(f"✅ فاصل = <code>{val or 'off'}</code>",
                                 parse_mode="HTML", reply_markup=kb_settings(cfg))
        else:
            await message.answer("❌ خطأ في الحفظ.")

    elif aw == "set_suffix_text":
        cfg["suffix_text"] = text
        cfg["suffix_on"]   = True
        if await save_user(uid, cfg):
            await message.answer(f"✅ النص = <code>{text}</code>\nاختر الموضع:",
                                 parse_mode="HTML", reply_markup=kb_suffix_pos())
        else:
            await message.answer("❌ خطأ في الحفظ.")

    elif aw == "del_msg_num":
        try:
            idx = int(text) - 1
            if 0 <= idx < len(cfg["messages"]):
                removed = cfg["messages"].pop(idx)
                if await save_user(uid, cfg):
                    await message.answer(
                        f"✅ حُذفت: <code>{removed['text'][:50]}</code>",
                        parse_mode="HTML", reply_markup=kb_msgs())
                else:
                    await message.answer("❌ خطأ في الحفظ.")
            else:
                await message.answer("❌ رقم غير صحيح.", reply_markup=kb_msgs())
        except ValueError:
            await message.answer("❌ أدخل رقماً.", reply_markup=kb_msgs())

    elif aw == "set_human_ms":
        try:
            ms = max(0, int(text))
            cfg["human_ms"] = ms
            if await save_user(uid, cfg):
                await message.answer(f"✅ سرعة البشري = <code>{ms}ms</code>",
                                     parse_mode="HTML", reply_markup=kb_settings(cfg))
            else:
                await message.answer("❌ خطأ في الحفظ.")
        except ValueError:
            await message.answer("❌ أدخل رقماً (0=أقصى سرعة)")
            AWAITING[uid] = "set_human_ms"


# ── ملف JSON ─────────────────────────────────────────────────────────────────
@dp.message(F.document)
async def handle_file(message: Message):
    uid = str(message.from_user.id)
    if not message.document.file_name.endswith(".json"):
        return
    fb = await bot.download(message.document)
    try:
        raw = json.loads(fb.read().decode("utf-8"))
        normalized = []
        for m in raw:
            if isinstance(m, dict):
                t = m.get("text") or m.get("message","")
                if t: normalized.append({"text":t,"repeat":m.get("repeat",1)})
            elif isinstance(m, str):
                normalized.append({"text":m,"repeat":1})
        if not normalized: raise ValueError("empty")
        AWAITING[uid] = {"type":"file","msgs":normalized}
        await message.answer(
            f"📂 <b>{len(normalized)} رسالة</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ أضف للموجودين", callback_data="file_append"),
                 InlineKeyboardButton(text="🔄 استبدل الكل",   callback_data="file_replace")],
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
    await cb.answer()

    cfg = await get_user(uid)   # أعد الجلب في كل callback

    # ── الحالة ──────────────────────────────────────────────────────────────
    if d == "do_status":
        await safe_edit(cb, await _status_text(uid), kb=kb_main(uid))

    # ── الإرسال ─────────────────────────────────────────────────────────────
    elif d == "do_start":
        if not _has_session(cfg):
            await cb.answer("❌ ربط حسابك أولاً", show_alert=True); return
        if not cfg.get("target"):
            await cb.answer("❌ حدد الشات من الإعدادات", show_alert=True); return
        if not cfg.get("messages"):
            await cb.answer("❌ أضف رسائل أولاً", show_alert=True); return
        if uid in ACTIVE_TASKS:
            await cb.answer("⚠️ الإرسال شغّال", show_alert=True); return
        task = asyncio.create_task(_send_loop(uid))
        ACTIVE_TASKS[uid] = task
        await safe_edit(cb, "▶️ <b>بدأ الإرسال!</b>", kb=kb_main(uid))

    elif d == "do_pause":
        PAUSED.add(uid)
        await safe_edit(cb, await _status_text(uid), kb=kb_main(uid))

    elif d == "do_resume":
        PAUSED.discard(uid)
        await safe_edit(cb, await _status_text(uid), kb=kb_main(uid))

    elif d == "do_stop":
        PAUSED.discard(uid)
        if uid in ACTIVE_TASKS:
            ACTIVE_TASKS[uid].cancel()
            del ACTIVE_TASKS[uid]
        await safe_edit(cb, await _status_text(uid), kb=kb_main(uid))

    # ── الرسائل ─────────────────────────────────────────────────────────────
    elif d == "menu_msgs":
        msgs = cfg["messages"]
        text = f"📋 <b>الرسائل ({len(msgs)})</b>"
        if msgs:
            text += "\n"
            for i, m in enumerate(msgs, 1):
                text += f"\n{i}. ×{m.get('repeat',1)} — {m['text'][:40].replace(chr(10),' ')}"
        else:
            text += "\n<i>لا توجد رسائل بعد</i>"
        await safe_edit(cb, text, kb=kb_msgs())

    elif d == "do_addmsg":
        AWAITING[uid] = "add_msg"
        await safe_edit(cb,
            "✏️ <b>أرسل نص الرسالة:</b>\n\n"
            "• عادي: <code>مرحبا</code>\n"
            "• مع تكرار: <code>5 | مرحبا</code>",
            kb=kb_cancel("menu_msgs"))

    elif d == "do_listmsgs":
        msgs = cfg["messages"]
        if not msgs:
            await cb.answer("لا توجد رسائل", show_alert=True); return
        lines = ["📋 <b>الرسائل:</b>\n"]
        for i, m in enumerate(msgs, 1):
            lines.append(f"<code>{i}.</code> ×{m.get('repeat',1)} — {m['text'][:50].replace(chr(10),' ')}")
        await safe_edit(cb, "\n".join(lines),
            kb=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 احذف رسالة", callback_data="do_delmsg")],
                [InlineKeyboardButton(text="🔙 رجوع",       callback_data="menu_msgs")],
            ]))

    elif d == "do_delmsg":
        AWAITING[uid] = "del_msg_num"
        await safe_edit(cb, "🗑 أرسل رقم الرسالة للحذف:", kb=kb_cancel("do_listmsgs"))

    elif d == "do_clearmsgs":
        cfg["messages"] = []
        await save_user(uid, cfg)
        await safe_edit(cb, "✅ تم مسح جميع الرسائل.", kb=kb_msgs())

    # ── الإعدادات ────────────────────────────────────────────────────────────
    elif d == "menu_settings":
        await safe_edit(cb,
            f"⚙️ <b>الإعدادات</b>\n"
            f"الشات: <code>{cfg.get('target') or 'غير محدد'}</code>\n"
            f"الوضع: {_mode_ar(cfg.get('mode','normal'))} | الفاصل: {cfg.get('interval_s',2)}s",
            kb=kb_settings(cfg))

    elif d == "s_target":
        AWAITING[uid] = "set_target"
        await safe_edit(cb,
            "🎯 أرسل <b>@username</b> أو <b>رقم ID</b> للشات:",
            kb=kb_cancel("menu_settings"))

    elif d == "s_interval":
        AWAITING[uid] = "set_interval"
        await safe_edit(cb,
            "⏱ أرسل الفاصل:\n"
            "<code>2s</code> | <code>500ms</code> | <code>1m</code> | <code>0</code>",
            kb=kb_cancel("menu_settings"))

    elif d in ("s_normal","s_bullet","s_human"):
        mode = d.replace("s_","")
        cfg["mode"] = mode
        await save_user(uid, cfg)
        if mode == "human":
            await safe_edit(cb,
                f"✅ الوضع = {_mode_ar(mode)}\nأرسل سرعة الكتابة (ms) — 0=أقصى سرعة:",
                kb=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="0ms أقصى",    callback_data="hms_0"),
                     InlineKeyboardButton(text="80ms عادي",   callback_data="hms_80"),
                     InlineKeyboardButton(text="✏️ تخصيص",    callback_data="hms_custom")],
                    [InlineKeyboardButton(text="🔙 رجوع", callback_data="menu_settings")],
                ]))
        else:
            await safe_edit(cb, f"✅ الوضع = {_mode_ar(mode)}", kb=kb_settings(cfg))

    elif d.startswith("hms_"):
        if d == "hms_custom":
            AWAITING[uid] = "set_human_ms"
            await safe_edit(cb, "✏️ أرسل القيمة (0–2000):", kb=kb_cancel("menu_settings"))
        else:
            cfg["human_ms"] = int(d.replace("hms_",""))
            await save_user(uid, cfg)
            await safe_edit(cb, f"✅ سرعة البشري = <code>{cfg['human_ms']}ms</code>", kb=kb_settings(cfg))

    elif d == "s_random":
        cfg["random_order"] = not cfg.get("random_order", False)
        await save_user(uid, cfg)
        await safe_edit(cb, f"✅ عشوائي = {'✅' if cfg['random_order'] else '❌'}", kb=kb_settings(cfg))

    elif d == "s_sep":
        AWAITING[uid] = "set_sep"
        await safe_edit(cb,
            "🔗 أرسل الفاصل (مثل <code>*</code>) أو <code>off</code> للإيقاف:",
            kb=kb_cancel("menu_settings"))

    elif d == "s_suffix":
        AWAITING[uid] = "set_suffix_text"
        await safe_edit(cb,
            "📌 أرسل النص الإضافي (مثال: <code>#هادر</code>):",
            kb=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚫 إيقاف النص", callback_data="sp_off")],
                [InlineKeyboardButton(text="❌ إلغاء",       callback_data="menu_settings")],
            ]))

    elif d.startswith("sp_"):
        pos = d.replace("sp_","")
        if pos == "off":
            cfg["suffix_on"] = False; cfg["suffix_text"] = ""
        else:
            cfg["suffix_pos"] = pos; cfg["suffix_on"] = True
        await save_user(uid, cfg)
        await safe_edit(cb,
            f"✅ النص الإضافي: <code>{cfg.get('suffix_text','')}</code> في <b>{pos}</b>" if pos != "off"
            else "✅ النص الإضافي معطّل.",
            kb=kb_settings(cfg))

    elif d == "s_reply":
        cfg["reply_on"] = not cfg.get("reply_on", False)
        await save_user(uid, cfg)
        await safe_edit(cb, f"✅ ردّ = {'✅' if cfg['reply_on'] else '❌'}", kb=kb_settings(cfg))

    # ── حارس الشات ──────────────────────────────────────────────────────────
    elif d == "do_guard":
        if not _has_session(cfg):
            await cb.answer("❌ ربط حسابك أولاً", show_alert=True); return
        if not cfg.get("target"):
            await cb.answer("❌ حدد الشات أولاً", show_alert=True); return
        if uid in GUARD_TASKS:
            GUARD_TASKS[uid].cancel(); del GUARD_TASKS[uid]
            await safe_edit(cb, "🛡 حارس الشات: <b>أُوقف.</b>", kb=kb_main(uid))
        else:
            task = asyncio.create_task(_guard_loop(uid, cfg))
            GUARD_TASKS[uid] = task
            await safe_edit(cb, "🛡 حارس الشات: <b>شغّال 🔴</b>", kb=kb_main(uid))

    # ── حذف رسائلي ──────────────────────────────────────────────────────────
    elif d == "do_delmymsgs":
        if not _has_session(cfg):
            await cb.answer("❌ ربط حسابك أولاً", show_alert=True); return
        if not cfg.get("target"):
            await cb.answer("❌ حدد الشات أولاً", show_alert=True); return
        await safe_edit(cb, "🗑 جاري الحذف...", kb=None)
        asyncio.create_task(_delete_mine(uid, cfg, cb.message))

    # ── ملف JSON ────────────────────────────────────────────────────────────
    elif d in ("file_append","file_replace"):
        pending = AWAITING.pop(uid, None)
        if isinstance(pending, dict) and pending.get("type") == "file":
            new_msgs = pending["msgs"]
            if d == "file_append":
                cfg["messages"].extend(new_msgs); txt = f"✅ أُضيف {len(new_msgs)} رسائل."
            else:
                cfg["messages"] = new_msgs;       txt = f"✅ استُبدل بـ {len(new_msgs)} رسائل."
            await save_user(uid, cfg)
            await safe_edit(cb, txt, kb=kb_msgs())


# ══════════════════════════════════════════════════════════════════════════════
#  حلقة الإرسال
# ══════════════════════════════════════════════════════════════════════════════

async def _send_loop(uid: str):
    cfg  = await get_user(uid)
    sess = cfg.get("session_string","").strip()
    if not sess:
        await bot.send_message(int(uid), "⚠️ جلسة غير موجودة. أعد الربط عبر /start")
        ACTIVE_TASKS.pop(uid, None); return

    tg = TelegramClient(StringSession(sess), config.API_ID, config.API_HASH, timeout=30)
    try:
        await tg.connect()
        if not await tg.is_user_authorized():
            await bot.send_message(int(uid), "⚠️ انتهت صلاحية الجلسة. أعد الربط.")
            await tg.disconnect(); ACTIVE_TASKS.pop(uid,None); return

        me         = await tg.get_me()
        total_sent = 0
        log.info(f"[{uid}] Send loop started")

        while uid in ACTIVE_TASKS:
            while uid in PAUSED:
                await asyncio.sleep(0.3)

            cfg  = await get_user(uid)   # قراءة حية
            msgs = cfg["messages"]
            if not msgs or not cfg.get("target"): break

            i    = random.randint(0,len(msgs)-1) if cfg.get("random_order") else 0
            text = _apply_msg(msgs[i]["text"], cfg)

            try:
                reply_to = None
                if cfg.get("reply_on"):
                    async for msg in tg.iter_messages(cfg["target"], limit=20):
                        if msg.sender_id != me.id:
                            reply_to = msg.id; break

                if cfg.get("mode") == "human" and cfg.get("human_ms",0) > 0:
                    try:
                        await tg.action(cfg["target"], "typing")
                        await asyncio.sleep(min(len(text)*cfg["human_ms"]/1000, 5.0))
                    except Exception: pass

                await tg.send_message(cfg["target"], text, reply_to=reply_to)
                total_sent += 1
                log.info(f"[{uid}] ✅ Sent #{total_sent}: {text[:30]}")

                interval = float(cfg.get("interval_s", 2))
                if cfg.get("mode") == "bullet":
                    await asyncio.sleep(0.02)
                elif interval > 0:
                    await asyncio.sleep(interval)

            except FloodWaitError as e:
                log.warning(f"[{uid}] FloodWait {e.seconds}s")
                await bot.send_message(int(uid),
                    f"⚠️ Telegram: انتظر <b>{e.seconds}s</b>...", parse_mode="HTML")
                await asyncio.sleep(e.seconds + 2)
            except asyncio.CancelledError: raise
            except Exception as e:
                log.error(f"[{uid}] Send err: {e}"); await asyncio.sleep(2)

    except asyncio.CancelledError: pass
    except Exception as e:
        log.error(f"[{uid}] Loop err: {e}")
        await bot.send_message(int(uid), f"❌ خطأ: {e}")
    finally:
        await tg.disconnect(); ACTIVE_TASKS.pop(uid,None); PAUSED.discard(uid)
        await bot.send_message(int(uid),
            f"✅ <b>انتهى الإرسال!</b> أُرسلت <code>{total_sent}</code> رسالة.",
            parse_mode="HTML", reply_markup=kb_main(uid))


# ══════════════════════════════════════════════════════════════════════════════
#  حارس الشات
# ══════════════════════════════════════════════════════════════════════════════

async def _guard_loop(uid: str, cfg: dict):
    sess = cfg.get("session_string","").strip()
    if not sess: return
    tg = TelegramClient(StringSession(sess), config.API_ID, config.API_HASH, timeout=30)
    await tg.connect()
    if not await tg.is_user_authorized():
        await tg.disconnect(); GUARD_TASKS.pop(uid,None); return

    me      = await tg.get_me()
    target  = cfg["target"]
    deleted = 0

    await bot.send_message(int(uid), "🛡 يحذف رسائل الطرف الثاني الموجودة...")
    try:
        async for msg in tg.iter_messages(target, limit=300):
            if uid not in GUARD_TASKS: break
            if msg.sender_id and msg.sender_id != me.id and not msg.service:
                try:
                    await tg.delete_messages(target, [msg.id], revoke=True)
                    deleted += 1; await asyncio.sleep(0.25)
                except Exception: pass
    except Exception as e: log.error(f"[{uid}] Guard pre: {e}")

    await bot.send_message(int(uid), f"🛡 حُذف {deleted} موجود. يراقب الجدد...")

    @tg.on(events.NewMessage(chats=target))
    async def _on_new(ev):
        nonlocal deleted
        if uid not in GUARD_TASKS: tg.remove_event_handler(_on_new); return
        if ev.sender_id != me.id and not ev.message.service:
            try:
                await asyncio.sleep(0.3)
                await tg.delete_messages(target, [ev.message.id], revoke=True)
                deleted += 1
            except Exception as e: log.warning(f"[{uid}] Guard new: {e}")

    try:
        while uid in GUARD_TASKS: await asyncio.sleep(1.0)
    except asyncio.CancelledError: pass
    finally:
        tg.remove_event_handler(_on_new); await tg.disconnect()
        GUARD_TASKS.pop(uid,None)
        await bot.send_message(int(uid),
            f"🛡 الحارس توقف. حُذف {deleted} رسالة.", reply_markup=kb_main(uid))


# ══════════════════════════════════════════════════════════════════════════════
#  حذف رسائلي
# ══════════════════════════════════════════════════════════════════════════════

async def _delete_mine(uid: str, cfg: dict, progress_msg=None):
    sess = cfg.get("session_string","").strip()
    if not sess: return
    tg = TelegramClient(StringSession(sess), config.API_ID, config.API_HASH, timeout=30)
    await tg.connect()
    me     = await tg.get_me()
    target = cfg["target"]
    ids    = []
    try:
        async for msg in tg.iter_messages(target, limit=1000, from_user=me.id):
            ids.append(msg.id)
        for i in range(0, len(ids), 100):
            try: await tg.delete_messages(target, ids[i:i+100], revoke=False)
            except Exception as e: log.warning(f"[{uid}] Del: {e}")
            await asyncio.sleep(0.4)
        result = f"✅ تم حذف <code>{len(ids)}</code> رسالة."
        await bot.send_message(int(uid), result, parse_mode="HTML", reply_markup=kb_main(uid))
        if progress_msg:
            try: await progress_msg.edit_text(result, parse_mode="HTML")
            except: pass
    except Exception as e:
        await bot.send_message(int(uid), f"❌ {e}")
    finally:
        await tg.disconnect()


# ══════════════════════════════════════════════════════════════════════════════
#  WebApp — صفحة الربط
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
:root{--bg:#0d0d0d;--card:#161616;--acc:#00c6ff;--acc2:#7b2ff7;--green:#00e676;--red:#ff1744;--txt:#e0e0e0;--muted:#666;--bdr:#2a2a2a;--inp:#1a1a1a}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--txt);min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:20px 16px}
.logo{font-size:2rem;font-weight:900;background:linear-gradient(135deg,var(--acc),var(--acc2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin:16px 0 4px}
.sub{color:var(--muted);font-size:.85rem;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--bdr);border-radius:16px;padding:22px 18px;width:100%;max-width:380px}
.dots{display:flex;gap:6px;justify-content:center;margin-bottom:16px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--bdr);transition:.3s}
.dot.a{background:var(--acc)}.dot.d{background:var(--green)}
.title{font-size:.95rem;font-weight:700;color:var(--acc);margin-bottom:12px;text-align:center}
.field{margin-bottom:12px}
label{font-size:.78rem;color:var(--muted);display:block;margin-bottom:4px}
input,select{width:100%;padding:12px;background:var(--inp);border:1px solid var(--bdr);border-radius:10px;color:var(--txt);font-size:.95rem;outline:none;transition:.2s}
input:focus,select:focus{border-color:var(--acc)}
.row{display:flex;gap:8px}
.row select{width:44%;font-size:.85rem}
.row input{flex:1}
optgroup{background:#111;color:var(--acc);font-size:.8rem}
option{background:var(--inp);color:var(--txt)}
.btn{width:100%;padding:13px;background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff;border:none;border-radius:11px;font-size:.95rem;font-weight:700;cursor:pointer;margin-top:4px;transition:.15s}
.btn:active{opacity:.85;transform:scale(.98)}
.btn:disabled{opacity:.4;cursor:not-allowed}
.err{color:var(--red);font-size:.82rem;margin-top:8px;text-align:center;min-height:1em}
.step{display:none}.step.on{display:block}
.ok{font-size:3rem;text-align:center;margin-bottom:8px}
.ok-t{text-align:center;color:var(--green);font-weight:700;font-size:1rem}
.ok-s{text-align:center;color:var(--muted);margin-top:8px;font-size:.85rem}
</style>
</head>
<body>
<div class="logo">⚡ هادر</div>
<div class="sub">ربط الحساب الشخصي</div>
<div class="card">
  <div class="dots">
    <div class="dot a" id="d1"></div>
    <div class="dot" id="d2"></div>
    <div class="dot" id="d3"></div>
  </div>

  <div class="step on" id="s1">
    <div class="title">📱 رقم الجوال</div>
    <div class="field">
      <label>اختر الدولة وأدخل رقمك</label>
      <div class="row">
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
            <option value="+55">🇧🇷 +55 البرازيل</option>
            <option value="+61">🇦🇺 +61 أستراليا</option>
          </optgroup>
        </select>
        <input type="tel" id="ph" placeholder="5xxxxxxxx" inputmode="numeric">
      </div>
    </div>
    <button class="btn" id="b1" onclick="doPhone()">إرسال الرمز 📩</button>
    <div class="err" id="e1"></div>
  </div>

  <div class="step" id="s2">
    <div class="title">🔐 رمز التحقق</div>
    <div class="field">
      <label>الرمز الذي وصلك على تيليجرام</label>
      <input type="text" id="co" placeholder="1 2 3 4 5" inputmode="numeric"
             maxlength="7" style="letter-spacing:8px;font-size:1.4rem;text-align:center">
    </div>
    <button class="btn" id="b2" onclick="doCode()">تأكيد ✅</button>
    <div class="err" id="e2"></div>
  </div>

  <div class="step" id="s3">
    <div class="title">🔑 كلمة المرور</div>
    <div class="field">
      <label>حسابك محمي بالتحقق بخطوتين</label>
      <input type="password" id="pw" placeholder="كلمة المرور">
    </div>
    <button class="btn" id="b3" onclick="doPass()">دخول 🚀</button>
    <div class="err" id="e3"></div>
  </div>

  <div class="step" id="s4">
    <div class="ok">🟢</div>
    <div class="ok-t">تم ربط حسابك بنجاح!</div>
    <div class="ok-s">الجلسة محفوظة في Supabase.<br>ارجع للبوت وابدأ الإرسال.</div>
  </div>
</div>

<script>
const tg  = window.Telegram.WebApp;
tg.expand();
const uid = tg.initDataUnsafe?.user?.id
         || new URLSearchParams(location.search).get('uid')
         || "test";

function go(n){
  [1,2,3,4].forEach(i=>{
    document.getElementById('s'+i)?.classList.remove('on');
    const d=document.getElementById('d'+i);
    if(d) d.className='dot'+(i<n?' d':i===n?' a':'');
  });
  document.getElementById('s'+n).classList.add('on');
}
function err(id,msg){document.getElementById('e'+id).textContent=msg}
function busy(id,on,lbl){const b=document.getElementById('b'+id);b.disabled=on;if(!on)b.textContent=lbl}

async function req(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return r.json();
}

async function doPhone(){
  err(1,'');
  let num=document.getElementById('ph').value.trim().replace(/^0+/,'');
  if(!num){err(1,'❌ أدخل رقم الجوال');return}
  const phone=document.getElementById('cc').value+num;
  busy(1,true);
  const r=await req('/api/send-phone',{user_id:uid,phone});
  busy(1,false,'إرسال الرمز 📩');
  r.success?go(2):err(1,'❌ '+r.error);
}
async function doCode(){
  err(2,'');
  const code=document.getElementById('co').value.trim();
  if(!code){err(2,'❌ أدخل الرمز');return}
  busy(2,true);
  const r=await req('/api/send-code',{user_id:uid,code});
  busy(2,false,'تأكيد ✅');
  if(r.success){r.need_password?go(3):(go(4),setTimeout(()=>tg.close(),2500))}
  else err(2,'❌ '+r.error);
}
async function doPass(){
  err(3,'');
  const pw=document.getElementById('pw').value.trim();
  if(!pw){err(3,'❌ أدخل كلمة المرور');return}
  busy(3,true);
  const r=await req('/api/send-password',{user_id:uid,password:pw});
  busy(3,false,'دخول 🚀');
  r.success?(go(4),setTimeout(()=>tg.close(),2500)):err(3,'❌ '+r.error);
}
</script>
</body></html>"""


async def handle_login_page(req):
    return web.Response(text=HTML_PAGE, content_type='text/html')

async def api_send_phone(req):
    d = await req.json()
    uid, phone = str(d.get("user_id")), d.get("phone","").replace(" ","")
    tg = TelegramClient(StringSession(), config.API_ID, config.API_HASH, timeout=20)
    await tg.connect()
    try:
        sent = await tg.send_code_request(phone)
        PENDING_LOGINS[uid] = {"client":tg,"phone":phone,"hash":sent.phone_code_hash}
        return web.json_response({"success":True})
    except Exception as e:
        await tg.disconnect()
        return web.json_response({"success":False,"error":str(e)})

async def api_send_code(req):
    d = await req.json()
    uid, code = str(d.get("user_id")), d.get("code","").strip()
    if uid not in PENDING_LOGINS:
        return web.json_response({"success":False,"error":"انتهت الجلسة، حاول مرة أخرى"})
    item = PENDING_LOGINS[uid]; tg = item["client"]
    try:
        await tg.sign_in(phone=item["phone"], code=code, phone_code_hash=item["hash"])
        sess = tg.session.save()
        await tg.disconnect()
        # ← الحفظ في Supabase فوراً
        cfg = await get_user(uid)
        cfg["phone"] = item["phone"]; cfg["session_string"] = sess
        ok  = await save_user(uid, cfg)
        if not ok:
            return web.json_response({"success":False,"error":"فشل الحفظ في Supabase"})
        del PENDING_LOGINS[uid]
        try: await bot.send_message(int(uid),
            "🟢 <b>تم ربط حسابك وحفظه في Supabase بنجاح!</b>",
            parse_mode="HTML", reply_markup=kb_main(uid))
        except: pass
        return web.json_response({"success":True,"need_password":False})
    except SessionPasswordNeededError:
        return web.json_response({"success":True,"need_password":True})
    except Exception as e:
        return web.json_response({"success":False,"error":str(e)})

async def api_send_password(req):
    d = await req.json()
    uid, pw = str(d.get("user_id")), d.get("password","")
    if uid not in PENDING_LOGINS:
        return web.json_response({"success":False,"error":"انتهت الجلسة"})
    item = PENDING_LOGINS[uid]; tg = item["client"]
    try:
        await tg.sign_in(password=pw)
        sess = tg.session.save()
        await tg.disconnect()
        cfg = await get_user(uid)
        cfg["phone"] = item["phone"]; cfg["session_string"] = sess
        ok  = await save_user(uid, cfg)
        if not ok:
            return web.json_response({"success":False,"error":"فشل الحفظ في Supabase"})
        del PENDING_LOGINS[uid]
        try: await bot.send_message(int(uid),
            "🟢 <b>تم الربط بنجاح!</b>", parse_mode="HTML", reply_markup=kb_main(uid))
        except: pass
        return web.json_response({"success":True})
    except Exception as e:
        return web.json_response({"success":False,"error":str(e)})


# ══════════════════════════════════════════════════════════════════════════════
#  التشغيل
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    app = web.Application()
    app.router.add_get('/login-page',         handle_login_page)
    app.router.add_post('/api/send-phone',     api_send_phone)
    app.router.add_post('/api/send-code',      api_send_code)
    app.router.add_post('/api/send-password',  api_send_password)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    await web.TCPSite(runner, '0.0.0.0', port).start()
    log.info(f"🌐 Web server on port {port}")
    log.info("⚡ هادر بوت (Supabase Edition) — بدأ التشغيل...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
