"""د متن رسمول — د Pillow اصلي raqm shaping (RTL)، wrap، اتومات سایز، هایلایټ.

Text rendering. The provided FModam font ships no Arabic presentation forms,
so classic reshaper output would be tofu. Pillow's bundled raqm/harfbuzz does
real OpenType shaping instead — we pass base Unicode with ``direction="rtl"``
and the font joins letters natively. English is drawn left-to-right.
"""

from __future__ import annotations

import logging

from PIL import ImageDraw, ImageFont

from ..config import DesignConfig

logger = logging.getLogger("design.text")

_font_cache: dict = {}

# ⚠️ زموږ ټول شکل‌ورکول (RTL) پر Pillow-libraqm ولاړ دي. که راقم نه وي، Pillow
# د `direction=` لپاره یوه ګونګه `KeyError` غورځوي او هر ډیزاین ماتیږي.
_RAQM_HINT = (
    "د Pillow راقم ناکاره دی — د پښتو/فارسي شکل‌ورکول (RTL) کار نه کوي.\n"
    "  لامل: راقم او harfbuzz د Pillow د wheel دننه دي، خو **fribidi** نه —\n"
    "  راقم یې د چلولو پر مهال په dlopen(\"libfribidi.so.0\") لټوي، نو باید د\n"
    "  سیسټم پر انځور موجود وي. سپک (slim) انځورونه یې نه لري.\n"
    "  حل: libfribidi0 نصب کړه  →  apt-get install -y libfribidi0\n"
    "       (یا د مخزن Dockerfile وکاروه — دمخه یې لري).\n"
    "  Pillow vendors raqm+harfbuzz but dlopens libfribidi.so.0 at runtime; "
    "install libfribidi0."
)


def has_raqm() -> bool:
    """آیا Pillow له libraqm سره جوړ شوی؟  Whether Pillow has libraqm."""
    try:
        from PIL import features

        return bool(features.check("raqm"))
    except Exception:  # noqa: BLE001
        return False


def ensure_raqm() -> None:
    """که راقم نشته، په پیل کې یې څرګنده کړه (نه د هر ډیزاین پر مهال ګونګه خطا)."""
    if not has_raqm():
        logger.critical("%s", _RAQM_HINT)


def _direction(is_english: bool) -> str:
    return "ltr" if is_english else "rtl"


def parse_color(hx: str) -> tuple[int, int, int]:
    """د رنګ hex پارس کړه — د ARGB (۸ رقمه) ملاتړ سره.

    Accept ``RRGGBB`` or ``AARRGGBB`` (leading alpha dropped) with/without ``#``.
    """
    hx = hx.lstrip("#").strip()
    if len(hx) == 8:  # ARGB → لومړی ۲ رقمه (alpha) پرې کړه
        hx = hx[2:]
    return tuple(int(hx[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def get_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(path, size)
    return _font_cache[key]


def text_width(font: ImageFont.FreeTypeFont, text: str, is_english: bool) -> float:
    """د shaped متن عرض (raqm)."""
    if not text:
        return 0.0
    try:
        return font.getlength(text, direction=_direction(is_english))
    except KeyError as err:  # راقم نشته → ګونګه KeyError؛ څرګنده پیغام ورکړه
        raise RuntimeError(_RAQM_HINT) from err


def line_height(font: ImageFont.FreeTypeFont, gap: int) -> int:
    """د لاین لوړوالی = د فونټ طبیعي لوړوالی (ascent+descent) + فاصله (px)."""
    ascent, descent = font.getmetrics()
    return ascent + descent + gap


def wrap_text(text: str, font_path: str, size: int, max_w: int, is_english: bool) -> list[str]:
    """متن د باکس عرض ته wrap کړه (منطقي کرښې راګرځوي).

    Wrap to the box width using shaped measurement. Lines stay in logical
    order; raqm handles shaping and bidi at draw time.
    """
    font = get_font(font_path, size)
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        cur: list[str] = []
        for w in words:
            test = cur + [w]
            if text_width(font, " ".join(test), is_english) <= max_w or not cur:
                cur = test
            else:
                lines.append(" ".join(cur))
                cur = [w]
        if cur:
            lines.append(" ".join(cur))
    return lines or [""]


# د ډیفالټ سایز څخه څومره ښکته لټون کوو. مقصد **ډیفالټ یا ورته نږدې** سایز دی —
# نه ډیر کوچنی (غیرمسلکي) او نه ډیر لوی. که په دې بند کې هیڅ ونه ځایېږي، بیا
# جلا (shrink-to-fit) پړاو تر مطلق کوچني حد پورې ښکته کوي.
_TARGET_SHRINK = 20
# د لاین د پاتې تشې اعظمي حد (د باکس د عرض برخه) — تر دې زیاته تشه نه منو.
_GAP_TOL = 0.02
# ⚠️ خو یو ثابت سلنه کافي نه ده. کشیده یو **بشپړ توری** دی او له فونټ سره لویږي:
#   ۴۵px → ۲.۵٪ د باکس، ۵۸px → ۳.۳٪، ۶۵px → ۳.۶٪.
# نو پاتې تشه تل «تر یوې کشیدې کمه» وي — دا د جاسټیفای ریاضي کچه ده. که په
# ۵۸px کې «تر ۲٪ کمه» وغواړو، دا یعنې «تر یوې کشیدې ښه» — یوازې په بخت سره
# ممکنه. پایله دا شوه چې الګوریتم به تل هغه **کوچني** سایز ته ټوپ وهه چې تشه
# یې تصادفاً د کشیدې پر مضرب لوېده — او د ادارې ملګري یې په حق شکایت کاوه چې
# «د نقل قول متن تل تر معمول کوچنی وي».
# ځکه حد اوس **د کشیدې په تناسب** هم بیانیږي: پاتې تشه باید تر درې-څلورمې
# کشیدې کمه وي — یعنې «کشیدې خپل کار تر پایه کړی دی».
_GAP_KASHIDA_FRACTION = 0.75
# د wrap پر مهال د خامې (د کشیدې مخکې) تشې غوره سقف — DP یې ساتي که وسه یې وي.
_RAW_GAP_CAP = 0.22
# د سایز د ټاکلو پر مهال مطلق سقف: لاین باید تر کشیدې مخکې لږ تر لږه ۵۵٪ ډک وي.
# (بې دې، لوی سایز به یو لاین ته یوازې یوه کلمه پریږدي او کشیده به یې نیمایي ډکه
#  کړي — «افـــغـــانـــســـتـــان» — چې د کشیدې د کم کارولو قانون ماتوي.)
_RAW_GAP_MAX = 0.45
# ⚠️ د خبر عنوان تر ټولو مهم قانون — **د ادارې له ۶ لاسي نمونو څخه اندازه شوی**:
#   نمونه ۱: ۶۹px، ۲ لاینه، ډکوالی ۹۷/۱۰۰
#   نمونه ۲: ۷۸px، ۳ لاینه، ۹۹/۹۹/۹
#   نمونه ۳: ۸۴px، ۳ لاینه، ۹۳/۹۸/۳۰
#   نمونه ۴: ۸۰px، ۲ لاینه، ۹۹/۹۵
#   نمونه ۵: ۵۴px، ۳ لاینه، ۱۰۰/۱۰۰/۸۶
#   نمونه ۶: ۷۳px، ۳ لاینه، ۹۰/۹۰/۹۹
# هر **نا-وروستی** لاین تل ≥۹۰٪ ډک دی — بې استثنا. وروستی لاین هیڅ حد نلري
# (۹٪ هم ښه دی). او سایز ۵۴–۸۴ خوځېږي، نو «ثابت ډیفالټ» هلته قانون نه دی.
_MIN_LINE_FILL = 0.90
# د «ښکتنی لاین تر پاسني پراخ نه دی» قانون زغم.
# ⚠️ لاسي نمونې پخپله ۹۷→۱۰۰، ۹۳→۹۸ او ۹۰→۹۹ لري، نو کلک قانون یې منع کاوه.
# او په حقیقت کې د ۹۰٪ قانون دا ستونزه **پخپله** حلوي: که هر نا-وروستی لاین
# ≥۹۰٪ وي، نو تر ټولو لوی توپیر یوازې ۱۰٪ کیدای شي. هغه پخوانۍ بده بېلګه
# («۷۲٪ بیا ۸۷٪») پخپله د ۹۰٪ قانون له امله ردیږي. نو دا زغم یوازې د هغو
# عنوانونو لپاره دی چې ۹۰٪ قانون یې پوره نشي کیدای.
_DESC_TOL = 0.10


def raggedness(lines: list[str], font_path: str, size: int, box_w: int,
               is_english: bool) -> float:
    """د نا-وروستیو لاینونو منځنۍ پاتې تشه (۰ = بشپړ ډک) — **د کشیدې مخکې**.

    مربع کاروو نو یوه **لویه** تشه تر څو وړو زیاته جریمه شي.
    """
    if len(lines) < 2 or not box_w:
        return 0.0
    font = get_font(font_path, size)
    gaps = [max(0.0, 1.0 - text_width(font, ln, is_english) / box_w) for ln in lines[:-1]]
    return sum(g * g for g in gaps) / len(gaps)


def worst_gap_after_kashida(lines: list[str], font_path: str, size: int,
                            box_w: int, is_english: bool) -> float:
    """د کشیدې **وروسته** تر ټولو لویه پاتې تشه (px) — دا هغه څه دي چې سترګه یې ویني.

    ⚠️ ولې د کشیدې مخکینۍ تشه بس نه ده: کشیده یو بشپړ توری دی (~۱۵px)، نو
    پاتې تشه تقریباً «تشه ÷ د کشیدې پراخي» پاتې برخه ده — یعنې د سایز سره یې
    اړیکه نامنظمه ده. ځکه سایز باید د **ریښتیني** پاتې تشې پر بنسټ وټاکو، نه
    د خامې تشې. د justify ورته seed کاروو چې اندازه او رسم یو شان وي.
    """
    if is_english or len(lines) < 2:
        return 0.0
    from .justify import justify_line

    font = get_font(font_path, size)
    worst = 0.0
    for i, line in enumerate(lines[:-1]):
        filled = justify_line(line, lambda t: text_width(font, t, is_english), box_w, seed=i)
        worst = max(worst, box_w - text_width(font, filled, is_english))
    return worst


def kashida_width(font: ImageFont.FreeTypeFont) -> float:
    """د یوې کشیدې پراخي (px) — «بـب» منفي «بب».

    ⚠️ دا شمېره د جاسټیفای **دانه‌والی** (granularity) دی: کشیده یو بشپړ توری دی،
    نو د یوه لاین پاتې تشه تل تر یوې کشیدې کمه وي — لږه یې نشو کولی. او ځکه چې
    د فونټ له سایز سره لویږي، لوی فونټ = درشته دانه. نو د wrap پر مهال باید
    داسې مات‌ځایونه وټاکو چې خامه تشه یې **د کشیدې پر مضرب** ودریږي.
    """
    return font.getlength("بـب") - font.getlength("بب")


def kashida_wrap(text: str, font_path: str, size: int, box_w: int,
                 is_english: bool) -> list[str]:
    """داسې wrap چې د هر لاین خامه تشه **د کشیدې پر مضرب** نږدې ودریږي.

    ⚠️ ساده wrap یوازې دا ګوري چې کلمه ځایېږي که نه. خو د کشیدې دانه‌والی
    (~۲۰px په ۶۵ سایز کې) پدې مانا ده چې «۲۵px تشه» او «۴۰px تشه» یو شان نه دي:
    لومړۍ یوه کشیده اخلي او ۵px پاتې کوي، دویمه دوه کشیدې اخلي او ۰px پاتې کوي.
    نو DP د **پاتې تشې مربع** کموي، نه د خامې تشې.

    پایله: لوی فونټ هم کولی شي لاینونه بشپړ ډک کړي — پخوا مو د تشې د کمولو
    لپاره فونټ کوچنی کاوه (ځکه کوچنۍ کشیده = ښه دانه)، چې ډیزاین یې غیرمسلکي کاوه.
    """
    if is_english:
        return wrap_text(text, font_path, size, box_w, is_english)
    font = get_font(font_path, size)
    words = text.split()
    n = len(words)
    if n < 2:
        return words or [""]
    kw = kashida_width(font) or 1.0
    space = text_width(font, " ", is_english)
    prefix = [0.0]
    for w in words:
        prefix.append(prefix[-1] + text_width(font, w, is_english))

    def width(i: int, j: int) -> float:
        return prefix[j] - prefix[i] + space * (j - i - 1)

    INF = float("inf")
    heavy = box_w * box_w  # د «کشیده یې نشي ډکولی» لاین جریمه
    best = [INF] * (n + 1)
    back = [-1] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        for i in range(j):
            if best[i] == INF:
                continue
            w = width(i, j)
            if w > box_w and i + 1 < j:
                continue                       # ډیر پراخ (یوه کلمه استثنا ده)
            raw = max(0.0, box_w - w)
            if j == n:
                cost = 0.0                     # وروستی لاین justify نه کیږي
            else:
                left = raw - kw * int(raw // kw)   # د کشیدې وروسته پاتې تشه
                cost = left * left + (heavy if raw > _RAW_GAP_CAP * box_w else 0.0)
            if best[i] + cost < best[j]:
                best[j] = best[i] + cost
                back[j] = i
    if best[n] == INF:
        return wrap_text(text, font_path, size, box_w, is_english)
    out, j = [], n
    while j > 0:
        i = back[j]
        out.append(" ".join(words[i:j]))
        j = i
    return out[::-1]


def gap_tolerance(font_path: str, size: int, box_w: int) -> float:
    """د پاتې تشې د زغم حد (px) — د باکس سلنه **یا** د کشیدې برخه، کومه چې لویه.

    د کشیدې برخه ځکه ورزیاته شوه چې د جاسټیفای دانه له فونټ سره لویږي؛ پورته
    د `_GAP_KASHIDA_FRACTION` یادښت وګوره.
    """
    return max(_GAP_TOL * box_w,
               _GAP_KASHIDA_FRACTION * kashida_width(get_font(font_path, size)))


def _is_clean(lines: list[str], font_path: str, size: int, box_w: int,
              is_english: bool, strict: bool = True) -> bool:
    """آیا دا سایز «پاک» دی — نه تشه پریږدي او نه زیاتې کشیدې غواړي؟"""
    gap = worst_gap_after_kashida(lines, font_path, size, box_w, is_english)
    if gap > gap_tolerance(font_path, size, box_w):
        return False
    if not strict:
        return True
    font = get_font(font_path, size)
    raw = max((box_w - text_width(font, ln, is_english) for ln in lines[:-1]),
              default=0.0)
    return raw <= _RAW_GAP_MAX * box_w


def fit_to_box(text: str, font_path: str, box_w: int, box_h: int, gap: int,
               target: int, floor: int, is_english: bool, headroom: int = 0):
    """ټول متن د باکس دننه ځای کړه (بې کلیپ) — **ډیفالټ سایز یا ورته نږدې**.

    ⚠️ دوه تېروتنې مو ولیدې، دواړه اوس بندې دي:
      • «تر ټولو لږه تشه» → کشیده د فونټ سره لویږي، نو کوچنی فونټ تل ښه دانه لري
        او دا معیار **تل ترټولو کوچنی فونټ** غوره کاوه (۴۷–۵۹px — غیرمسلکي).
      • «تر ټولو لوی پاک سایز» → بل لور ته افراط و (۹۳px د لنډ نقل قول لپاره).

    سم قانون: **د ډیفالټ سایز ته تر ټولو نږدې** پاک سایز. پاک یعنې (۱) ټول متن
    ځایېږي، (۲) د کشیدې وروسته تشه تر ۲٪ کمه ده، (۳) کشیدې ته یې زیاته اړتیا نشته.

    ``headroom`` یوازې دومره اجازه ورکوي چې که پخپله ډیفالټ پاک نه و، لږ پورته
    هم وکتل شي — نه دا چې متن تر ډیفالټه ډیر لوی شي.
    """
    hi = target + max(0, headroom)
    lo = max(floor, target - _TARGET_SHRINK)

    fitting: list[tuple[int, list[str]]] = []
    above: tuple[int, list[str]] | None = None   # تر ډیفالټ پورته، نږدې پاک
    below: tuple[int, list[str]] | None = None   # تر ډیفالټ ښکته، نږدې پاک
    for size in range(hi, lo - 1, -1):
        lines = kashida_wrap(text, font_path, size, box_w, is_english)
        if len(lines) * line_height(get_font(font_path, size), gap) > box_h:
            continue
        fitting.append((size, lines))
        if not _is_clean(lines, font_path, size, box_w, is_english):
            continue
        if size >= target:
            above = (size, lines)   # ښکته راځو، نو وروستی = ډیفالټ ته نږدې
        else:
            below = (size, lines)   # لومړی پاک تر ډیفالټ ښکته = نږدې
            break

    best = _closest(above, below, target)
    if best is not None:
        return best

    # هیڅ پاک سایز نشته → د کشیدې سقف نرم کړه (تشه نه‌شتون لومړیتوب لري).
    soft = [sl for sl in fitting
            if _is_clean(sl[1], font_path, sl[0], box_w, is_english, strict=False)]
    if soft:
        return min(soft, key=lambda sl: (abs(sl[0] - target), -sl[0]))
    if fitting:  # هیڅ یو پاک نه و → تر ټولو لږه تشه، بیا ډیفالټ ته نږدې
        return min(fitting, key=lambda sl: (
            round(worst_gap_after_kashida(sl[1], font_path, sl[0], box_w, is_english)),
            abs(sl[0] - target)))

    # متن ډیر اوږد و — تر مطلق کوچني حد پورې ښکته کوو (هیڅکله کلیپ نه کوو).
    for size in range(lo - 1, floor - 1, -1):
        lines = kashida_wrap(text, font_path, size, box_w, is_english)
        if len(lines) * line_height(get_font(font_path, size), gap) <= box_h:
            logger.info("متن اوږد و — سایز تر %dpx ښکته شو چې ټول ځای شي", size)
            return size, lines
    return floor, wrap_text(text, font_path, floor, box_w, is_english)


def _closest(above, below, target: int):
    """له دوو نوماندانو هغه چې ډیفالټ ته نږدې دی؛ که برابر وو، لوی یې."""
    if above is None:
        return below
    if below is None:
        return above
    return above if above[0] - target <= target - below[0] else below


# ─────────── د ژبني مات‌ځای قواعد (د لاین پای/پیل) ───────────
# ⚠️ ستونزه: «... روغتون **د**» / «جوړېدو ...» — د اضافت «د» له خپل اسم بېلېږي او
# لاین «نیمایي کې» بندیږي. لاسي ډیزاین دا کار هیڅکله نه کوي. همدارنګه
# «... زېربناوو د **په**» / «نښه کولو ...» — «په نښه کول» یوه ټوټه ده.
#
# دا ټکي له **راتلونکي** ټکي سره تړلي دي، نو د لاین په پای کې یتیم نه پریږدو:
# ⚠️ یوازې **قوي** تړونکي (اضافت/حرف جر) — هغه چې بې خپل اسم معنا نلري.
# پیوندونه (و، او، یا) او اشارې (دا، هغه) پکې **نشته**: د ادارې خپله لاسي
# نمونه ۴ پخپله لاین په «و» پای ته رسوي («… درباره روابط و» | «همکاری‌های …»),
# نو که یې منع کړو، د لاسي نمونې خلاف ځو.
_TIE_NEXT = {
    # پښتو
    "د", "په", "له", "تر", "پر", "بې", "لکه", "پرته", "ترڅو",
    # فارسي/دري
    "از", "به", "با", "در", "بر", "تا", "برای", "روی", "بدون", "طبق",
    "بی", "طی", "علیه", "درباره",
}
# دا ټکي له **پخواني** ټکي سره تړلي دي، نو د لاین په سر کې یې نه پریږدو:
_TIE_PREV = {"یې", "مې", "مو", "هم", "را", "نیز", "است", "اند", "شوی", "شوې"}
_TIE_STRIP = "،؛:.!?»«\"'()[]—–-"


def _tie_next(word: str, is_english: bool) -> bool:
    """آیا دا ټکی له راتلونکي سره تړلی دی (نو د لاین په پای کې یې مه پریږده)؟

    ⚠️ **یوازې پښتو/دري.** د ادارې انګلیسي لاسي ډیزاین پخپله لاین په «to» پای ته
    رسوي («536 Returnee Families Return to» | «Afghanistan»)، نو انګلیسي ته دا
    قاعده نه پلي کوو — نه‌نو د لاسي نمونې خلاف ځو.
    """
    if is_english:
        return False
    return word.strip(_TIE_STRIP) in _TIE_NEXT


def _tie_prev(word: str, is_english: bool) -> bool:
    """آیا دا ټکی له پخواني سره تړلی دی (نو د لاین په سر کې یې مه پریږده)؟"""
    if is_english:
        return False
    return word.strip(_TIE_STRIP) in _TIE_PREV


def bad_breaks(lines: list[str], is_english: bool) -> int:
    """څو ځله یو تړلی ټکی له خپل جوړې څخه د لاین په مات‌ځای بېل شوی."""
    bad = 0
    for idx, line in enumerate(lines[:-1]):
        words = line.split()
        nxt = lines[idx + 1].split()
        if words and _tie_next(words[-1], is_english):
            bad += 1
        if nxt and _tie_prev(nxt[0], is_english):
            bad += 1
    return bad


def descending_wrap(text: str, font_path: str, size: int, box_w: int,
                    is_english: bool) -> list[str]:
    """داسې wrap چې **هیڅ لاین تر پاسني پراخ نشي** او پاسني لاینونه ډک وي.

    ⚠️ ساده (greedy) wrap یوازې دا ګوري چې کلمه ځایېږي که نه — نو کیدای شي دویم
    لاین تر لومړي **پراخ** شي (زموږ قانون ماتوي). دلته د ټولو ممکنو مات‌ځایونو
    منځ کې هغه غوره کوو چې لومړی **سرغړونې** (پراخېدل) کمې کړي، بیا تشه.

    ⚠️ **وروستی لاین نه شمېرو.** دا د لاسي ډیزاین اصل دی: پاسني لاینونه بشپړ
    ډکیږي او پاتې کلمې وروستي لاین ته ځي («۹۳٪ + ۲۱٪»). که وروستی لاین هم
    وشمېرو، DP لاینونه برابروي («۵۷٪ + ۵۷٪») — او بیا د ډکولو لپاره فونټ لوی
    کول پکار وي، چې متن غیرمسلکي لوی کړي (د ادارې شکایت).

    DP پر (د لاین پیل، پای) — د عنوان کلمې لږې دي، نو ارزانه ده.
    """
    font = get_font(font_path, size)
    words = text.split()
    n = len(words)
    if n < 2:
        return words or [""]
    space = text_width(font, " ", is_english)
    prefix = [0.0]
    for w in words:
        prefix.append(prefix[-1] + text_width(font, w, is_english))

    def width(i: int, j: int) -> float:
        return prefix[j] - prefix[i] + space * (j - i - 1)

    INF = float("inf")
    # د مات‌ځای ژبنۍ جریمه: که د لاین وروستی ټکی له راتلونکي سره تړلی وي، یا د
    # راتلونکي لاین لومړی ټکی له پخواني سره — «... روغتون د | جوړېدو ...».
    def tie_cost(j: int) -> int:
        if j >= n:
            return 0                          # وروستی لاین — مات‌ځای نشته
        return (1 if _tie_next(words[j - 1], is_english) else 0) \
            + (1 if _tie_prev(words[j], is_english) else 0)

    # best[(i, j)] = (سرغړونې، بد مات‌ځایونه، تشه) د هغه پریفکس چې وروستی لاین یې words[i:j]
    best: dict[tuple[int, int], tuple[float, float, float]] = {}
    back: dict[tuple[int, int], tuple[int, int] | None] = {}
    for j in range(1, n + 1):
        for i in range(j):
            w = width(i, j)
            if w > box_w and i + 1 < j:
                continue                      # ډیر پراخ (یوه کلمه استثنا ده)
            # وروستی لاین معاف دی — طبیعي توګه لنډ وي (پورته یادښت وګوره).
            ragged = 0.0 if j == n else (1 - min(w, box_w) / box_w) ** 2
            tie = tie_cost(j)
            if i == 0:
                cand, prev = (0.0, float(tie), ragged), None
            else:
                cand, prev = (INF, INF, INF), None
                for k in range(i):
                    if (k, i) not in best:
                        continue
                    pv, pt, pr = best[(k, i)]
                    # سرغړونه: پاسنی لاین تر ښکتني **د پام وړ** لنډ وي.
                    viol = pv + (1 if width(k, i) < w - _DESC_TOL * box_w else 0)
                    score = (viol, pt + tie, pr + ragged)
                    if score < cand:
                        cand, prev = score, (k, i)
            if cand[0] < INF and (best.get((i, j), (INF, INF, INF)) > cand):
                best[(i, j)] = cand
                back[(i, j)] = prev

    ends = [(v, key) for key, v in best.items() if key[1] == n]
    if not ends:
        return wrap_text(text, font_path, size, box_w, is_english)
    _, key = min(ends)
    out = []
    while key is not None:
        out.append(" ".join(words[key[0]:key[1]]))
        key = back[key]
    return out[::-1]


def find_best_size(text: str, font_path: str, box: tuple, is_english: bool, dcfg: DesignConfig):
    """د خبر عنوان: **تر ټولو لوی سایز چې نا-وروستي لاینونه یې ≥۹۰٪ ډک وي**.

    دا قانون د ادارې له ۶ لاسي نمونو څخه اندازه شوی (وګوره `_MIN_LINE_FILL`).
    په ټولو شپږو کې هر نا-وروستی لاین ۹۰–۱۰۰٪ ډک دی، خو سایز ۵۴ تر ۸۴ خوځېږي —
    نو **ډکوالی قانون دی، سایز یې پایله**. زموږ پخوانی «ډیفالټ ته نږدې» قانون
    همدا برعکس کاوه، ځکه:
      • نمونه ۱ کې رباټ ۷۵px/۳ لاینه [۹۹/۷۴/۴۰] جوړاوه، لاسي ۶۹px/۲ لاینه [۹۷/۱۰۰]
      • نمونه ۶ کې رباټ ۶۰px/۴ لاینه [۷۴/۶۹/۶۵/۲۱]، لاسي ۷۳px/۳ لاینه [۹۰/۹۰/۹۹]
    دواړه شکایتونه همدا وو: «ځینې وخت لاینونه ډیر لنډ وي» او «ځینې وخت سایز
    ډیر لوی وي، حال دا چې بل سایز هم قانوني او هم ښکلی و».

    ترتیب: ۱) سرغړونه، ۲) بد مات‌ځای، ۳) تش لاین (<۹۰٪)، بیا **تر ټولو لوی سایز**.
    که هیڅ سایز پاک نه و، د ډیفالټ شاوخوا ته ورګرځو (لنډ عنوان چې هیڅکله
    نه ډکېږي، لکه «زلزله»).
    """
    _, _, box_w, box_h = box
    target = dcfg.title_target_size
    hi = dcfg.title_max_size
    lo = max(dcfg.title_abs_min, dcfg.title_min_size)

    fitting = []
    for size in range(hi, lo - 1, -1):
        lines = descending_wrap(text, font_path, size, box_w, is_english)
        lh = line_height(get_font(font_path, size), dcfg.title_line_gap)
        if len(lines) * lh <= box_h:
            fitting.append((size, lines))

    if fitting:
        scored = [(_balance_score(l, sz, target, font_path, is_english, box_w), sz, l)
                  for sz, l in fitting]
        # پاک = نه سرغړونه، نه بد مات‌ځای، نه تش لاین. له هغو **لوی** سایز.
        clean = [(sz, l) for sc, sz, l in scored if sc[0] == sc[1] == sc[2] == 0]
        if clean:
            return max(clean, key=lambda sl: sl[0])
        # هیڅ یو پاک نه و → تر ټولو لږې سرغړونې، بیا ډیفالټ ته نږدې.
        best = min(scored, key=lambda x: (x[0][0], x[0][1], x[0][2],
                                          abs(x[1] - target), -x[1]))
        return best[1], best[2]

    # په بند کې هیڅ ونه ځایېد → تر abs_min پورې کوچنی کوه (شرینک-ټو-فټ).
    for size in range(lo - 1, dcfg.title_abs_min - 1, -1):
        lines = descending_wrap(text, font_path, size, box_w, is_english)
        lh = line_height(get_font(font_path, size), dcfg.title_line_gap)
        if len(lines) * lh <= box_h:
            logger.info("عنوان اوږد و — سایز تر %dpx ښکته شو چې ټول ځای شي", size)
            return size, lines
    logger.warning("عنوان ډیر اوږد و — د %dpx مطلق کوچني سایز سره", dcfg.title_abs_min)
    return dcfg.title_abs_min, wrap_text(text, font_path, dcfg.title_abs_min,
                                         box_w, is_english)


def _balance_score(lines: list[str], size: int, target: int, font_path: str,
                   is_english: bool, box_w: int) -> tuple:
    """د لاینونو کیفیت — کوچنی = ښه.

    ترتیب: (سرغړونې، بد مات‌ځایونه، تش لاینونه، تشه، له ډیفالټ واټن)

    ⚠️ **ډکوالی تر «له ډیفالټ واټن» مخکې دی — خو یوازې د [normal_min، normal_max]
    بند دننه.** دا توپیر مهم دی:
      • پخوا بند تر ۱۰۷px پورې خلاص و، نو «ډکوالی لومړی» متن غیرمسلکي لوی کاوه.
      • اوس بند ±۷ دی (۶۳–۷۷)، نو «ډکوالی لومړی» یوازې د لاسي ډیزاین بڼه راوړي:
        پاسني لاینونه بشپړ ډک، وروستی لاین لنډ.

    که ډکوالی د سایز پر بدلون سره ښه نشي، بیا **ډیفالټ ته نږدې** پرېکړه کوي.
    تشه یوازې د **نا-وروستیو** لاینونو ده — وروستی لاین طبیعي توګه لنډ وي
    («۹۹٪ + ۸۵٪ + ۲۰٪» لکه د ادارې لاسي ډیزاین).
    """
    font = get_font(font_path, size)
    widths = [text_width(font, ln, is_english) for ln in lines]
    # ⚠️ زغم سره — لاسي نمونې پخپله ۹۷→۱۰۰ او ۹۳→۹۸ لري (وګوره `_DESC_TOL`).
    violations = sum(1 for i in range(len(widths) - 1)
                     if widths[i] < widths[i + 1] - _DESC_TOL * box_w)
    head = widths[:-1] or widths
    fills = [min(w, box_w) / box_w for w in head] if box_w else [1.0]
    empties = sum(1 for f in fills if f < _MIN_LINE_FILL)
    gaps = [(1 - f) ** 2 for f in fills]
    return (violations, bad_breaks(lines, is_english), empties,
            round(sum(gaps) / len(gaps), 2), abs(size - target))


def draw_title(canvas, text: str, font_path: str, box: tuple, is_english: bool, dcfg: DesignConfig) -> None:
    """عنوان ورسموه — اتومات سایز + کلمه‌په‌کلمه هایلایټ (وروستۍ برخه نارنجي).

    RTL words are placed from the right edge; each word is shaped by raqm.
    """
    if not text.strip():
        return
    size, lines = find_best_size(text, font_path, box, is_english, dcfg)
    font = get_font(font_path, size)
    draw = ImageDraw.Draw(canvas)
    bx, by, bw, bh = box
    lh = line_height(font, dcfg.title_line_gap)
    direction = _direction(is_english)
    space_w = text_width(font, " ", is_english) or font.getlength(" ")

    highlight = parse_color(dcfg.highlight_color_hex)
    normal = parse_color(dcfg.normal_color_hex)
    total_words = sum(len(ln.split()) for ln in lines)
    start_idx = int(total_words * (1 - dcfg.highlight_fraction))

    if dcfg.title_valign == "middle":
        y = by + (bh - len(lines) * lh) // 2
    else:
        y = by

    gi = 0
    for line in lines:
        words = line.split()
        if is_english:
            x = bx
            for w in words:
                col = highlight if gi >= start_idx else normal
                draw.text((x, y), w, font=font, fill=col, direction=direction)
                x += text_width(font, w, is_english) + space_w
                gi += 1
        else:
            x = bx + bw  # د ښي څنډې څخه پیل (RTL)
            for w in words:
                col = highlight if gi >= start_idx else normal
                ww = text_width(font, w, is_english)
                x -= ww
                draw.text((x, y), w, font=font, fill=col, direction=direction)
                x -= space_w
                gi += 1
        y += lh


def fit_source(text: str, font_path: str, box: tuple, is_english: bool, dcfg: DesignConfig) -> ImageFont.FreeTypeFont:
    """د منبع لپاره سایز ومومه — تر یوې کرښې پورې، تر min ښکته کیدلی شي."""
    _, _, box_w, box_h = box
    for size in range(dcfg.source_size, dcfg.source_min_size - 1, -1):
        font = get_font(font_path, size)
        if text_width(font, text, is_english) <= box_w and size * 1.3 <= box_h:
            return font
    return get_font(font_path, dcfg.source_min_size)


def draw_source(canvas, text: str, font_path: str, box: tuple, is_english: bool, dcfg: DesignConfig) -> None:
    """منبع ورسموه — سپینه، یو لاین، عمودي مرکز؛ RTL=ښي-مرکز، LTR=کیڼ-مرکز.

    Anchored so the source sits exactly right-centre (RTL) or left-centre (LTR)
    inside its box.
    """
    if not text.strip():
        return
    font = fit_source(text, font_path, box, is_english, dcfg)
    draw = ImageDraw.Draw(canvas)
    bx, by, bw, bh = box
    color = parse_color(dcfg.source_color_hex)
    cy = by + bh // 2
    if is_english:
        draw.text((bx, cy), text, font=font, fill=color, direction="ltr", anchor="lm")
    else:
        draw.text((bx + bw, cy), text, font=font, fill=color, direction="rtl", anchor="rm")
