"""
⚡ هادر بوت — النسخة الاحترافية متعددة المستخدمين (تسجيل دخول ذكي)
البوت يستقبل الأوامر من المستخدمين ويربط حساباتهم الشخصية تلقائياً عبر التوكن.
"""

import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# حفظ الكلاينتات النشطة بالذاكرة مؤقتاً أثناء تسجيل الدخول
USER_CLIENTS = {}

# حالات نظام الفلو (FSM) لتوجيه المستخدم خطوة بخطوة
class LoginStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()

# ══════════════════════════════════════════════════════════════════════════════
#  الأوامر العامة للمستخدمين
# ══════════════════════════════════════════════════════════════════════════════
@dp.message(Command("start"))
async def cmd_start(message: Message):
    welcome_text = (
        "👋 **أهلاً بك في هادر بوت للإرسال التلقائي!**\n\n"
        "عشان البوت يرسل باسم حسابك الشخصي في الشات اللي تبغاه، تحتاج تربط حسابك أولاً بطريقة آمنة وسهلة.\n\n"
        "👉 أرسل أمر `/login` للبدء في ربط حسابك الآن."
    )
    await message.reply(welcome_text, parse_mode="Markdown")

# ══════════════════════════════════════════════════════════════════════════════
#  خطوات تسجيل الدخول الذكي (بدون ملفات جلسة يدوية)
# ══════════════════════════════════════════════════════════════════════════════

# 1. طلب رقم الجوال
@dp.message(Command("login"))
async def cmd_login(message: Message, state: FSMContext):
    await message.reply("📱 ممتاز، أرسل الآن رقم جوالك مع رمز الدولة.\nمثال: `+9665xxxxxxxx`", parse_mode="Markdown")
    await state.set_state(LoginStates.waiting_for_phone)

# 2. استقبال الرقم وبدء جلسة Telethon بالخلفية
@dp.message(LoginStates.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "")
    user_id = message.from_user.id
    
    await message.reply("⏳ جاري الاتصال بسيرفرات تليجرام وإرسال كود التحقق لك...")
    
    # إنشاء اسم ملف جلسة خاص بهذا المستخدم بناءً على الآيدي حقه
    session_path = f"sessions/user_{user_id}"
    os.makedirs("sessions", exist_ok=True)
    
    client = TelegramClient(session_path, config.API_ID, config.API_HASH)
    await client.connect()
    
    try:
        # إرسال الكود لحساب المستخدم
        send_code_result = await client.send_code_request(phone)
        
        # حفظ الكلاينت وبيانات الجلسة مؤقتاً بالذاكرة لاستكمال الخطوات
        USER_CLIENTS[user_id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": send_code_result.phone_code_hash
        }
        
        await message.reply("📩 وصلك الآن كود تحقق من تليجرام (داخل تطبيق تليجرام نفسه).\nأرسل الكود هنا في الشات فوراً:")
        await state.set_state(LoginStates.waiting_for_code)
        
    except Exception as e:
        log.error(f"Login error for {user_id}: {e}")
        await message.reply(f"❌ حدث خطأ أثناء إرسال الكود: {e}\nأرسل `/login` للمحاولة مجدداً.")
        await client.disconnect()
        await state.clear()

# 3. استقبال كود التحقق وتفعيل الحساب الشخصي
@dp.message(LoginStates.waiting_for_code)
async def process_code(message: Message, state: FSMContext):
    user_id = message.from_user.id
    code = message.text.strip()
    
    if user_id not in USER_CLIENTS:
        await message.reply("❌ انتهت مهلة الجلسة، أرسل `/login` من جديد.")
        await state.clear()
        return
        
    user_data = USER_CLIENTS[user_id]
    client = user_data["client"]
    
    try:
        # محاولة تسجيل الدخول بالكود المكتوب
        await client.sign_in(
            phone=user_data["phone"],
            code=code,
            phone_code_hash=user_data["phone_code_hash"]
        )
        
        await message.reply("🟢 **تم ربط حسابك الشخصي بنجاح تام!**\nالحين البوت يقدر يرسل تلقائياً باسمك.\n\nتستطيع البدء باستخدام الأوامر لتجهيز الإرسال.")
        await state.clear()
        # هنا الجلسة انحفظت بملف اسمه sessions/user_ID.session وتقدر تستدعيها وقت الإرسال التلقائي
        
    except SessionPasswordNeededError:
        # إذا كان المستخدم مفعل التحقق بخطوتين (المرور الثاني)
        await message.reply("🔒 حسابك محمي بكلمة مرور (التحقق بخطوتين)، يرجى إرسال كلمة المرور الخاصة بك:")
        await state.set_state(LoginStates.waiting_for_password)
        
    except Exception as e:
        await message.reply(f"❌ الكود غير صحيح أو منتهي: {e}\nأرسل `/login` للمحاولة مجدداً.")
        await client.disconnect()
        await state.clear()

# 4. استقبال الباسورد (لو مفعّل التحقق بخطوتين)
@dp.message(LoginStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext):
    user_id = message.from_user.id
    password = message.text.strip()
    
    user_data = USER_CLIENTS[user_id]
    client = user_data["client"]
    
    try:
        await client.sign_in(password=password)
        await message.reply("🟢 **تم التحقق من كلمة المرور وربط حسابك الشخصي بنجاح!**")
        await state.clear()
    except Exception as e:
        await message.reply(f"❌ كلمة المرور خاطئة: {e}\nأرسل `/login` للمحاولة مجدداً.")
        await client.disconnect()
        await state.clear()


async def main():
    log.info("🚀 تشغيل السيرفر متعدد المستخدمين بذخيرة تسجيل الدخول التلقائي...")
    bot_info = await bot.get_me()
    log.info(f"Bot @{bot_info.username} is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
