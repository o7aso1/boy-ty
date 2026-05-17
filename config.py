import os

# 1. بيانات التليجرام والـ API (تم استخراجها من لقطات الشاشة)
BOT_TOKEN = os.getenv("BOT_TOKEN", "7905187747:AAEvpRE2U8C94p9N3S6VqIic9rK7uO_jRDo")
API_ID = int(os.getenv("API_ID", 24391216))
API_HASH = os.getenv("API_HASH", "b40d413dc508dbbe838f5f67a68393fa")

# 2. إعدادات الجلسة والتحكم (تم استخراجها من لقطات الشاشة)
SESSION_NAME = os.getenv("SESSION_NAME", "hadar_bot")
ADMIN_ID = int(os.getenv("ADMIN_ID", 6432029959))
CONTROL_CHAT = int(os.getenv("CONTROL_CHAT", -1002492160176))
