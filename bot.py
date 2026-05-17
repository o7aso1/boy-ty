"""
⚡ هادر بوت — النسخة الكاملة
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
from config import API_ID, API_HASH, SESSION_NAME, ADMIN_ID, CONTROL_CHAT

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("hadir.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

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
    "human_delay_ms": 80,
    "random_order":   False,
    "separator":      None,
    "suffix_on":      False,
    "suffix_text":    "",
    "suffix_pos":     "end",   # start | mid | end
    "reply_on":       False,
    "reply_username": "",
    "guard_active":   False,
    "extra_sessions": [],      # [{"name":..., "client":...}]
    "status_msg_id":  None,
    "awaiting":       None,
}

state         = deepcopy(DEFAULT_STATE)
MESSAGES_FILE = "messages.json"
SESSIONS_DIR  = "sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
#  دوال مساعدة
# ══════════════════════════════════════════════════════════════════════════════

def _save_messages():
    with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
        json.dump(state["messages"], f, ensure_ascii=False, indent=2)

def _load_messages():
    if os.path.exists(MESSAGES_FILE):
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            state["messages"] = json.load(f)

def _apply_msg(text: str) -> str:
    if state["separator"]:
        words = text.split()
        if len(words) > 1:
            text = f" {state['separator']} ".join(words)
    if state["suffix_on"] and state["suffix_text"]:
        extra = state["suffix_text"].strip()
        pos   = state["suffix_pos"]
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

def _mode_ar():
    return {"normal":"عادي ⚡","bullet":"رصاصة 🚀","human":"بشري ✍️"}.get(state["mode"],"عادي")

def _status_text() -> str:
    total = sum(m["repeat"] for m in state["messages"])
    done  = sum(state["done_counts"]) if state["done_counts"] else 0
    bar_l = 14
    filled = int(bar_l * done / total) if total else 0
    bar   = "█"*filled + "░"*(bar_l-filled)
    rlbl  = ("⏸ موقف مؤقت" if state["paused"]
             else ("▶ شغّال" if state["running"] else "⏹ متوقف"))
    suf   = (f"{state['suffix_text'][:15]} ({state['suffix_pos']})"
             if state["suffix_on"] else "—")
    accs  = 1 + len(state["extra_sessions"])
    return (
        f"```\n"
        f"⚡ هادر بوت — لوحة التحكم\n"
        f"{'─'*30}\n"
        f"الحالة   : {rlbl}\n"
        f"الوضع    : {_mode_ar()}\n"
        f"الشات    : {state['target_chat'] or '—'}\n"
        f"التقدم   : [{bar}] {done}/{total}\n"
        f"الفاصل   : {state['interval_s']}s\n"
        f"عشوائي   : {'✅' if state['random_order'] else '❌'}\n"
        f"فاصل كلمات: {state['separator'] or '—'}\n"
        f"نص إضافي : {suf}\n"
        f"ردّ      : {'✅' if state['reply_on'] else '❌'}"
        f"{' (' + state['reply_username'] + ')' if state['reply_username'] else ''}\n"
        f"حارس     : {'🛡 شغّال 🔴' if state['guard_active'] else '💤 موقوف'}\n"
        f"حسابات   : {accs}\n"
        f"الرسائل  : {len(state['messages'])} نوع\n"
        f"```"
    )

async def _update_status():
    try:
        if state["status_msg_id"]:
            await client.edit_message(
                CONTROL_CHAT, state["status_msg_id"],
                _status_text(), parse_mode="md")
    except Exception:
        pass

async def _notify(text: str, parse_mode="md"):
    try:
        await client.send_message(CONTROL_CHAT, text, parse_mode=parse_mode)
    except Exception as e:
        log.error(f"_notify: {e}")

def _is_admin(event) -> bool:
    return event.sender_id == ADMIN_ID

def _parse_interval(raw: str) -> float:
    raw = raw.strip().lower()
    if raw.endswith("ms"):
        return float(raw[:-2]) / 1000
    elif raw.endswith("m"):
        return float(raw[:-1]) * 60
    elif raw.endswith("s"):
        return float(raw[:-1])
    return float(raw)


# ══════════════════════════════════════════════════════════════════════════════
#  لوحة المفاتيح الرئيسية
# ══════════════════════════════════════════════════════════════════════════════

MAIN_KB = [
    [("▶ ابدأ","act:start"),    ("⏸ مؤقت","act:pause"),   ("⏹ وقف","act:stop")],
    [("📋 الرسائل","menu:msgs"), ("⚙ الإعدادات","menu:settings")],
    [("🛡 حارس","act:guard"),    ("🗑 احذف رسائلي","act:delmymsgs")],
    [("👤 حسابات","menu:accounts"),("📊 الحالة","act:status")],
    [("❓ مساعدة","act:help")],
]

def _kb(rows):
    return [[types.KeyboardButtonCallback(l,c.encode()) for l,c in row] for row in rows]

MSGS_KB = [
    [("➕ أضف رسالة","prompt:add"),    ("📋 اعرض","act:listmsgs")],
    [("🗑 امسح الكل","act:clearmsgs"),  ("💾 صدّر JSON","act:exportmsgs")],
    [("🔙 رجوع","act:mainmenu")],
]

SETTINGS_KB = [
    [("⚡ عادي","setmode:normal"),  ("🚀 رصاصة","setmode:bullet"),("✍️ بشري","setmode:human")],
    [("🔀 عشوائي","toggle:random"), ("⏱ الفاصل","prompt:interval")],
    [("🔗 فاصل الكلمات","prompt:sep"), ("📌 نص إضافي","prompt:suffix")],
    [("↩️ ردّ على آخر","toggle:reply"), ("👤 فلتر المرسل","prompt:replyuser")],
    [("🔙 رجوع","act:mainmenu")],
]


# ══════════════════════════════════════════════════════════════════════════════
#  الأوامر
# ══════════════════════════════════════════════════════════════════════════════

@client.on(events.NewMessage(pattern=r"^/start$"))
async def cmd_start(event):
    if not _is_admin(event): return
    _load_messages()
    msg = await event.reply(
        "⚡ **هادر بوت** — مرحباً!\n" + _status_text(),
        parse_mode="md", buttons=_kb(MAIN_KB))
    state["status_msg_id"] = msg.id

@client.on(events.NewMessage(pattern=r"^/status$"))
async def cmd_status(event):
    if not _is_admin(event): return
    msg = await event.reply(_status_text(), parse_mode="md", buttons=_kb(MAIN_KB))
    state["status_msg_id"] = msg.id

# ── الشات المستهدف ───────────────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r"^/chat (.+)$"))
async def cmd_chat(event):
    if not _is_admin(event): return
    state["target_chat"] = event.pattern_match.group(1).strip()
    await event.reply(f"✅ الشات: `{state['target_chat']}`", parse_mode="md")

# ── الرسائل ──────────────────────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r"^/add (.+)$"))
async def cmd_add(event):
    if not _is_admin(event): return
    raw = event.pattern_match.group(1).strip()
    m   = re.match(r"^(\d+)\s*\|\s*(.+)$", raw, re.DOTALL)
    repeat, text = (int(m.group(1)), m.group(2).strip()) if m else (1, raw)
    state["messages"].append({"text": text, "repeat": repeat})
    _save_messages()
    await event.reply(
        f"✅ رسالة #{len(state['messages'])} (×{repeat})\n`{text[:80]}`", parse_mode="md")

@client.on(events.NewMessage(pattern=r"^/bulk\n([\s\S]+)$"))
async def cmd_bulk(event):
    if not _is_admin(event): return
    raw   = event.pattern_match.group(1)
    parts = [p.strip() for p in re.split(r"\n\*\n|\*", raw) if p.strip()]
    for p in parts:
        state["messages"].append({"text": p, "repeat": 1})
    _save_messages()
    await event.reply(f"✅ أُضيف {len(parts)} رسائل. المجموع: {len(state['messages'])}")

@client.on(events.NewMessage(pattern=r"^/list$"))
async def cmd_list(event):
    if not _is_admin(event): return
    if not state["messages"]:
        await event.reply("📭 لا توجد رسائل."); return
    lines = ["📋 **الرسائل:**\n"]
    for i, m in enumerate(state["messages"], 1):
        lines.append(f"`{i}.` ×{m['repeat']} — {m['text'][:50].replace(chr(10),' ')}")
    await event.reply("\n".join(lines), parse_mode="md")

@client.on(events.NewMessage(pattern=r"^/del_msg (\d+)$"))
async def cmd_del_msg(event):
    if not _is_admin(event): return
    idx = int(event.pattern_match.group(1)) - 1
    if 0 <= idx < len(state["messages"]):
        r = state["messages"].pop(idx); _save_messages()
        await event.reply(f"✅ حُذفت: `{r['text'][:50]}`", parse_mode="md")
    else:
        await event.reply("❌ رقم غير صحيح.")

@client.on(events.NewMessage(pattern=r"^/edit_repeat (\d+) (\d+)$"))
async def cmd_edit_repeat(event):
    if not _is_admin(event): return
    idx, rpt = int(event.pattern_match.group(1))-1, int(event.pattern_match.group(2))
    if 0 <= idx < len(state["messages"]):
        state["messages"][idx]["repeat"] = rpt; _save_messages()
        await event.reply(f"✅ رسالة #{idx+1} تكرار = {rpt}")
    else:
        await event.reply("❌ رقم غير صحيح.")

@client.on(events.NewMessage(pattern=r"^/clear_msgs$"))
async def cmd_clear_msgs(event):
    if not _is_admin(event): return
    state["messages"] = []; _save_messages()
    await event.reply("✅ مسح جميع الرسائل.")

@client.on(events.NewMessage(pattern=r"^/save_msgs$"))
async def cmd_save_msgs(event):
    if not _is_admin(event): return
    if not state["messages"]:
        await event.reply("❌ لا توجد رسائل."); return
    _save_messages()
    await client.send_file(CONTROL_CHAT, MESSAGES_FILE, caption="📦 ملف الرسائل")

# ── الإعدادات ─────────────────────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r"^/interval (.+)$"))
async def cmd_interval(event):
    if not _is_admin(event): return
    try:
        state["interval_s"] = max(0.0, _parse_interval(event.pattern_match.group(1)))
        await event.reply(f"✅ الفاصل = `{state['interval_s']}s`", parse_mode="md")
    except:
        await event.reply("❌ مثال: /interval 2s | 500ms | 1m | 0")

@client.on(events.NewMessage(pattern=r"^/mode (normal|bullet|human)$"))
async def cmd_mode(event):
    if not _is_admin(event): return
    state["mode"] = event.pattern_match.group(1)
    await event.reply(f"✅ الوضع = `{state['mode']}` ({_mode_ar()})", parse_mode="md")

@client.on(events.NewMessage(pattern=r"^/human_speed (\d+)$"))
async def cmd_human_speed(event):
    if not _is_admin(event): return
    state["human_delay_ms"] = max(0, int(event.pattern_match.group(1)))
    await event.reply(f"✅ سرعة البشري = `{state['human_delay_ms']}ms`", parse_mode="md")

@client.on(events.NewMessage(pattern=r"^/random (on|off)$"))
async def cmd_random(event):
    if not _is_admin(event): return
    state["random_order"] = event.pattern_match.group(1) == "on"
    await event.reply(f"✅ عشوائي = `{'on' if state['random_order'] else 'off'}`", parse_mode="md")

@client.on(events.NewMessage(pattern=r"^/sep (.+)$"))
async def cmd_sep(event):
    if not _is_admin(event): return
    v = event.pattern_match.group(1).strip()
    state["separator"] = None if v.lower() == "off" else v
    await event.reply(f"✅ فاصل الكلمات = `{state['separator'] or 'off'}`", parse_mode="md")

@client.on(events.NewMessage(pattern=r"^/suffix off$"))
async def cmd_suffix_off(event):
    if not _is_admin(event): return
    state["suffix_on"] = False
    await event.reply("✅ النص الإضافي معطّل.")

@client.on(events.NewMessage(pattern=r"^/suffix (start|mid|end) (.+)$"))
async def cmd_suffix(event):
    if not _is_admin(event): return
    state["suffix_on"]   = True
    state["suffix_pos"]  = event.pattern_match.group(1)
    state["suffix_text"] = event.pattern_match.group(2).strip()
    await event.reply(
        f"✅ نص إضافي: `{state['suffix_text']}` في `{state['suffix_pos']}`", parse_mode="md")

@client.on(events.NewMessage(pattern=r"^/reply (on|off)$"))
async def cmd_reply(event):
    if not _is_admin(event): return
    state["reply_on"] = event.pattern_match.group(1) == "on"
    await event.reply(f"✅ ردّ = `{'on' if state['reply_on'] else 'off'}`", parse_mode="md")

@client.on(events.NewMessage(pattern=r"^/reply_user (.+)$"))
async def cmd_reply_user(event):
    if not _is_admin(event): return
    v = event.pattern_match.group(1).strip()
    state["reply_username"] = "" if v.lower() in ("off","") else v.lstrip("@")
    await event.reply(
        f"✅ فلتر الرد = `{state['reply_username'] or 'أي شخص'}`", parse_mode="md")

@client.on(events.NewMessage(pattern=r"^/settings$"))
async def cmd_settings(event):
    if not _is_admin(event): return
    suf = (f"`{state['suffix_text'][:20]}` ({state['suffix_pos']})"
           if state["suffix_on"] else "معطّل")
    await event.reply(
        f"⚙️ **الإعدادات:**\n\n"
        f"🎯 الشات: `{state['target_chat'] or '—'}`\n"
        f"⏱ الفاصل: `{state['interval_s']}s`\n"
        f"🎮 الوضع: `{state['mode']}` ({_mode_ar()})\n"
        f"✍️ سرعة البشري: `{state['human_delay_ms']}ms`\n"
        f"🎲 عشوائي: `{'on' if state['random_order'] else 'off'}`\n"
        f"🔗 فاصل: `{state['separator'] or 'off'}`\n"
        f"📌 نص إضافي: {suf}\n"
        f"↩️ ردّ: `{'on' if state['reply_on'] else 'off'}`"
        f" — فلتر: `{state['reply_username'] or 'أي شخص'}`\n"
        f"📋 رسائل: `{len(state['messages'])}`\n"
        f"👤 حسابات: `{1+len(state['extra_sessions'])}`",
        parse_mode="md")

@client.on(events.NewMessage(pattern=r"^/reset$"))
async def cmd_reset(event):
    if not _is_admin(event): return
    keep = {k: state[k] for k in ("messages","extra_sessions","status_msg_id")}
    state.update(deepcopy(DEFAULT_STATE)); state.update(keep)
    await event.reply("✅ تمت إعادة الإعدادات للافتراضي.")

# ── التحكم في الإرسال ────────────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r"^/start_send$"))
async def cmd_start_send(event):
    if not _is_admin(event): return
    if state["running"]:
        await event.reply("❌ الإرسال شغّال. /stop أولاً"); return
    if not state["target_chat"]:
        await event.reply("❌ /chat @username أولاً"); return
    if not state["messages"]:
        await event.reply("❌ أضف رسائل: /add نص"); return
    state.update({"running":True,"paused":False,"stop_requested":False,
                  "done_counts":[0]*len(state["messages"])})
    msg = await event.reply(
        "▶️ **بدأ الإرسال...**\n/pause مؤقت | /stop إيقاف", parse_mode="md")
    state["status_msg_id"] = msg.id
    asyncio.create_task(_send_loop())

@client.on(events.NewMessage(pattern=r"^/pause$"))
async def cmd_pause(event):
    if not _is_admin(event): return
    if not state["running"]:
        await event.reply("❌ لا يوجد إرسال."); return
    state["paused"] = not state["paused"]
    await event.reply("⏸ مؤقت." if state["paused"] else "▶️ استئناف.")
    await _update_status()

@client.on(events.NewMessage(pattern=r"^/stop$"))
async def cmd_stop(event):
    if not _is_admin(event): return
    state["running"] = False; state["stop_requested"] = True
    await event.reply("⏹ **تم الإيقاف.**", parse_mode="md")
    await _update_status()

# ── متعدد الحسابات ───────────────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r"^/add_account (\w+)$"))
async def cmd_add_account(event):
    if not _is_admin(event): return
    name = event.pattern_match.group(1)
    path = os.path.join(SESSIONS_DIR, name)
    try:
        extra = TelegramClient(path, API_ID, API_HASH)
        await extra.connect()
        if not await extra.is_user_authorized():
            state[f"_pclient_{name}"] = extra
            await event.reply(
                f"📱 أرسل رقم هاتف الحساب:\n`/phone_{name} +966xxxxxxxxx`",
                parse_mode="md")
        else:
            me = await extra.get_me()
            state["extra_sessions"].append({"name":name,"client":extra})
            await event.reply(
                f"✅ `{me.first_name}` (@{me.username}) أُضيف!", parse_mode="md")
    except Exception as e:
        await event.reply(f"❌ {e}")

@client.on(events.NewMessage(pattern=r"^/phone_(\w+) (\+\d+)$"))
async def cmd_phone(event):
    if not _is_admin(event): return
    name, phone = event.pattern_match.group(1), event.pattern_match.group(2)
    extra = state.get(f"_pclient_{name}")
    if not extra:
        await event.reply(f"❌ ابدأ بـ /add_account {name}"); return
    try:
        await extra.send_code_request(phone)
        state[f"_pphone_{name}"] = phone
        await event.reply(f"📟 أرسل الكود:\n`/code_{name} XXXXX`", parse_mode="md")
    except Exception as e:
        await event.reply(f"❌ {e}")

@client.on(events.NewMessage(pattern=r"^/code_(\w+) (\d+)$"))
async def cmd_code(event):
    if not _is_admin(event): return
    name, code = event.pattern_match.group(1), event.pattern_match.group(2)
    extra = state.get(f"_pclient_{name}")
    phone = state.get(f"_pphone_{name}")
    if not extra or not phone:
        await event.reply("❌ ابدأ بـ /add_account"); return
    try:
        await extra.sign_in(phone, code)
        me = await extra.get_me()
        state["extra_sessions"].append({"name":name,"client":extra})
        for k in (f"_pclient_{name}", f"_pphone_{name}"):
            state.pop(k, None)
        await event.reply(
            f"✅ `{me.first_name}` (@{me.username}) دخل بنجاح!", parse_mode="md")
    except Exception as e:
        await event.reply(f"❌ {e}")

@client.on(events.NewMessage(pattern=r"^/list_accounts$"))
async def cmd_list_accounts(event):
    if not _is_admin(event): return
    me    = await client.get_me()
    lines = [f"👤 **الحسابات:**\n1. {me.first_name} (@{me.username}) ← رئيسي"]
    for i,s in enumerate(state["extra_sessions"],2):
        lines.append(f"{i}. {s['name']}")
    await event.reply("\n".join(lines), parse_mode="md")

@client.on(events.NewMessage(pattern=r"^/remove_account (\w+)$"))
async def cmd_remove_account(event):
    if not _is_admin(event): return
    name = event.pattern_match.group(1)
    for i,s in enumerate(state["extra_sessions"]):
        if s["name"] == name:
            try: await s["client"].disconnect()
            except: pass
            state["extra_sessions"].pop(i)
            await event.reply(f"✅ حُذف حساب `{name}`.", parse_mode="md"); return
    await event.reply(f"❌ ما وجدت `{name}`.", parse_mode="md")

# ── حارس الشات ───────────────────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r"^/guard$"))
async def cmd_guard(event):
    if not _is_admin(event): return
    if not state["target_chat"]:
        await event.reply("❌ /chat @username أولاً"); return
    if state["guard_active"]:
        state["guard_active"] = False
        await event.reply("🛡 حارس الشات: **أُوقف.**", parse_mode="md")
    else:
        state["guard_active"] = True
        await event.reply(
            "🛡 حارس الشات: **شغّال 🔴**\nيحذف رسائل الطرف الثاني فوراً.",
            parse_mode="md")
        asyncio.create_task(_guard_loop())

# ── حذف رسائلي ───────────────────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r"^/delete_mine$"))
async def cmd_delete_mine(event):
    if not _is_admin(event): return
    if not state["target_chat"]:
        await event.reply("❌ /chat @username أولاً"); return
    msg = await event.reply("🗑 جاري حذف رسائلك...")
    asyncio.create_task(_delete_my_messages_task(msg))

# ── المساعدة ─────────────────────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r"^/help$"))
async def cmd_help(event):
    if not _is_admin(event): return
    await event.reply(HELP_TEXT, parse_mode="md")


# ══════════════════════════════════════════════════════════════════════════════
#  استقبال ملف JSON
# ══════════════════════════════════════════════════════════════════════════════

@client.on(events.NewMessage())
async def handle_file(event):
    if not _is_admin(event) or not event.file: return
    fname = getattr(event.file,"name","") or ""
    if not fname.endswith(".json"): return
    data = await event.download_media(bytes)
    try:
        msgs = json.loads(data.decode("utf-8"))
        normalized = []
        for m in msgs:
            if isinstance(m, dict):
                t = m.get("text") or m.get("message","")
                r = m.get("repeat",1)
                if t: normalized.append({"text":t,"repeat":r})
            elif isinstance(m, str):
                normalized.append({"text":m,"repeat":1})
        if not normalized: raise ValueError
        state["_pending_file"] = normalized
        await event.reply(
            f"📂 {len(normalized)} رسالة. أضف أم استبدل؟",
            buttons=[[
                types.KeyboardButtonCallback("➕ أضف","file:append"),
                types.KeyboardButtonCallback("🔄 استبدل","file:replace"),
            ]])
    except:
        await event.reply("❌ صيغة الملف غير صحيحة.")


# ══════════════════════════════════════════════════════════════════════════════
#  Callback Buttons
# ══════════════════════════════════════════════════════════════════════════════

@client.on(events.CallbackQuery())
async def cb(event):
    if event.sender_id != ADMIN_ID: return
    d = event.data.decode()

    # ── إجراءات الإرسال ──
    if d == "act:start":
        if state["running"]:
            await event.answer("⚠️ شغّال بالفعل!")
        elif not state["target_chat"]:
            await event.answer("❌ /chat @username أولاً")
        elif not state["messages"]:
            await event.answer("❌ أضف رسائل أولاً")
        else:
            state.update({"running":True,"paused":False,"stop_requested":False,
                          "done_counts":[0]*len(state["messages"])})
            asyncio.create_task(_send_loop())
            await event.answer("▶️ بدأ الإرسال!")
            await _update_status()

    elif d == "act:pause":
        if state["running"]:
            state["paused"] = not state["paused"]
            await event.answer("⏸ مؤقت" if state["paused"] else "▶️ استئناف")
            await _update_status()
        else:
            await event.answer("❌ لا يوجد إرسال")

    elif d == "act:stop":
        state["running"] = False; state["stop_requested"] = True
        await event.answer("⏹ تم الإيقاف")
        await _update_status()

    elif d == "act:guard":
        if not state["target_chat"]:
            await event.answer("❌ /chat @username أولاً"); return
        state["guard_active"] = not state["guard_active"]
        if state["guard_active"]:
            asyncio.create_task(_guard_loop())
            await event.answer("🛡 الحارس شغّال!")
        else:
            await event.answer("🛡 الحارس أُوقف")
        await _update_status()

    elif d == "act:delmymsgs":
        if not state["target_chat"]:
            await event.answer("❌ /chat @username أولاً"); return
        await event.answer("🗑 جاري الحذف...")
        asyncio.create_task(_delete_my_messages_task())

    elif d == "act:status":
        await event.answer()
        msg = await client.send_message(
            CONTROL_CHAT, _status_text(), parse_mode="md", buttons=_kb(MAIN_KB))
        state["status_msg_id"] = msg.id

    elif d == "act:help":
        await event.answer()
        await client.send_message(CONTROL_CHAT, HELP_TEXT, parse_mode="md")

    elif d == "act:mainmenu":
        await event.answer()
        msg = await client.send_message(
            CONTROL_CHAT, _status_text(), parse_mode="md", buttons=_kb(MAIN_KB))
        state["status_msg_id"] = msg.id

    # ── قوائم ──
    elif d == "menu:msgs":
        await event.answer()
        await client.send_message(
            CONTROL_CHAT, "📋 **إدارة الرسائل**", parse_mode="md", buttons=_kb(MSGS_KB))

    elif d == "act:listmsgs":
        await event.answer()
        if not state["messages"]:
            await client.send_message(CONTROL_CHAT, "📭 لا توجد رسائل."); return
        lines = ["📋 **الرسائل:**\n"]
        for i,m in enumerate(state["messages"],1):
            lines.append(f"`{i}.` ×{m['repeat']} — {m['text'][:45].replace(chr(10),' ')}")
        await client.send_message(CONTROL_CHAT, "\n".join(lines), parse_mode="md")

    elif d == "act:clearmsgs":
        state["messages"] = []; _save_messages()
        await event.answer("✅ تم المسح!")

    elif d == "act:exportmsgs":
        if not state["messages"]:
            await event.answer("❌ لا توجد رسائل"); return
        _save_messages()
        await client.send_file(CONTROL_CHAT, MESSAGES_FILE, caption="📦 ملف الرسائل")
        await event.answer("✅ تم الإرسال")

    elif d == "menu:settings":
        await event.answer()
        await client.send_message(
            CONTROL_CHAT,
            f"⚙️ **الإعدادات**\n"
            f"الوضع: {_mode_ar()}  |  الفاصل: {state['interval_s']}s\n"
            f"عشوائي: {'✅' if state['random_order'] else '❌'}  |  "
            f"ردّ: {'✅' if state['reply_on'] else '❌'}",
            parse_mode="md", buttons=_kb(SETTINGS_KB))

    elif d.startswith("setmode:"):
        state["mode"] = d.split(":")[1]
        await event.answer(f"✅ {_mode_ar()}")

    elif d == "toggle:random":
        state["random_order"] = not state["random_order"]
        await event.answer(f"عشوائي = {'on' if state['random_order'] else 'off'}")

    elif d == "toggle:reply":
        state["reply_on"] = not state["reply_on"]
        await event.answer(f"ردّ = {'on' if state['reply_on'] else 'off'}")

    elif d == "prompt:add":
        state["awaiting"] = "add_msg"
        await event.answer()
        await client.send_message(
            CONTROL_CHAT, "✏️ أرسل الرسالة (أو `عدد | نص` للتكرار):", parse_mode="md")

    elif d == "prompt:interval":
        state["awaiting"] = "interval"
        await event.answer()
        await client.send_message(
            CONTROL_CHAT, "⏱ أرسل الفاصل: `2s` | `500ms` | `1m` | `0`")

    elif d == "prompt:sep":
        state["awaiting"] = "sep"
        await event.answer()
        await client.send_message(CONTROL_CHAT, "🔗 أرسل الفاصل (مثل `*`) أو `off`:")

    elif d == "prompt:suffix":
        state["awaiting"] = "suffix"
        await event.answer()
        await client.send_message(
            CONTROL_CHAT, "📌 الصيغة: `start|mid|end نص`\nمثال: `end #هادر`")

    elif d == "prompt:replyuser":
        state["awaiting"] = "replyuser"
        await event.answer()
        await client.send_message(
            CONTROL_CHAT, "👤 أرسل @username للفلتر أو `off` لأي شخص:")

    elif d == "menu:accounts":
        await event.answer()
        me    = await client.get_me()
        lines = [f"👤 **الحسابات:**\n1. {me.first_name} (رئيسي)"]
        for i,s in enumerate(state["extra_sessions"],2):
            lines.append(f"{i}. {s['name']}")
        lines.append("\nلإضافة: `/add_account اسم`")
        await client.send_message(CONTROL_CHAT, "\n".join(lines), parse_mode="md")

    elif d == "file:append":
        p = state.pop("_pending_file",[])
        state["messages"].extend(p); _save_messages()
        await event.answer(f"✅ أُضيف {len(p)} رسائل")

    elif d == "file:replace":
        p = state.pop("_pending_file",[])
        state["messages"] = p; _save_messages()
        await event.answer(f"✅ استُبدل بـ {len(p)} رسائل")


# ══════════════════════════════════════════════════════════════════════════════
#  معالج الإدخال المنتظر
# ══════════════════════════════════════════════════════════════════════════════

@client.on(events.NewMessage())
async def handle_awaiting(event):
    if not _is_admin(event): return
    if not state.get("awaiting") or event.text.startswith("/"): return
    if event.file: return  # الملفات تُعالج أعلاه

    aw = state["awaiting"]; text = event.text.strip()
    state["awaiting"] = None

    if aw == "add_msg":
        m = re.match(r"^(\d+)\s*\|\s*(.+)$", text, re.DOTALL)
        repeat, t = (int(m.group(1)),m.group(2).strip()) if m else (1,text)
        state["messages"].append({"text":t,"repeat":repeat}); _save_messages()
        await event.reply(f"✅ رسالة #{len(state['messages'])} (×{repeat})")

    elif aw == "interval":
        try:
            state["interval_s"] = max(0.0, _parse_interval(text))
            await event.reply(f"✅ الفاصل = `{state['interval_s']}s`", parse_mode="md")
        except:
            await event.reply("❌ مثال: 2s | 500ms | 0")

    elif aw == "sep":
        state["separator"] = None if text.lower() == "off" else text
        await event.reply(f"✅ الفاصل = `{state['separator'] or 'off'}`", parse_mode="md")

    elif aw == "suffix":
        m = re.match(r"^(start|mid|end)\s+(.+)$", text, re.DOTALL)
        if m:
            state.update({"suffix_on":True,"suffix_pos":m.group(1),"suffix_text":m.group(2).strip()})
            await event.reply(
                f"✅ نص إضافي: `{state['suffix_text']}` في `{state['suffix_pos']}`",
                parse_mode="md")
        else:
            await event.reply("❌ مثال: `end #هادر`")

    elif aw == "replyuser":
        state["reply_username"] = "" if text.lower() == "off" else text.lstrip("@")
        await event.reply(
            f"✅ فلتر الرد = `{state['reply_username'] or 'أي شخص'}`", parse_mode="md")


# ══════════════════════════════════════════════════════════════════════════════
#  حلقة الإرسال الرئيسية
# ══════════════════════════════════════════════════════════════════════════════

async def _send_loop():
    log.info("Send loop started")
    all_clients = [client] + [s["client"] for s in state["extra_sessions"]]
    cli_n       = len(all_clients)
    cli_idx     = 0
    msgs        = state["messages"]
    done        = state["done_counts"]
    total       = sum(m["repeat"] for m in msgs)

    try:
        while state["running"] and not state["stop_requested"]:
            while state["paused"] and state["running"]:
                await asyncio.sleep(0.15)
            if not state["running"] or state["stop_requested"]: break
            if sum(done) >= total: break

            available = [i for i,m in enumerate(msgs) if done[i] < m["repeat"]]
            if not available: break

            i    = random.choice(available) if state["random_order"] else available[0]
            text = _apply_msg(msgs[i]["text"])
            cur  = all_clients[cli_idx % cli_n]; cli_idx += 1

            try:
                reply_to = None
                if state["reply_on"]:
                    me_cur = await cur.get_me()
                    fu = state["reply_username"] or None
                    try:
                        async for msg in cur.iter_messages(state["target_chat"], limit=30, from_user=fu):
                            if msg.sender_id != me_cur.id:
                                reply_to = msg.id; break
                    except: pass

                if state["mode"] == "human" and state["human_delay_ms"] > 0:
                    try:
                        await cur(SetTypingRequest(
                            peer=state["target_chat"], action=SendMessageTypingAction()))
                        await asyncio.sleep(min(len(text)*state["human_delay_ms"]/1000, 6.0))
                    except: pass

                await cur.send_message(state["target_chat"], text, reply_to=reply_to)
                done[i] += 1
                log.info(f"Sent [{i+1}] ({done[i]}/{msgs[i]['repeat']}) rem={total-sum(done)}")

                if sum(done) % 10 == 0:
                    await _update_status()

                if state["mode"] == "bullet":
                    await asyncio.sleep(0.02)
                elif state["interval_s"] > 0:
                    await asyncio.sleep(state["interval_s"])

            except FloodWaitError as e:
                log.warning(f"FloodWait {e.seconds}s")
                await _notify(f"⚠️ Telegram: انتظر `{e.seconds}s`...", "md")
                await asyncio.sleep(e.seconds + 2)
            except Exception as e:
                log.error(f"Send: {e}")
                await asyncio.sleep(2)

    finally:
        state["running"] = False; state["stop_requested"] = False
        await _notify(f"✅ **انتهى!** أُرسلت `{sum(state['done_counts'])}` رسالة.", "md")
        await _update_status()
        log.info("Send loop ended")


# ══════════════════════════════════════════════════════════════════════════════
#  حارس الشات
# ══════════════════════════════════════════════════════════════════════════════

async def _guard_loop():
    log.info("Guard loop started")
    me      = await client.get_me()
    target  = state["target_chat"]
    deleted = 0

    # احذف الموجودين أولاً
    await _notify("🛡 حارس الشات: يحذف الرسائل الموجودة...")
    try:
        async for msg in client.iter_messages(target, limit=500):
            if not state["guard_active"]: break
            if msg.sender_id and msg.sender_id != me.id and not msg.service:
                try:
                    await client.delete_messages(target, [msg.id], revoke=True)
                    deleted += 1; await asyncio.sleep(0.2)
                except MessageDeleteForbiddenError: pass
                except Exception as e: log.warning(f"Guard pre: {e}")
    except Exception as e:
        log.error(f"Guard pre-loop: {e}")

    await _notify(f"🛡 حُذف {deleted} موجود. يراقب الجدد الآن...")

    # راقب الجدد بـ event handler
    new_deleted = [0]

    @client.on(events.NewMessage(chats=target))
    async def _on_new(ev):
        if not state["guard_active"]:
            client.remove_event_handler(_on_new); return
        me2 = await client.get_me()
        if ev.sender_id != me2.id and not ev.message.service:
            try:
                await asyncio.sleep(0.4)
                await client.delete_messages(target, [ev.message.id], revoke=True)
                new_deleted[0] += 1
                log.info(f"Guard: deleted new {ev.message.id}")
            except Exception as e:
                log.warning(f"Guard new: {e}")

    while state["guard_active"]:
        await asyncio.sleep(1.0)

    client.remove_event_handler(_on_new)
    state["guard_active"] = False
    await _notify(f"🛡 الحارس توقف. حُذف {new_deleted[0]} رسالة جديدة.")
    log.info("Guard loop ended")


# ══════════════════════════════════════════════════════════════════════════════
#  حذف رسائلي
# ══════════════════════════════════════════════════════════════════════════════

async def _delete_my_messages_task(progress_msg=None):
    log.info("Deleting my messages...")
    me = await client.get_me(); target = state["target_chat"]
    ids = []
    try:
        async for msg in client.iter_messages(target, limit=1000, from_user=me.id):
            ids.append(msg.id)
        for i in range(0, len(ids), 100):
            try:
                await client.delete_messages(target, ids[i:i+100], revoke=False)
            except Exception as e:
                log.warning(f"Batch del: {e}")
            await asyncio.sleep(0.4)
        result = f"✅ حُذف `{len(ids)}` رسالة من رسائلك."
        await _notify(result, "md")
        if progress_msg:
            try: await progress_msg.edit(result)
            except: pass
        log.info(f"Deleted {len(ids)} my messages")
    except Exception as e:
        await _notify(f"❌ خطأ: {e}")
        log.error(f"Delete mine: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  نص المساعدة
# ══════════════════════════════════════════════════════════════════════════════

HELP_TEXT = """
⚡ **هادر بوت — الأوامر الكاملة**

**🎯 أساسيات:**
`/start` — لوحة التحكم الرئيسية
`/chat @username` — تحديد الشات
`/start_send` — ابدأ الإرسال
`/pause` — مؤقت / استئناف
`/stop` — إيقاف كامل
`/status` — الحالة
`/settings` — جميع الإعدادات

**📋 الرسائل:**
`/add نص` — إضافة (×1)
`/add 5 | نص` — إضافة (×5)
`/bulk` ثم رسائل مفصولة بـ `*` في سطر منفصل
`/list` — عرض الرسائل
`/del_msg 2` — حذف رسالة #2
`/edit_repeat 2 10` — تعديل تكرار
`/clear_msgs` — مسح الكل
`/save_msgs` — تصدير JSON
↑ أو أرسل ملف JSON لاستيراد رسائل

**⏱ الفاصل الزمني:**
`/interval 2s` — ثانيتان
`/interval 500ms` — 500 ميلي ثانية
`/interval 1m` — دقيقة
`/interval 0` — بلا فاصل

**🎮 الوضع:**
`/mode normal` — عادي ⚡
`/mode bullet` — رصاصة 🚀 (أقصى سرعة)
`/mode human` — بشري ✍️
`/human_speed 80` — ms بين الأحرف (0=أقصى)
`/random on|off` — ترتيب عشوائي

**📌 تعديل الرسائل:**
`/sep *` — فاصل بين الكلمات
`/sep off` — إيقاف الفاصل
`/suffix end #هادر` — نص في النهاية
`/suffix start رد:` — نص في البداية
`/suffix mid كلمة` — نص في الوسط
`/suffix off` — إيقاف النص الإضافي

**↩️ الرد على آخر رسالة:**
`/reply on|off` — تفعيل/إيقاف
`/reply_user @username` — فلتر المرسل
`/reply_user off` — أي شخص

**👤 متعدد الحسابات:**
`/add_account اسم` — إضافة حساب
`/list_accounts` — عرض الحسابات
`/remove_account اسم` — حذف حساب
(الإرسال يتوزع دورياً على الكل)

**🛠️ الأدوات:**
`/guard` — حارس الشات (يحذف رسائل الطرف الثاني)
`/delete_mine` — حذف رسائلك من الشات

**⚙️ متقدم:**
`/reset` — إعادة الإعدادات للافتراضي
"""


# ══════════════════════════════════════════════════════════════════════════════
#  التشغيل
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    import config

    _load_messages()
    log.info("⚡ هادر بوت — بدأ التشغيل...")
    
    try:
        # 1. الاتصال بالسيرفر
        await client.connect()
        
        # 2. تسجيل الدخول الصريح بالتوكن
        log.info("🔐 جاري التحقق من التوكن والـ API...")
        await client.start(bot_token=config.BOT_TOKEN)
        
        me = await client.get_me()
        log.info(f"Logged in: {me.first_name} (@{me.username})")
        
        # 3. إرسال رسالة التفعيل
        await client.send_message(
            config.CONTROL_CHAT,
            f"⚡ **هادر بوت شغّال! 🟢**\n"
            f"الحساب: {me.first_name} (@{me.username})\n"
            f"أرسل /start للوحة التحكم أو /help للأوامر.",
            parse_mode="md"
        )
        
        # استمرار تشغيل البوت في حال نجاح الاتصال
        await client.run_until_disconnected()

    except telethon.errors.rpcerrorlist.ApiIdInvalidError:
        log.error("❌ خطأ قاتل: الـ API_ID أو الـ API_HASH غير صحيح! تأكد منهم في موقع my.telegram.org")
        return # إيقاف السيرفر تماماً لتعديل البيانات
    except Exception as e:
        log.error(f"❌ حدث خطأ غير متوقع: {e}")
        return


if __name__ == "__main__":
    asyncio.run(main())
