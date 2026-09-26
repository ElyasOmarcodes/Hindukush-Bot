"""د ژبو او موقعیت جوړښتي تنظیمات (باکسونه، فایل نومونه).

Structural layout: per-language templates + text boxes, and the location
file map. These are geometry, not user taste, so they live in code (numbers
that a user tunes for look live in DesignConfig instead).
"""

from __future__ import annotations

# باکس = (left, top, width, height) — تل left/top/width/height (نه right/bottom).
# د پښتو + فارسي باکسونه یو شان دي.
# د منبع باکس ~۲۰px پورته خوا ته نقل شو (top: 980 → 960).
_TITLE_BOX_RTL = (202, 1086, 1176, 421)
_SOURCE_BOX_RTL = (439, 960, 817, 104)
_TITLE_BOX_EN = (267, 1086, 1218, 421)
_SOURCE_BOX_EN = (380, 960, 864, 104)

# درې ژبې — پښتو/فارسي یو ټمپلیټ او یو باکس شریکوي.
LANGS = [
    {
        "code": "ps",
        "template": "news_pa.png",
        "title_key": "title_ps",
        "source_key": "source_ps",
        "is_english": False,
        "title_box": _TITLE_BOX_RTL,
        "source_box": _SOURCE_BOX_RTL,
    },
    {
        "code": "fa",
        "template": "news_fa.png",
        "title_key": "title_fa",
        "source_key": "source_fa",
        "is_english": False,
        "title_box": _TITLE_BOX_RTL,
        "source_box": _SOURCE_BOX_RTL,
    },
    {
        "code": "en",
        "template": "news_en.png",
        "title_key": "title_en",
        "source_key": "source_en",
        "is_english": True,
        "title_box": _TITLE_BOX_EN,
        "source_box": _SOURCE_BOX_EN,
    },
]

# د جیمنای د موقعیت ارزښت → د هرې ژبې فایل (نسبي مسیر).
# ⚠️ د پښتو فولډر کې "Pa Regin.png" دی (ټایپي)، نه "Pa Region.png".
LOCATION_MAP = {
    "en": {
        "Afghanistan": "En Location/En Afg.png",
        "Region": "En Location/En Region.png",
        "Middle East": "En Location/En Middle East.png",
        "World": "En Location/En World.png",
    },
    "ps": {
        "Afghanistan": "Pa Location/Pa Afg.png",
        "Region": "Pa Location/Pa Regin.png",
        "Middle East": "Pa Location/Pa Middle East.png",
        "World": "Pa Location/Pa World.png",
    },
    "fa": {
        "Afghanistan": "Fa Location/Fa Afg.png",
        "Region": "Fa Location/Fa Region.png",
        "Middle East": "Fa Location/Fa Middle East.png",
        "World": "Fa Location/Fa World.png",
    },
}

# د جیمنای د موقعیت مجاز ارزښتونه؛ نامعلوم → World.
VALID_LOCATIONS = ("Afghanistan", "Region", "Middle East", "World")
DEFAULT_LOCATION = "World"

# ─────────────── د عاجل خبر جوړښت (1561×1561، یوازې عنوان + تاریخ) ───────────────
URGENT_TEMPLATES = {
    "ps": "Urgent_Pashto.png",
    "fa": "Urgent_Persian.png",
    "en": "Urgent_English.png",
}
# باکس = (left, top, width, height)
URGENT_TITLE_BOX = (163, 584, 1223, 390)
URGENT_DATE_BOX = (267, 1241, 410, 46)

# ─────────────── د نقل قول جوړښت (1353×1353) ───────────────
QUOTE_TEMPLATES = {
    "ps": "Quotation_Pashto.png",
    "fa": "Quotation_Persian.png",
    "en": "Quotation_English.png",
}
# د شخص انځور — ښي-پورته، تر ټولو لاندې لایر (ټمپلیټ یې پر مخ راځي).
QUOTE_PERSON_BOX = (677, 0, 686, 1175)
# باکس = (left, top, width, height) — ټول د ټمپلیټ پر مخ.
QUOTE_TITLE_BOX = (62, 223, 554, 687)
# د نوم باکس ۱۵px ښکته شو (947→962) چې رتبې ته نږدې شي — لکه یو متن، دوه لاینه.
QUOTE_NAME_BOX = (62, 962, 554, 57)
# رتبه **پورته-تنظیم** ده (نه عمودي-مرکز) چې د نوم سره یې واټن ثابت پاتې شي او
# دوه-لاینه رتبه ښکته خواته وغزیږي، نه نوم ته پورته. پخوا ۲۸px تشه وه → اوس ~۱۴px.
QUOTE_RANK_BOX = (62, 1011, 554, 101)
