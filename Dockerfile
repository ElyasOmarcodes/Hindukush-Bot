# د Hindukush-Bot د چلولو انځور (Railway / هر Docker کوربه).
#
# ⚠️ ولې Dockerfile؟  — د libraqm/fribidi کیسه
# زموږ ټول د پښتو/فارسي (RTL) شکل‌ورکول د Pillow پر **libraqm** ولاړ دي —
# `direction="rtl"` پرته له راقم کار نه کوي.
#
# د Pillow رسمي manylinux wheelونه راقم او harfbuzz **دننه** لري (vendored)،
# خو **fribidi نه**. راقم fribidi د چلولو پر مهال په `dlopen("libfribidi.so.0")`
# لټوي — یعنې دا کتابتون باید **د سیسټم** پر انځور موجود وي.
#   • زموږ پر کمپیوټر `libfribidi0` نصب دی → راقم کار کوي.
#   • د Railway اتومات انځور او `python:*-slim` کې fribidi **نشته** → راقم
#     ناکاره کیږي او هر ډیزاین ماتیږي:
#       KeyError: setting text direction ... is not supported without libraqm
#
# نو یوازینی اړین ګام: **libfribidi0 نصب کړه**. (راقم او harfbuzz دمخه د wheel
# دننه دي — جلا نصب ته اړتیا نشته.)
#
# Pillow's manylinux wheels vendor raqm + harfbuzz but dlopen libfribidi.so.0
# at runtime, so the system must provide it. Slim images don't — hence the
# "not supported without libraqm" failures. Installing libfribidi0 fixes it.

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ⚠️ دا کرښه مه لرې کوه — پرته له دې راقم ناکاره او ټول ډیزاینونه ماتیږي.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libfribidi0 tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
# Pillow یوازې له wheel (که wheel ونه موندل شي، pip خطا ورکوي — نه دا چې چوپ
# له سرچینې جوړ کړي)، بیا د راقم چک — چې ماته-انځور هیڅکله deploy نشي.
RUN pip install --upgrade pip \
    && pip install --only-binary=Pillow -r requirements.txt \
    && python -c "import sys; from PIL import features; ok = features.check('raqm'); print('raqm:', features.version('raqm') if ok else 'MISSING'); sys.exit(0 if ok else 1)"

COPY . .

CMD ["python", "-u", "main.py"]
