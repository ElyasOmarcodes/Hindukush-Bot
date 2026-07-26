"""د کشیده (kashida/tatweel) پر بنسټ justify — د کاروونکي د جاوا منطق پایتون نسخه.

Arabic-script justification by inserting tatweel (ـ, U+0640) between eligible
connecting letters until a line fills the target width — the same rules as the
user's ElyasJustifyTextView (non-connecting-left, forbidden-prev, max per slot,
no latin/digits, not at word edges). Reused later for the quote design too.
"""

from __future__ import annotations

import random

TATWEEL = "ـ"
MAX_KASHIDA_PER_SLOT = 4
# یوه‌کلمه‌یي کرښه د فاصلو له لارې نشي غزیدلی — نو کشیده یې ډیره اجازه لري.
MAX_KASHIDA_SINGLE_WORD = 12

# هغه توري چې کیڼ لور ته نه نښلي (کشیده نه اخلي کله چې کیڼ (a) وي).
NON_CONNECTING_LEFT = set("ادذرزژوآأإءؤئةىٰ")
# هغه توري چې که مخکې (a) وي، کشیده نه ور اضافه کوو.
FORBIDDEN_PREV = set("ادذرزژږړوءأآإډ")
# د «لا» رباط (lam-alef) — د ل او ا ترمنځ کشیده رباط ماتوي: تلـاش ❌ / تلاش ✅
_LAM = "ل"
_ALEF = set("اآأإٱ")
_PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~،؛؟«»–—−")


def _is_diacritic(ch: str) -> bool:
    o = ord(ch)
    return (0x0610 <= o <= 0x061A) or (0x064B <= o <= 0x065F) or o == 0x0670 or (0x06D6 <= o <= 0x06ED)


def _is_arabic_letter(ch: str) -> bool:
    o = ord(ch)
    in_block = (0x0600 <= o <= 0x06FF) or (0xFB50 <= o <= 0xFDFF) or (0xFE70 <= o <= 0xFEFF) or (0x0750 <= o <= 0x077F)
    return in_block and not _is_diacritic(ch)


def _is_latin_or_digit(ch: str) -> bool:
    return ch.isdigit() or (ch.isascii() and ch.isalpha())


def _is_ws_or_punct(ch: str) -> bool:
    return ch.isspace() or ch in _PUNCT


def _can_insert_at(s: str, idx: int) -> bool:
    """آیا د idx (کیڼ توری a) او idx+1 (b) ترمنځ کشیده اضافه کیدلی شي؟ (د جاوا قواعد)."""
    n = len(s)
    # idx باید داسې وي چې before=idx-1 او after=idx+2 دواړه موجود وي.
    if idx < 1 or idx > n - 3:
        return False
    a, b = s[idx], s[idx + 1]
    if _is_ws_or_punct(a) or _is_ws_or_punct(b):
        return False
    if not _is_arabic_letter(a) or not _is_arabic_letter(b):
        return False
    if a in NON_CONNECTING_LEFT or a in FORBIDDEN_PREV:
        return False
    if b == TATWEEL:
        return False
    # د «لا» رباط مه ماتوه — «تلـاش» ناسم دی، «تلاش» سم دی.
    if a == _LAM and b in _ALEF:
        return False
    before, after = s[idx - 1], s[idx + 2]
    if not _is_arabic_letter(before) or not _is_arabic_letter(after):
        return False
    if _is_latin_or_digit(before) or _is_latin_or_digit(after) or _is_latin_or_digit(a) or _is_latin_or_digit(b):
        return False
    return True


def _slots(s: str) -> list[int]:
    return [i for i in range(1, len(s) - 1) if _can_insert_at(s, i)]


def _build(line: str, counts: dict) -> str:
    """د هر slot وروسته د شمېر په اندازه tatweel ور دننه کړه."""
    out = []
    for i, ch in enumerate(line):
        out.append(ch)
        c = counts.get(i, 0)
        if c:
            out.append(TATWEEL * c)
    return "".join(out)


def justify_line(line: str, measure, target_w: float, seed: int | None = None) -> str:
    """یوه کرښه د کشیده په مټ تر ``target_w`` پورې ډکه کړه (بشپړ عرض).

    ``measure(text) -> float`` عرض ورکوي (د راقم/فونټ له لارې). که کرښه دمخه پراخه
    وي یا کوم slot نه وي، همغه کرښه راګرځي.

    ⚠️ کله چې یو slot تر target واوړي، **ټوله دوره نه ودریږي** — هغه slot یوازې
    بند شي او نور slotونه هڅه کوي. په دې سره وروستۍ تشه تر ټولو وړه کیږي؛ پاتې
    وړه تشه بیا د رسمولو پر مهال د کلمو ترمنځ ویشل کیږي (بشپړ justify).
    """
    if measure(line) >= target_w:
        return line
    slots = _slots(line)
    if not slots:
        return line
    rng = random.Random(seed)
    order = slots[:]
    rng.shuffle(order)
    counts: dict = {s: 0 for s in slots}
    # یوه‌کلمه‌یي کرښه وروسته د فاصلو له لارې نشي غزیدلی → کشیده ته ډیر ځای ورکړه.
    cap = MAX_KASHIDA_SINGLE_WORD if len(line.split()) == 1 else MAX_KASHIDA_PER_SLOT

    blocked: set = set()
    guard = 0
    while guard < 20000:
        guard += 1
        progressed = False
        for slot in order:
            if slot in blocked or counts[slot] >= cap:
                continue
            counts[slot] += 1
            if measure(_build(line, counts)) > target_w:
                counts[slot] -= 1  # دا slot نور نه ځایېږي — بند یې کړه، بل ته لاړ شه
                blocked.add(slot)
                continue
            progressed = True
        if not progressed:
            break
    return _build(line, counts)
