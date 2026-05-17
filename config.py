import os

# سيحاول الكود قراءة القيم من متغيرات الاستضافة أولاً، وإذا لم يجدها سيأخذ القيم الافتراضية المكتوبة هنا
BOT_TOKEN = os.getenv("BOT_TOKEN", "6547204382:AAGF11w-KmizISyGLA2OYW-geZ8DfarFivE")

# الـ API_ID يجب أن يكون رقماً (int)
API_ID = int(os.getenv("API_ID", 24391216))

API_HASH = os.getenv("API_HASH", "b40d413dc508dbbe838f5f67a68393fa")
