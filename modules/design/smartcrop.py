"""ځیرک کرپ — د ۵:۴ تر ټولو لوی مستطیل چې اصلي سوژه پکې پاتې شي.

Content-aware cropping. The news template expects a 5:4 photo; anything else is
stretched (distorted) or centre-cropped (subject may be cut off). This module
finds the largest 5:4 window that keeps the most "important" content.

⚠️ یوازې Pillow کاروو — نه numpy، نه OpenCV. لامل یې دا دی چې دا کار پر
Railway کې د هر پکیج لپاره چلیږي، نو باید سپک او چټک وي. ټول حساب پر یوه
**کوچني** (~۱۸۰px) کاپي کیږي، نو د یوه انځور لپاره څو میلي-ثانیې نیسي.

ولې دا بس دی: د ۵:۴ تر ټولو لوی مستطیل **یوازې په یوه محور** ښویږي (که انځور
تر ۵:۴ پراخ وي، لوړوالی بشپړ دی او یوازې کیڼ/ښي ټاکل پاتې دي). نو لټون یو-
اړخیز دی — د یوه کمپیوټري لید مډل ته اړتیا نشته.
"""

from __future__ import annotations

import logging

from PIL import Image, ImageChops, ImageDraw, ImageFilter

logger = logging.getLogger("design.smartcrop")

# د تحلیل کوچنی اندازه (اوږد اړخ). کوچنی = چټک؛ ۱۸۰ د سوژې پیژندلو لپاره بس دی.
_ANALYSIS_LONG = 180
# د اهمیت وزنونه — پوستکی (خلک) تر ټولو مهم دی، بیا څنډې (تفصیل)، بیا رنګ.
_W_EDGE, _W_SAT, _W_SKIN = 0.9, 0.4, 1.6
# د مرکز ملایم تمایل — که دوه کړکۍ نږدې یو شان وي، منځنۍ غوره ده (عکاس
# معمولاً سوژه مرکز ته نږدې ږدي، او څنډې کې کرپ نا-طبیعي ښکاري).
_CENTER_BIAS = 0.18


def _threshold(layer: Image.Image, above: int) -> Image.Image:
    return layer.point(lambda v, t=above: 255 if v > t else 0)


def _skin_mask(rgb: Image.Image) -> Image.Image:
    """د پوستکي رنګ ماسک — د خلکو (مخونه/لاسونه) موندلو لپاره.

    کلاسیک RGB قانون:  R>95, G>40, B>20, max-min>15, R-G>15, R>G, R>B.

    ⚠️ ټول حساب د Pillow په **چینل-عملیاتو** کیږي، نه د پایتون لوپ کې. لوپ پر
    ~۲۵ زره پکسلو ~۲۵۰ms نیوه؛ دا نسخه یې څو میلي-ثانیو ته راکموي — او دا
    فنکشن د هر پکیج لپاره چلیږي.
    """
    r, g, b = rgb.split()
    hi = ImageChops.lighter(ImageChops.lighter(r, g), b)
    lo = ImageChops.darker(ImageChops.darker(r, g), b)
    parts = [
        _threshold(r, 95), _threshold(g, 40), _threshold(b, 20),
        _threshold(ImageChops.subtract(hi, lo), 15),   # رنګین دی، نه خړ
        _threshold(ImageChops.subtract(r, g), 15),     # R تر G ښه لوړ
        _threshold(ImageChops.subtract(r, b), 0),      # R تر B لوړ
    ]
    mask = parts[0]
    for part in parts[1:]:
        mask = ImageChops.multiply(mask, part)         # منطقي «او»
    # لږ خپور یې کړه چې د مخ شاوخوا (ویښتان/اوږې) هم راونیسي.
    return mask.filter(ImageFilter.MaxFilter(3))


def importance_map(img: Image.Image) -> Image.Image:
    """د «اهمیت» نقشه جوړه کړه — روښانه = مهمه ساحه."""
    w, h = img.size
    scale = _ANALYSIS_LONG / max(w, h)
    sw, sh = max(8, int(w * scale)), max(8, int(h * scale))
    # `reducing_gap` لومړی یوه ګړندۍ `reduce()` کوي — د ۴۰۰۰px انځور لپاره
    # دا وخت له ~۲۲۰ms څخه ~۲۵ms ته راکموي.
    small = img.convert("RGB").resize((sw, sh), Image.BOX, reducing_gap=2.0)

    gray = small.convert("L")
    # څنډې = تفصیل. MaxFilter یې لږ پراخوي چې یو نری خط ټوله سوژه ونه ښيي.
    edges = gray.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(3))
    # ⚠️ `FIND_EDGES` د انځور پر څنډو یو روښانه ۱px چوکاټ پریږدي (د کرنل
    # artifact دی، نه ریښتینی تفصیل). که یې پرینږدو، ټوله «انرژي» څنډو ته
    # ټوپ وهي او کرپ د سوژې پرځای څنډې نیسي. نو څنډه تورېږي.
    ImageDraw.Draw(edges).rectangle([0, 0, sw - 1, sh - 1], outline=0, width=2)
    sat = small.convert("HSV").split()[1]
    skin = _skin_mask(small)

    def w(layer: Image.Image, weight: float) -> Image.Image:
        return layer.point(lambda v, k=weight: min(255, int(v * k)))

    energy = ImageChops.add(w(edges, _W_EDGE), w(sat, _W_SAT))
    return ImageChops.add(energy, w(skin, _W_SKIN))


def _best_offset(weights: list[float], window: int) -> int:
    """هغه پیل ومومه چې د `window` اوږدې کړکۍ دننه وزن تر ټولو لوړ وي."""
    n = len(weights)
    if window >= n:
        return 0
    prefix = [0.0]
    for v in weights:
        prefix.append(prefix[-1] + v)
    total = prefix[-1] or 1.0
    centre = (n - window) / 2.0
    span = max(1.0, n - window)
    best_i, best_score = 0, -1.0
    for i in range(n - window + 1):
        inside = (prefix[i + window] - prefix[i]) / total
        # د مرکز نږدېوالی: ۱ = دقیقاً مرکز، ۰ = بشپړه څنډه.
        nearness = 1.0 - abs(i - centre) / span
        score = inside + _CENTER_BIAS * nearness
        if score > best_score:
            best_i, best_score = i, score
    return best_i


def smart_crop_box(img: Image.Image, ratio: float) -> tuple[int, int, int, int]:
    """د ورکړل شوي نسبت **تر ټولو لوی** مستطیل ومومه چې سوژه پکې وي.

    ``ratio`` = عرض ÷ لوړوالی (د خبر قالب لپاره ۱۶۵۰/۱۳۰۹ ≈ ۱.۲۶).
    د (left, top, right, bottom) څلورګوني راګرځوي.
    """
    W, H = img.size
    src = W / H
    if abs(src - ratio) < 0.01:                 # دمخه سم نسبت دی
        return (0, 0, W, H)

    energy = importance_map(img)
    ew, eh = energy.size

    if src > ratio:                              # ډیر پراخ → کیڼ/ښي پرې کوو
        cw, ch = int(round(H * ratio)), H
        cols = list(energy.resize((ew, 1), Image.BOX).getdata())
        win = max(1, int(round(cw / W * ew)))
        left = int(round(_best_offset(cols, win) / ew * W))
        left = max(0, min(left, W - cw))
        return (left, 0, left + cw, ch)

    cw, ch = W, int(round(W / ratio))            # ډیر لوړ → پورته/ښکته پرې کوو
    rows = list(energy.resize((1, eh), Image.BOX).getdata())
    win = max(1, int(round(ch / H * eh)))
    top = int(round(_best_offset(rows, win) / eh * H))
    top = max(0, min(top, H - ch))
    return (0, top, cw, top + ch)


def smart_crop(img: Image.Image, width: int, height: int) -> Image.Image:
    """انځور د (width×height) نسبت ته ځیرک کرپ او بیا اندازه کړه."""
    box = smart_crop_box(img, width / height)
    cropped = img.crop(box)
    if cropped.size != (width, height):
        cropped = cropped.resize((width, height), Image.LANCZOS)
    return cropped
