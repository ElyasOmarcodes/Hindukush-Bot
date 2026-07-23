# Hindukush-Bot

یو ساده Python Telegram بوټ چې `/start` کمانډ او عادي پیغامونو ته ځواب ورکوي.

A simple Python Telegram bot that replies to the `/start` command and to
normal text messages.

## Files / فایلونه

- `bot.py` — د بوټ اصلي کوډ (the bot itself)
- `requirements.txt` — د پایتون پکجونه (dependencies)
- `test_bot.py` — ساده ټیسټونه (tests)
- `.github/workflows/bot.yml` — GitHub Actions workflow (test + run)

## Setup / تنظیمول

بوټ ټوکن د `TELEGRAM_BOT_TOKEN` چاپیریال متغیر څخه لوستل کیږي. ټوکن هیڅکله
په کوډ کې نه ساتل کیږي.

The bot reads its token from the `TELEGRAM_BOT_TOKEN` environment variable.
The token is **never** committed to the repository.

### 1. Add the token as a GitHub Actions secret

Repository → **Settings** → **Secrets and variables** → **Actions** →
**New repository secret**:

- Name: `TELEGRAM_BOT_TOKEN`
- Value: your bot token from [@BotFather](https://t.me/BotFather)

> ⚠️ که تاسو ټوکن په عامه ډول شریک کړی وي (لکه د دې غوښتنې په متن کې)،
> نو مهرباني وکړئ په BotFather کې یې بیا جوړ (revoke/regenerate) کړئ.
> If a token was ever shared publicly, revoke and regenerate it in BotFather.

### 2. Run locally / محلي اجرا

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="your-token-here"
python bot.py
```

### 3. Run tests / ټیسټونه

```bash
pip install -r requirements.txt pytest
pytest -v
```

## GitHub Actions

د `Hindukush-Bot CI & Run` workflow دوه دندې لري:

The `Hindukush-Bot CI & Run` workflow has two jobs:

1. **test** — پکجونه نصلوي او `pytest` اجرا کوي.
   Installs dependencies and runs `pytest`.
2. **run-bot** — بوټ په polling حالت کې اجرا کوي او د GitHub Actions تر
   ممکنه وخته (نږدې ۶ ساعته) یې روان ساتي، له واضح لاګونو سره.
   Runs the bot in polling mode and keeps it alive for as long as
   GitHub Actions allows (~6 hours), with clear logs.

تاسو کولی شئ workflow په لاسي ډول هم اجرا کړئ: **Actions** ټب → **Hindukush-Bot
CI & Run** → **Run workflow**.
You can also trigger it manually from the **Actions** tab.
