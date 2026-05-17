# ⚡ هادر بوت — دليل التثبيت الكامل

## المتطلبات
- حساب Telegram
- Python 3.9+ (على جهازك أو السيرفر)

---

## الخطوة 1 — احصل على API credentials

1. افتح: https://my.telegram.org/apps
2. سجّل دخول برقم تيليجرام
3. اضغط "Create new application"
4. انسخ **api_id** و **api_hash**

---

## الخطوة 2 — عدّل config.py

افتح ملف `config.py` وعبّي:

```python
API_ID   = 12345678        # رقمك من الخطوة 1
API_HASH = "abc123..."     # الـ hash من الخطوة 1
ADMIN_ID = 987654321       # رقمك — أرسل /start لـ @userinfobot
CONTROL_CHAT = "me"        # "me" = رسائلك المحفوظة (الأبسط)
```

---

## الخطوة 3 — تشغيل أول مرة (على جهازك)

```bash
pip install -r requirements.txt
python bot.py
```

سيطلب منك رقم هاتفك وكود التحقق (مرة وحدة فقط).
بعدها يُنشئ ملف الجلسة `hadir_session.session`.

---

## الخطوة 4 — رفع على سيرفر مجاني (Railway)

### الطريقة الأسهل — Railway.app (مجاني):

1. اشترك في https://railway.app (بحساب GitHub)
2. اعمل repo على GitHub وارفع الملفات
3. في Railway: New Project → Deploy from GitHub
4. أضف Environment Variables:
   - `API_ID` = رقمك
   - `API_HASH` = الـ hash
   - `ADMIN_ID` = رقمك
5. ارفع ملف `hadir_session.session` (من جهازك بعد أول تشغيل)

### أو على Render.com (مجاني):

1. اشترك في https://render.com
2. New → Background Worker
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python bot.py`
5. أضف Environment Variables نفسها

---

## الخطوة 5 — التحكم من الجوال

افتح تيليجرام على جوالك، اذهب لـ **Saved Messages** (رسائلك المحفوظة)،
وابدأ ترسل أوامر:

---

## قائمة الأوامر

### الإعداد
| الأمر | الوظيفة |
|-------|---------|
| `/start` | لوحة التحكم الرئيسية |
| `/chat @username` | تحديد الشات المستهدف |
| `/add نص الرسالة` | إضافة رسالة (تكرار 1) |
| `/add 5 \| نص الرسالة` | إضافة رسالة بتكرار 5 |
| `/bulk` (ثم الرسائل مفصولة بـ *) | إضافة رسائل متعددة |
| `/list` | عرض الرسائل المحفوظة |
| `/del_msg 2` | حذف رسالة رقم 2 |
| `/clear_msgs` | مسح جميع الرسائل |

### الإعدادات
| الأمر | الوظيفة |
|-------|---------|
| `/interval 2s` | فاصل 2 ثانية |
| `/interval 500ms` | فاصل 500 ميلي ثانية |
| `/interval 1m` | فاصل دقيقة |
| `/mode normal` | وضع عادي |
| `/mode bullet` | وضع رصاصة (بلا توقف) |
| `/mode human` | وضع كتابة بشرية |
| `/human_speed 80` | 80ms بين كل حرف (0 = أقصى سرعة) |
| `/random on` | ترتيب عشوائي |
| `/separator *` | فاصل بين الكلمات |
| `/suffix end #هادر` | نص في النهاية |
| `/suffix start رد:` | نص في البداية |
| `/suffix mid كلمة` | نص في الوسط |
| `/suffix off` | إلغاء الـ suffix |
| `/reply on` | رد على آخر رسالة قبل كل إرسال |

### التحكم
| الأمر | الوظيفة |
|-------|---------|
| `/start_send` | ابدأ الإرسال |
| `/pause` | إيقاف مؤقت / استئناف |
| `/stop` | إيقاف نهائي |
| `/status` | عرض الحالة |
| `/settings` | عرض جميع الإعدادات |

### الأدوات
| الأمر | الوظيفة |
|-------|---------|
| `/guard` | تشغيل/إيقاف حارس الشات |
| `/delete_mine` | حذف رسائلك من الشات المستهدف |

---

## مثال عملي سريع

```
/chat @someone
/add 3 | مرحبا كيف حالك
/add 2 | أهلاً وسهلاً
/interval 1s
/mode bullet
/start_send
```

---

## ملاحظات مهمة

- ⚠️ البوت يعمل على **حسابك الشخصي** (userbot) — مو بوت منفصل
- ⚠️ تجنب الإرسال المفرط لأن Telegram قد يحظر الحساب مؤقتاً
- ✅ ملف الجلسة `hadir_session.session` حساس — لا تشاركه مع أحد
- ✅ الرسائل تُحفظ تلقائياً في `messages.json`

---

## استكشاف الأخطاء

**"Session file not found"**: شغّل على جهازك أولاً لتسجيل الجلسة

**"FloodWaitError"**: تيليجرام طلب انتظار — البوت يتعامل معها تلقائياً

**"Chat not found"**: تأكد من كتابة `@username` صح، أو استخدم رقم الـ ID
