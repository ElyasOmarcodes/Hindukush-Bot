# NewsBot Telegram — فاز ۱

یو د ټلګرام خبري رباټ چې په یوه ګروپ کې (چې رباټ پکې **اډمین** دی) پیغامونه
څاري. کاروونکی یو **پکیج** لیږي (۱ انځور + تر ۳ متني راپورونه: پښتو/فارسي/
انګلیسي)، بیا `/cmp` دستور ورکوي. رباټ پکیج [Google Gemini](https://ai.google.dev/)
ته لیږي، سرلیک/سرچینه راباسي، او پایله (متن + JSON فایل) بیرته ګروپ ته لیږي.

A Telegram news bot that watches a group, buffers one image + up to three text
reports, and on `/cmp` sends them to Gemini to extract verbatim titles/sources,
then returns the result as text and a JSON file.

---

## د فایلونو جوړښت / Structure

```
Hindukush-Bot/
├── main.py                 # د پیل نقطه، handlers ثبتول
├── run.bat                 # Windows چلونکی
├── requirements.txt
├── test_bot.py             # یونټ-ټیسټونه (بې شبکې)
├── .env.example            # د کیلیانو بیلګه (کاپي یې کړه → .env)
├── config/
│   └── settings.json       # جوړښتي تنظیمات (بې پټو معلوماتو)
├── modules/
│   ├── config.py           # تنظیمات + د .env پټ معلومات
│   ├── monitor.py          # څارنه + بفر + ری‌اکشن + البوم
│   ├── commands.py         # /cmp /cnl /clean /status /help + pipeline
│   ├── package.py          # د پکیج جوړول/ساتل/اعتبار
│   ├── gemini.py           # د Gemini تحلیل + cascade fallback
│   └── utils.py            # فایل نوم، لاګ، Markdown escape
└── data/                   # د پکیجونو فولډرونه (git ته نه ځي)
```

---

## تنظیمول / Setup

### ۱. کیلیانې (پټ معلومات)

ټول پټ معلومات د **چاپیریال متغیرونو** (`.env`) څخه لوستل کیږي — **نه** په
کوډ کې. `.env` د `.gitignore` کې دی نو عامه نه کیږي.

```bash
cp .env.example .env
```

بیا `.env` کې ډک کړه:

| متغیر | تشریح |
|-------|-------|
| `TELEGRAM_BOT_TOKEN` | له [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_GROUP_ID` | د ګروپ ID (منفي عدد، لکه `-1001234567890`) |
| `ADMIN_USER_IDS` | د اډمینانو ID، په کامې جلا (اختیاري — خالي = هرڅوک) |
| `GEMINI_API_KEYS` | د Gemini کیلیانې، په کامې جلا |

### ۲. ⚠️ د BotFather دوه اړین تنظیمونه

**الف) د privacy mode بندول** (له دې پرته رباټ د ګروپ عادي پیغامونه **نشي لیدلی**):
```
/setprivacy → خپل رباټ وټاکه → Disable
```

**ب) د دستورونو مینو** (رباټ دا پخپله هم ثبتوي، خو لاسي هم کولی شې):
```
/setcommands
cmp - پکیج بشپړ او پروسس کړه
cnl - روان عملیات لغوه کړه
clean - بفر پاک کړه
status - اوسنی حالت
help - مرسته
```

### ۳. د ګروپ ID موندل

رباټ ګروپ ته اضافه کړه او اډمین یې کړه. که `TELEGRAM_GROUP_ID` تش پریږدې،
رباټ په **dev حالت** کې ټول چټونه څاري او د هر پیغام ID لاګ کوي — له هغه
ځایه د ګروپ (منفي) ID واخله او په `.env` کې یې کېږده.

---

## چلول / Run

### محلي (Windows/Linux)

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux

pip install -r requirements.txt
python main.py                 # یا په Windows: run.bat
```

### ټیسټونه

```bash
pip install -r requirements.txt pytest
pytest -v
```

### GitHub Actions

د `Hindukush-Bot CI & Run` workflow:

1. **test** — پکجونه نصلوي او `pytest` اجرا کوي (په هر push).
2. **run-bot** — رباټ په polling حالت کې د GitHub Actions تر ممکنه وخته
   (~۶ ساعته) روان ساتي، له واضح لاګونو سره.

**اړین secrets** (Settings → Secrets and variables → Actions):
`TELEGRAM_BOT_TOKEN` (اړین)، `GEMINI_API_KEYS`، `TELEGRAM_GROUP_ID`،
`ADMIN_USER_IDS` (اختیاري).

> ⚠️ **یادونه:** GitHub Actions د دایمي ۲۴/۷ رباټ لپاره نه دی — هره دنده تر
> ۶ ساعتو محدوده ده. د دایمي چلولو لپاره یو سرور/VPS یا Railway/Render وکاروه.

---

## د کارونې جریان / Usage flow

1. په ګروپ کې ۱ انځور + تر ۳ متني راپورونه ولیږه → هر یو ری‌اکشن اخلي (1️⃣2️⃣3️⃣4️⃣).
2. `/cmp` ولیکه → د خلاصې سره شیشه‌یي «✅ تایید / ❌ لغوه» بټن راځي.
3. «تایید» کلیک کړه → پکیج ساتل کیږي، Gemini تحلیل کوي، پایله (متن + `result.json`) راځي.
4. رباټ پخپله reset کیږي — نوی پکیج ولیږه.

نور دستورونه: `/status` (اوسنی بفر)، `/clean` (بفر پاک)، `/cnl` (لغوه)، `/help`.

---

## د دستورونو لنډیز / Commands

| دستور | کار |
|-------|-----|
| `/cmp` | پکیج بشپړ کړه، تایید وغواړه، بیا پروسس کړه |
| `/cnl` | روان عملیات لغوه + بفر پاک |
| `/clean` | بفر پاک (بې لغوې) |
| `/status` | اوسنی بفر وښیه |
| `/help` | د دستورونو لیست |
