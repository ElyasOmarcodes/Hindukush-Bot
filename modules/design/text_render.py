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


# تر لومړي (لوی) ځایېدونکي سایز څو پوائنټه ښکته وګورو چې پاک-جاسټیفای سایز ومومو.
# ډیر مه ښکته کوه — کوچنی فونټ پخپله یو نقص دی.
_RAGGED_SHRINK = 12
# د لاین د پاتې تشې اعظمي حد (د باکس د عرض برخه) — تر دې زیاته تشه نه منو.
_GAP_TOL = 0.02
# د wrap پر مهال د خامې (د کشیدې مخکې) تشې غوره سقف — DP یې ساتي که وسه یې وي.
_RAW_GAP_CAP = 0.22
# د سایز د ټاکلو پر مهال مطلق سقف: لاین باید تر کشیدې مخکې لږ تر لږه ۵۵٪ ډک وي.
# (بې دې، لوی سایز به یو لاین ته یوازې یوه کلمه پریږدي او کشیده به یې نیمایي ډکه
#  کړي — «افـــغـــانـــســـتـــان» — چې د کشیدې د کم کارولو قانون ماتوي.)
_RAW_GAP_MAX = 0.45


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


def fit_to_box(text: str, font_path: str, box_w: int, box_h: int, gap: int,
               target: int, floor: int, is_english: bool, headroom: int = 0):
    """ټول متن د باکس دننه ځای کړه (بې کلیپ) — **تر ټولو لوی** سایز چې تشه پرې نږدي.

    ⚠️ پخوا مو له ځایېدونکو سایزونو څخه هغه اخیست چې پاتې تشه یې تر ټولو لږه وه.
    خو کشیده د فونټ سره لویږي، نو «تر ټولو لږه تشه» تل **تر ټولو کوچنی فونټ** و —
    د ادارې ملګري په حق شکایت کاوه چې متن ډیر کوچنی او غیرمسلکي ښکاري.

    اوس ترتیب سم دی:
      ۱. `kashida_wrap` هر سایز ته داسې مات‌ځایونه ورکوي چې تشه یې ډکېدونکې وي،
      ۲. له هغو سایزونو چې **ځایېږي او تشه یې تر ۲٪ کمه ده**، تر ټولو لوی اخلو،
      ۳. که هیڅ یو پاک نه و، هغه اخلو چې تشه یې تر ټولو لږه ده (بیا لوی سایز).

    ``headroom`` لنډ متن ته اجازه ورکوي چې تر ډیفالټ سایز پورته شي (باکس ډک کړي).
    """
    hi = target + max(0, headroom)
    fitting: list[tuple[int, list[str]]] = []
    biggest: int | None = None
    for size in range(hi, floor - 1, -1):
        lines = kashida_wrap(text, font_path, size, box_w, is_english)
        lh = line_height(get_font(font_path, size), gap)
        if len(lines) * lh <= box_h:
            if biggest is None:
                biggest = size          # لومړی (لوی) سایز چې ځایېږي
            fitting.append((size, lines))
        if biggest is not None and size <= biggest - _RAGGED_SHRINK:
            break                        # تر دې ښکته مه ځه — فونټ ډیر کوچنی کیږي
    if not fitting:
        return floor, wrap_text(text, font_path, floor, box_w, is_english)

    gaps = {
        size: worst_gap_after_kashida(lines, font_path, size, box_w, is_english)
        for size, lines in fitting
    }
    raw = {
        size: max((box_w - text_width(get_font(font_path, size), ln, is_english)
                   for ln in lines[:-1]), default=0.0)
        for size, lines in fitting
    }
    # پاک = (۱) د کشیدې وروسته تشه نشته، او (۲) کشیدې ته یې زیاته اړتیا نه ده.
    clean = [sl for sl in fitting
             if gaps[sl[0]] <= _GAP_TOL * box_w and raw[sl[0]] <= _RAW_GAP_MAX * box_w]
    if not clean:  # د کشیدې سقف نرم کړه — تشه نه‌شتون لومړیتوب لري
        clean = [sl for sl in fitting if gaps[sl[0]] <= _GAP_TOL * box_w]
    if clean:
        return max(clean, key=lambda sl: sl[0])   # تر ټولو لوی پاک سایز
    return min(fitting, key=lambda sl: (round(gaps[sl[0]]), -sl[0]))


def descending_wrap(text: str, font_path: str, size: int, box_w: int,
                    is_english: bool) -> list[str]:
    """داسې wrap چې **هیڅ لاین تر پاسني پراخ نشي** او تشه یې لږه وي.

    ⚠️ ساده (greedy) wrap یوازې دا ګوري چې کلمه ځایېږي که نه — نو کیدای شي دویم
    لاین تر لومړي **پراخ** شي (زموږ قانون ماتوي). دلته د ټولو ممکنو مات‌ځایونو
    منځ کې هغه غوره کوو چې لومړی **سرغړونې** (پراخېدل) کمې کړي، بیا تشه.

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
    # best[(i, j)] = (سرغړونې، تشه) د هغه پریفکس لپاره چې وروستی لاین یې words[i:j]
    best: dict[tuple[int, int], tuple[float, float]] = {}
    back: dict[tuple[int, int], tuple[int, int] | None] = {}
    for j in range(1, n + 1):
        for i in range(j):
            w = width(i, j)
            if w > box_w and i + 1 < j:
                continue                      # ډیر پراخ (یوه کلمه استثنا ده)
            # ⚠️ **وروستی لاین هم** حسابوو. که یې معاف کړو، DP به «۹۹٪ + ۵۹٪»
            # بشپړ وګڼي — حال دا چې «۷۹٪ + ۷۹٪» ورته منځنی لري خو ډیر ښکلی دی.
            ragged = (1 - min(w, box_w) / box_w) ** 2
            if i == 0:
                cand, prev = (0.0, ragged), None
            else:
                cand, prev = (INF, INF), None
                for k in range(i):
                    if (k, i) not in best:
                        continue
                    pv, pr = best[(k, i)]
                    # سرغړونه: پاسنی لاین تر ښکتني **لنډ** وي.
                    viol = pv + (1 if width(k, i) < w - 0.5 else 0)
                    score = (viol, pr + ragged)
                    if score < cand:
                        cand, prev = score, (k, i)
            if cand[0] < INF and (best.get((i, j), (INF, INF)) > cand):
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
    """هغه سایز وټاکه چې لاینونه یې **تر ټولو ډک** وي او قانون یې مات نکړي.

    ⚠️ پخوا مو له ځایېدونکو سایزونو څخه هغه اخیست چې **ډیفالټ سایز ته نږدې** وي.
    خو دا د تش-فضا لامل کېده: یو عنوان په ۷۰ کې ۳ لاینه جوړوي چې ۶۰٪ ډک وي،
    حال دا چې په ۶۶ کې ۲ لاینه جوړوي چې ۸۷٪ ډک دي. کارونکی خو **ډکوالی** ویني.

    نو اوس ترتیب دا دی:
      ۱. سرغړونه نشته (هیڅ ښکتنی لاین تر پاسني پراخ نه دی)،
      ۲. لاینونه تر ټولو ډک (د ټولو لاینونو منځنۍ تشه — د وروستي په ګډون)،
      ۳. بیا ډیفالټ سایز ته نږدې.
    """
    _, _, box_w, box_h = box
    lo, target = dcfg.title_normal_min, dcfg.title_target_size
    # پورتنی حد: لنډ عنوان ته اجازه ورکوو چې لوی شي ترڅو باکس ډک کړي.
    hi = dcfg.title_normal_max + dcfg.title_fill_headroom

    fitting = []
    for size in range(hi, lo - 1, -1):
        lines = descending_wrap(text, font_path, size, box_w, is_english)
        lh = line_height(get_font(font_path, size), dcfg.title_line_gap)
        if len(lines) * lh <= box_h:
            fitting.append((size, lines))
    if fitting:
        return min(fitting, key=lambda sl: _balance_score(
            sl[1], sl[0], target, font_path, is_english, box_w))

    # نږدې-ډیفالټ رینج کې ونه ځایېد → تر abs_min پورې کوچنی کوه (شرینک-ټو-فټ).
    abs_min = min(dcfg.title_abs_min, lo)
    for size in range(lo - 1, abs_min - 1, -1):
        lines = descending_wrap(text, font_path, size, box_w, is_english)
        lh = line_height(get_font(font_path, size), dcfg.title_line_gap)
        if len(lines) * lh <= box_h:
            logger.info("عنوان اوږد و — سایز تر %dpx ښکته شو چې ټول ځای شي", size)
            return size, lines
    logger.warning("عنوان ډیر اوږد و — د %dpx مطلق کوچني سایز سره", abs_min)
    return abs_min, wrap_text(text, font_path, abs_min, box_w, is_english)


def _balance_score(lines: list[str], size: int, target: int, font_path: str,
                   is_english: bool, box_w: int) -> tuple:
    """د لاینونو کیفیت — کوچنی = ښه.  (سرغړونې، تشه، له ډیفالټ واټن)

    ⚠️ **تشه د ټولو لاینونو** (د وروستي په ګډون) اندازه کیږي. که یوازې نا-وروستي
    وشمېرو، «یو ډک لاین + یو لنډ» به بشپړ ښکاره شي — حال دا چې کارونکی لنډ
    لاین ویني.
    """
    font = get_font(font_path, size)
    widths = [text_width(font, ln, is_english) for ln in lines]
    violations = sum(1 for i in range(len(widths) - 1) if widths[i] < widths[i + 1] - 0.5)
    gaps = [(1 - min(w, box_w) / box_w) ** 2 for w in widths] if box_w else [0.0]
    return (violations, round(sum(gaps) / len(gaps), 2), abs(size - target))


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
