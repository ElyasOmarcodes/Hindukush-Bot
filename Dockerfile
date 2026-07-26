# د Hindukush-Bot د چلولو انځور (Railway / هر Docker کوربه).
#
# ⚠️ ولې Dockerfile؟
# زموږ ټول د پښتو/فارسي (RTL) شکل‌ورکول د Pillow پر **libraqm** ولاړ دي —
# `direction="rtl"` پرته له راقم کار نه کوي.
#
# د Pillow رسمي manylinux wheelونه راقم **په خپله دننه** لري (statically
# vendored — ځکه `pillow.libs/` کې جلا libraqm.so نه لیدل کیږي، خو
# `_imagingft` کې د راقم سمبولونه شته). خو کله چې جوړوونکی Pillow **له
# سرچینې (sdist)** جوړ کړي — لکه چې د Railway پر اتومات builder وشول — راقم
# یوازې هغه وخت شاملیږي چې د سیسټم libraqm-dev سرلیکونه موجود وي؛ نه‌نو ورک شي
# او هر ډیزاین ماتیږي:
#     KeyError: setting text direction ... is not supported without libraqm
#
# نو دلته دوه شیان ډاډمن کوو:
#   ۱. `--only-binary=Pillow` → pip باید wheel واخلي؛ که ونه موندل شي، **خطا**
#      ورکړي، نه دا چې په چوپه خوله له سرچینې جوړ کړي.
#   ۲. د جوړولو پر مهال د راقم چک → ماته-انځور هیڅکله deploy نشي.
#
# Official Pillow manylinux wheels vendor raqm statically; a source build drops
# it. Force the wheel and assert raqm at build time so a broken image can never
# ship.

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install --only-binary=Pillow -r requirements.txt \
    && python -c "import sys; from PIL import features; ok = features.check('raqm'); print('raqm:', features.version('raqm') if ok else 'MISSING'); sys.exit(0 if ok else 1)"

COPY . .

CMD ["python", "-u", "main.py"]
