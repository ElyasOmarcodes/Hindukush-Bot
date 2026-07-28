"""د Gemini تحلیل — د راپورونو سرلیک/سرچینه راویستل + cascade fallback.

Gemini analysis. Builds the strict "verbatim title / source split" prompt,
calls Gemini with a cascade across keys and models (retrying on 503/UNAVAILABLE),
and parses the JSON reply robustly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from itertools import permutations

from .config import Config

logger = logging.getLogger("gemini")

# د تحلیل لارښود — Gemini یوازې کټ مټ پاراګراف راوباسي؛ بیلول په کوډ کې کیږي.
PROMPT_TEMPLATE = """تاسو د یوې خبري ادارې مرستیال یاست. لاندې تر دریو راپورونه درکړل \
شوي دي (کیدای شي پښتو، فارسي او انګلیسي وي).

⚠️ ستا دنده (۱۰۰٪ قلف):
1. د هر راپور **یوازې سرلیک = لومړی لاین** راوباسه. سرلیک تر **لومړي line-break** \
پورې دی؛ که line-break نه وي، نو تر **لومړۍ ټکي (.)** پورې لومړۍ جمله. سرلیک **کټ مټ \
(VERBATIM)** کاپي کړه — هیڅ بدلون، هیڅ ژباړه.
2. ⚠️ **خورا مهم:** یوازې سرلیک (لومړی لاین/جمله) راوباسه — **نه** دوهمه جمله، **نه** د \
بدن (body) پاراګرافونه، **نه** بشپړ راپور. که د سرلیک وروسته نور متن وي، هغه **بشپړ \
پریږده**. سرلیک معمولاً ۱ یا ۲ لنډې کرښې دي، نه ۴-۵.
3. که د سرلیک په پیل کې د سرچینې نوم وي (لکه «الجزیره:»)، هغه ورسره ونیسه — زه به یې \
پخپله بیل کړم.
4. ژبې: هر راپور خپلې ژبې ته وسپاره — `text_ps` = د پښتو راپور سرلیک، `text_fa` = فارسي، \
`text_en` = انګلیسي. **د هر ورکړل شوي راپور لپاره خپله ساحه حتماً ډکه کړه** — که یو \
راپور په یوه ژبه ورکړل شوی، هغه ساحه مه پریږده تشه.
5. که کومه ژبه اصلاً نه وي ورکړل شوې، یوازې هغه ساحه خالي رشته "" پریږده.

د موقعیت (location) ټاکل — دا اصول **په ترتیب** سره پلي کړه، یو ارزښت راوباسه:
1. که خبر کې افغانستان په هره بڼه ښکیل وي (یوازې، یا له بل هیواد سره یوځای)، یا خاص \
د افغانستان په اړه وي → "Afghanistan".
2. که نه، او خبر یوازې د افغانستان د پولې ګاونډیو هیوادونو (پاکستان، ایران، تاجیکستان، \
ازبکستان، ترکمنستان، چین) په اړه وي → "Region". خو که له دې ګاونډیو سره یو غیر-سیمه‌ایز \
هیواد هم ښکیل وي → "World".
3. که نه، او خبر یوازې د منځني ختیځ هیوادونو (سعودي، امارات، قطر، کویټ، بحرین، عمان، یمن، \
عراق، سوریه، لبنان، اردن، فلسطین، اسراییل، مصر، ترکیه) په اړه وي → "Middle East". خو که \
له منځني ختیځ سره یو بل هیواد (سیمه یا نړۍ) هم ښکیل وي → "World".
4. که نه → "World".

یوازې JSON راوګرځوه، پرته له کوم markdown یا تشریح:
{{
  "text_ps": "...",
  "text_fa": "...",
  "text_en": "...",
  "location": "Middle East"
}}

راپورونه:
{reports}
"""

# د پارس کولو تمه شوې خامې کیلیانې (له Gemini).
_RAW_KEYS = ("text_ps", "text_fa", "text_en", "location")
# د render لخوا کارول شوې وروستۍ کیلیانې.
_EXPECTED_KEYS = (
    "title_ps", "source_ps", "title_fa", "source_fa",
    "title_en", "source_en", "location",
)

# د سرچینې بیلونکي نښې — colon ډولونه + پایپ + فاصله-ډش.
_COLON_SEPS = (":", "：", "﹕")
_OTHER_SEPS = ("|", "｜", " - ", " – ", " — ")

# د کاروونکي اختصاصي، تجربه شوی پرامپټ د سرچینې د معنوي استخراج لپاره.
# The user's proven source-extraction prompt (do not paraphrase — used verbatim).
SOURCE_PROMPT = """Role: Professional Media Analyst & Expert Pashto Linguist.
Task: Extract the **Official News Source Entity** from the provided text.

--- CORE OBJECTIVE ---
Identify the organization, agency, or official body responsible for the news report. The output must be the "Nominative" (Standard/Dictionary) name of the entity in the original language (Pashto/Persian/English).

--- STRICT LINGUISTIC RULES (PASHTO SPECIFIC) ---

1. RULE: GRAMMAR CORRECTION (OBLIQUE TO NOMINATIVE)
   Pashto nouns change form when followed by prepositions/postpositions. You MUST revert them to their original Dictionary Name.
   - IF input is "نړيوالو رسنيو" (Oblique) -> OUTPUT: "نړيوالې رسنۍ" (Nominative).
   - IF input is "ازبکستاني چارواکو" -> OUTPUT: "ازبکستاني چارواکي".
   - IF input is "الجزیرې" (ending in ې due to context) -> OUTPUT: "الجزیره" (ending in ه).

2. RULE: HANDLING THE PARTICLE "د" (THE/OF)
   - KEEP "د" if it is part of an Official Title (Ministries, Directorates).
     * Correct: "د ملي دفاع وزارت" (Ministry of Defense).
     * Correct: "د ریاست‌الوزراء اقتصادي معاونیت".
   - REMOVE "د" if it indicates mere possession or reporting.
     * Wrong: "د رویټرز" -> Correct: "رویټرز".
     * Wrong: "د طلوع نیوز" -> Correct: "طلوع نیوز".

3. RULE: HIERARCHY & LOCATION CONTEXT
   - Do NOT amputate parent organizations if the child entity is generic.
     * Input: "د ریاست‌الوزراء اقتصادي مرستیال" -> Output: "د ریاست‌الوزراء اقتصادي معاونیت" (Not just "اقتصادي معاونیت").
   - KEEP Location for Embassies/Missions.
     * Input: "په قطر کې د افغانستان سفارت" -> Output: "قطر کې د افغانستان سفارت" (Because "Afghanistan Embassy" alone is ambiguous).

4. RULE: CLEAN EXTRACTION
   - Remove reporting verbs: "ویلي دي", "راپور ورکړ", "زیاته کړې".
   - Remove prepositions: "له قوله", "د ... پر بنسټ", "According to".

--- FEW-SHOT EXAMPLES (LEARN FROM THESE PATTERNS) ---

Input: "نړيوالو رسنيو د ملګرو ملتونو له قوله ويلي چې مرستې بندېږي."
Output: نړيوالې رسنۍ

Input: "د ازبکستان د احصایې ملي ادارې په خپل تازه راپور کې ویلي."
Output: د ازبکستان د احصایې ملي اداره

Input: "په قطر کې د افغانستان سفارت سرپرست له چینایي استازي سره وکتل."
Output: قطر کې د افغانستان سفارت

Input: "د ملي دفاع وزارت د ستراتیژۍ مرستیال روسیې ته لاړ."
Output: د ملي دفاع وزارت

Input: "الحزیرې خبر ورکړی چې په کانګو کې پېښه شوې."
Output: الجزیره

Input: "د سرې میاشتې ټولنې مشرتابه ویلي."
Output: د سرې میاشتې ټولنه

Input: "باختر آژانس خبر ورکوي."
Output: باختر آژانس

--- ADDITIONAL RULES (SOURCE LOCATION & FALLBACK) ---

5. RULE: WHERE THE SOURCE APPEARS (SEARCH THE WHOLE TEXT)
   The source is not always in the first line. It is most often at the start of
   the FIRST line or the SECOND paragraph, and is sometimes moved to the THIRD
   paragraph. Read the entire text before deciding — do not stop at line one.

6. RULE: SOURCE INSIDE THE HEADLINE
   Sometimes the first paragraph (the headline) begins with the source, separated
   from the rest by a ":" character. Example: "الجزیره: خبر...". Here "الجزیره"
   is the source.

7. RULE: ORGANIZATION OVER PERSON (STRICT)
   If the text contains BOTH a person's name AND a media outlet / organization /
   group name (e.g. الجزیره, رویټرز, باختر آژانس), you MUST choose the
   media/organization/group. In ~99% of cases the source is an outlet, an
   organization, or a group — NEVER a person's personal name.

8. RULE: FALLBACK WHEN NO SOURCE EXISTS
   If NO media outlet / organization / movement is explicitly named as the origin
   of the report, output the default word for the input language:
   - Pashto  -> راپورونه
   - Persian -> گزارش‌ها
   - English -> Reports

--- END OF INSTRUCTIONS ---

Now, extract the source from the following text:

"""

# د نقل قول اختصاصي پرامپټ — نوم + رتبه + نقل قول راباسي (کټ مټ).
QUOTE_PROMPT = """Role: Expert Linguistic Analyst and Media Monitor.
Task: Analyze the text and extract entities in JSON format.

RULES FOR EXTRACTION:
1. LANGUAGE: The extracted 'name' and 'rank' MUST be in the SAME language as the input text (e.g., Pashto input -> Pashto output).
2. DEEP SEARCH ('name' & 'rank'): Scan the ENTIRE text to find the correct name and title/rank. They might appear in the 1st, 2nd, or last paragraph.
3. QUOTE LOGIC (CRITICAL):
   - Target ONLY the FIRST paragraph (or headline).
   - Case A: If 1st paragraph starts with 'Name/Rank says: ...', REMOVE the name/rank introduction and keep the rest of the sentence EXACTLY VERBATIM.
   - Case B: If 1st paragraph is just the statement, keep it 100% IDENTICAL (Verbatim).
   - Do NOT edit, summarize, or change words in the 'quote'.
4. ZERO CHANGES: The 'quote' must NOT be altered even 1%. It is written by
   professional journalists — never reword, translate, fix, or paraphrase it.
5. NO TRAILING PUNCTUATION: The 'quote' must NOT end with a trailing period or
   exclamation mark (e.g. '.', '!', '۔'). If the sentence ends with one, remove it.

OUTPUT FORMAT: Return raw JSON only: { "name": "...", "rank": "...", "quote": "..." }
NO Markdown formatting.

Input Text:
"""


# د کاروونکي د سرچینې قواعد **کټ مټ** (له «--- END OF INSTRUCTIONS ---» پرته)
# چې په ګډ پرامپټ کې یې بیا وکاروو — یو توری یې هم نه بدلوو.
_SOURCE_RULES = SOURCE_PROMPT.split("--- END OF INSTRUCTIONS ---")[0].strip()

# ⚠️ **یو کال، یو ځواب.** پخوا مو څلور جلا کالونه کول (۱ سرلیک/موقعیت + ۳
# سرچینې) — دا هم وخت خوري او هم د وړیا کوټې څلور برخې. اوس ټول یوځای غواړو.
COMBINED_PROMPT = """تاسو د یوې خبري ادارې مرستیال یاست. لاندې تر دریو راپورونه \
درکړل شوي دي (کیدای شي پښتو، فارسي او انګلیسي وي).

⚠️ ستا دنده درې برخې لري: (الف) سرلیک، (ب) سرچینه، (ج) موقعیت.

════════ (الف) سرلیک ════════
1. د هر راپور **یوازې سرلیک = لومړی لاین** راوباسه. سرلیک تر **لومړي line-break** \
پورې دی؛ که line-break نه وي، نو تر **لومړۍ ټکي (.)** پورې لومړۍ جمله. سرلیک **کټ مټ \
(VERBATIM)** کاپي کړه — هیڅ بدلون، هیڅ ژباړه.
2. ⚠️ **خورا مهم:** یوازې سرلیک (لومړی لاین/جمله) راوباسه — **نه** دوهمه جمله، **نه** د \
بدن (body) پاراګرافونه، **نه** بشپړ راپور. که د سرلیک وروسته نور متن وي، هغه **بشپړ \
پریږده**. سرلیک معمولاً ۱ یا ۲ لنډې کرښې دي، نه ۴-۵.
3. که د سرلیک په پیل کې د سرچینې نوم وي (لکه «الجزیره:»)، هغه **د سرلیک دننه پریږده** — \
زه به یې پخپله بیل کړم؛ خو سرچینه یې جلا هم ولیکه (لاندې برخه ب).
4. ژبې: هر راپور خپلې ژبې ته وسپاره — `text_ps` = د پښتو راپور سرلیک، `text_fa` = فارسي، \
`text_en` = انګلیسي. **د هر ورکړل شوي راپور لپاره خپله ساحه حتماً ډکه کړه**.
5. که کومه ژبه اصلاً نه وي ورکړل شوې، هغه ساحه خالي رشته "" پریږده.

════════ (ب) سرچینه ════════
د **هر** راپور لپاره سرچینه راوباسه (`source_ps` / `source_fa` / `source_en`) — د هغې \
ژبې په خپله ژبه. لاندې قواعد **کټ مټ** پلي کړه (دا د بشپړ راپور متن ته ګوري، نه یوازې \
سرلیک ته):

{source_rules}

════════ (ج) موقعیت ════════
دا اصول **په ترتیب** سره پلي کړه، یو ارزښت راوباسه:
1. که خبر کې افغانستان په هره بڼه ښکیل وي (یوازې، یا له بل هیواد سره یوځای)، یا خاص \
د افغانستان په اړه وي → "Afghanistan".
2. که نه، او خبر یوازې د افغانستان د پولې ګاونډیو هیوادونو (پاکستان، ایران، تاجیکستان، \
ازبکستان، ترکمنستان، چین) په اړه وي → "Region". خو که له دې ګاونډیو سره یو غیر-سیمه‌ایز \
هیواد هم ښکیل وي → "World".
3. که نه، او خبر یوازې د منځني ختیځ هیوادونو (سعودي، امارات، قطر، کویټ، بحرین، عمان، یمن، \
عراق، سوریه، لبنان، اردن، فلسطین، اسراییل، مصر، ترکیه) په اړه وي → "Middle East". خو که \
له منځني ختیځ سره یو بل هیواد (سیمه یا نړۍ) هم ښکیل وي → "World".
4. که نه → "World".

════════ خروجي ════════
**یوازې** دا JSON راوګرځوه، پرته له کوم markdown یا تشریح:
{{
  "text_ps": "...", "source_ps": "...",
  "text_fa": "...", "source_fa": "...",
  "text_en": "...", "source_en": "...",
  "location": "Middle East"
}}

راپورونه:
{reports}
"""


# د کاروونکي د نقل قول قواعد **کټ مټ** (له OUTPUT FORMAT پرته).
_QUOTE_RULES = QUOTE_PROMPT.split("OUTPUT FORMAT:")[0].strip()

# ⚠️ یو کال د ټولو ژبو لپاره (پخوا فی ژبه یو کال = تر ۳ کالونو).
# ژبې مو **په کوډ کې** ټاکلې دي (map_reports)، نو دلته یې یوازې لیبل ورکوو —
# د Gemini پر ژب-پیژندنې تکیه نه کوو.
QUOTE_COMBINED_PROMPT = """Role: Expert Linguistic Analyst and Media Monitor.
Task: For EACH labelled report below, extract its entities.

{rules}

OUTPUT FORMAT: Return raw JSON only, keyed by the language code shown in each
label. Include ONLY the languages that appear below. NO Markdown.
{{
  "ps": {{ "name": "...", "rank": "...", "quote": "..." }},
  "fa": {{ "name": "...", "rank": "...", "quote": "..." }},
  "en": {{ "name": "...", "rank": "...", "quote": "..." }}
}}

Reports:
{reports}
"""


def build_prompt(texts: list[str]) -> str:
    """د راپورونو لیست د تحلیل پرامپټ ته واړوه.  Build the analysis prompt."""
    numbered = "\n\n".join(f"[راپور {i}]\n{t}" for i, t in enumerate(texts, 1))
    return COMBINED_PROMPT.format(source_rules=_SOURCE_RULES, reports=numbered)


def extract_json(text: str) -> dict:
    """د Gemini د ځواب څخه JSON راوباسه، بیا سرچینه/سرلیک په کوډ کې بیل کړه.

    Parse the model reply (tolerating ```json fences), then deterministically
    split each verbatim paragraph into source + title. Supports both the new
    ``text_*`` shape and the older ``title_*``/``source_*`` shape.
    """
    return build_result(_parse_json(text))


def _parse_json(text: str) -> dict:
    """د خام ځواب څخه JSON دیکشنري راوباسه (د ```json حصار زغم سره)."""
    if not text:
        raise ValueError("د Gemini ځواب خالي دی")
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def build_result(data: dict) -> dict:
    """خامه Gemini پایله د render لپاره {title_*, source_*, location} ته واړوه."""
    result: dict = {"location": (data.get("location") or "").strip()}
    for lang in ("ps", "fa", "en"):
        # نوی فارمټ (text_*) یا زوړ (title_*) — پاراګراف واخله.
        para = (data.get(f"text_{lang}") or data.get(f"title_{lang}") or "").strip()
        src, title = _split_source_title(para)
        # که Gemini پخپله جلا سرچینه ورکړې وه او پاراګراف کې «:» نه و.
        existing_src = (data.get(f"source_{lang}") or "").strip()
        if not src and existing_src:
            src, title = existing_src, para
        result[f"source_{lang}"] = src
        result[f"title_{lang}"] = title
    return result


def _split_source_title(text: str) -> tuple[str, str]:
    """که د پیل نږدې د سرچینې بیلونکی وي، (سرچینه, سرلیک) بیل کړه؛ نه‌نو ("", متن).

    Split "الجزیره: خبر" → ("الجزیره:", "خبر"). Handles colon (kept in source),
    plus pipe and spaced-dash separators (dropped). Only near the start (≤40
    chars) so mid-sentence punctuation is untouched.
    """
    if not text:
        return "", text
    best_idx = -1
    best_sep = ""
    for sep in _COLON_SEPS + _OTHER_SEPS:
        i = text.find(sep)
        if 0 < i <= 40 and (best_idx == -1 or i < best_idx):
            best_idx, best_sep = i, sep
    if best_idx == -1:
        return "", text
    if best_sep in _COLON_SEPS:  # colon په سرچینه کې ساتل کیږي
        source = text[: best_idx + len(best_sep)].strip()
    else:  # پایپ/ډش لرې کیږي
        source = text[:best_idx].strip()
    rest = text[best_idx + len(best_sep) :].strip()
    if not rest or not source:
        return "", text
    return source, rest


def ensure_sources(data: dict) -> dict:
    """د زاړه فارمټ (title_*/source_*) لپاره د سرچینې بیلولو خوندي شبکه."""
    return build_result(data)


def _is_quota(err: Exception) -> bool:
    """آیا دا خطا د کوټې/لیمیټ ده (نه ریښتینې تخنیکي ستونزه)؟"""
    msg = str(err).upper()
    return any(tok in msg for tok in (
        "429", "RESOURCE_EXHAUSTED", "QUOTA", "RATE", "503", "UNAVAILABLE", "OVERLOAD",
    ))


# ─────────────── د کوټې حافظه (کولډاون) ───────────────
# ⚠️ ستونزه: کله چې د یوه مډل ورځنۍ کوټه ختمه شي، هره نوې غوښتنه یې بیا له سره
# ازمویله — هر (مډل، کیلي) جوړه څو ثانیې ضایع کولې. اوس یې په حافظه کې ساتو
# او تر بیا-تازه کیدو پورې یې پرېږدو.
#
# Google د وړیا کچې ورځنۍ کوټه **د America/Los_Angeles په نیمه شپه** بیا تازه
# کوي. ZoneInfo کاروو (نه ثابت offset) نو د اوړي/ژمي (DST) بدلون پخپله سم شي.
_QUOTA_TZ = "America/Los_Angeles"

# د «ورځنۍ» کوټې نښه د Google د خطا په quotaId کې (لکه
# "GenerateRequestsPerDayPerProjectPerModel-FreeTier").
_DAILY_MARKERS = ("perday", "per_day", "requests_per_day")
_RETRY_DELAY_RE = re.compile(r"retrydelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)\s*s", re.I)
# د لنډې (فی-دقیقې / بار زیاتوالی) ستونزې ډیفالټ کولډاون.
_DEFAULT_SHORT_COOLDOWN = 60.0
_OVERLOAD_COOLDOWN = 20.0
# ورو (timeout) جوړه — لنډ یې پریږدو، ښایي بیا ژر شي.
_SLOW_COOLDOWN = 45.0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def quota_timezone():
    """د کوټې د بیا-تازه کیدو وخت-سیمه — ZoneInfo، یا fallback."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(_QUOTA_TZ)
    except Exception:  # noqa: BLE001 - tzdata نشته
        logger.warning("د «%s» وخت-سیمه ونه موندل شوه — ثابت UTC-08:00 کارول کیږي",
                       _QUOTA_TZ)
        return timezone(timedelta(hours=-8))


def next_quota_reset(now: datetime | None = None) -> datetime:
    """راتلونکې نیمه شپه (۰۰:۰۰) د America/Los_Angeles په وخت — د UTC په بڼه."""
    tz = quota_timezone()
    local = (now or _now_utc()).astimezone(tz)
    tomorrow = (local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return tomorrow.astimezone(timezone.utc)


def _is_daily_quota(err: Exception) -> bool:
    """آیا دا د **ورځنۍ** کوټې ختمېدل دي (نه فی-دقیقې لیمیټ)؟"""
    s = str(err).lower().replace(" ", "")
    return any(m in s for m in _DAILY_MARKERS)


def _is_overload(err: Exception) -> bool:
    """۵۰۳ / بار زیاتوالی — لنډمهاله ده، نه کوټه."""
    s = str(err).upper()
    return any(t in s for t in ("503", "UNAVAILABLE", "OVERLOAD"))


def _retry_delay(err: Exception, default: float = _DEFAULT_SHORT_COOLDOWN) -> float:
    m = _RETRY_DELAY_RE.search(str(err))
    return float(m.group(1)) if m else default


class QuotaTracker:
    """د (مډل، کیلي) جوړو کولډاون حافظه — په روان پروسس کې ساتل کیږي.

    Remembers which (model, key) pairs are rate-limited and until when, so the
    cascade skips them instead of burning seconds on a call that will fail.
    """

    def __init__(self) -> None:
        self._until: dict[tuple[str, int], datetime] = {}
        self._reason: dict[tuple[str, int], str] = {}

    def clear(self) -> None:
        self._until.clear()
        self._reason.clear()

    def block_until(self, model: str, key_idx: int, until: datetime, reason: str) -> None:
        key = (model, key_idx)
        # تل اوږد کولډاون وساته (ورځنۍ کوټه تر لنډې مهمه ده).
        if key not in self._until or until > self._until[key]:
            self._until[key] = until
            self._reason[key] = reason

    def block_daily(self, model: str, key_idx: int) -> datetime:
        """تر راتلونکې نیمې شپې (Los Angeles) پورې یې پرېږده."""
        until = next_quota_reset()
        self.block_until(model, key_idx, until, "ورځنۍ کوټه")
        logger.warning(
            "ورځنۍ کوټه ختمه: %s / کیلي #%d — تر %s (LA نیمه شپه) پورې پرېښودل کیږي",
            model, key_idx, until.astimezone(quota_timezone()).strftime("%Y-%m-%d %H:%M %Z"),
        )
        return until

    def block_seconds(self, model: str, key_idx: int, seconds: float, reason: str) -> None:
        until = _now_utc() + timedelta(seconds=max(1.0, seconds))
        self.block_until(model, key_idx, until, reason)
        logger.info("%s: %s / کیلي #%d — %.0f ثانیې پرېښودل کیږي",
                    reason, model, key_idx, seconds)

    def blocked_until(self, model: str, key_idx: int) -> datetime | None:
        until = self._until.get((model, key_idx))
        if until is None:
            return None
        if until <= _now_utc():        # وخت یې تېر شو → بیرته لیست ته راځي
            self._until.pop((model, key_idx), None)
            self._reason.pop((model, key_idx), None)
            logger.info("کوټه بیا تازه شوه: %s / کیلي #%d — بیرته فعال", model, key_idx)
            return None
        return until

    def is_blocked(self, model: str, key_idx: int) -> bool:
        return self.blocked_until(model, key_idx) is not None

    def earliest_unblock(self) -> datetime | None:
        alive = [u for u in self._until.values() if u > _now_utc()]
        return min(alive) if alive else None

    def note(self, err: Exception, model: str, key_idx: int) -> None:
        """د خطا پر بنسټ سم کولډاون ورکړه."""
        if _is_daily_quota(err):
            self.block_daily(model, key_idx)
        elif _is_overload(err):
            self.block_seconds(model, key_idx, _retry_delay(err, _OVERLOAD_COOLDOWN),
                               "بار زیاتوالی")
        elif _is_quota(err):
            self.block_seconds(model, key_idx, _retry_delay(err), "لنډ لیمیټ")

    def status(self) -> list[str]:
        """د بندو جوړو لنډ لیست (د /status لپاره)."""
        tz = quota_timezone()
        out = []
        for (model, key_idx), until in sorted(self._until.items(), key=lambda kv: kv[1]):
            if until <= _now_utc():
                continue
            out.append(f"{model} / کیلي #{key_idx} → {until.astimezone(tz):%H:%M} LA "
                       f"({self._reason.get((model, key_idx), '')})")
        return out


# د پروسس په اوږدو کې یوه ګډه حافظه.
quota = QuotaTracker()


def _clean_source(raw: str) -> str:
    """د سرچینې د پرامپټ ځواب پاک کړه — لومړۍ کرښه، د quotes/Output: پرته."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.splitlines()[0].strip()
    s = re.sub(r"^(Output|Source|Answer|منبع|سرچینه)\s*[:：]\s*", "", s, flags=re.I).strip()
    return s.strip("\"'`«»").strip()


async def _cascade(prompt: str, config: Config) -> str:
    """یو پرامپټ د قوت پر بنسټ cascade سره واستوه؛ خام متن راوګرځوه.

    Model-major, key-minor cascade: strongest model first, key #1 then #2 …,
    then the next model. Any failure (quota or otherwise) advances silently;
    raises only if every (model, key) pair fails.
    """
    if not config.gemini_api_keys:
        raise RuntimeError("د Gemini هیڅ کیلي نه ده تنظیم شوې (GEMINI_API_KEYS).")

    # وارد کول دلته کوو چې د یونټ-ټیسټ پر مهال google-genai ته اړتیا نه وي.
    from google import genai

    clients = [(i + 1, genai.Client(api_key=k)) for i, k in enumerate(config.gemini_api_keys)]
    timeout = float(getattr(config, "gemini_timeout", 20.0) or 20.0)
    last_error: Exception | None = None
    tried = 0
    skipped = 0

    for model in config.gemini_models:
        for key_idx, client in clients:
            # ⚠️ که دې جوړې دمخه کوټه/لیمیټ ورکړی و، بیخي یې مه ازموه —
            # همدا هغه ځای و چې هره غوښتنه یې څو ثانیې ضایع کولې.
            if quota.is_blocked(model, key_idx):
                skipped += 1
                continue
            tried += 1
            started = time.monotonic()
            try:
                # ⚠️ وخت-حد حتمي دی — پرته له دې یو ورو ځواب ټوله دوره بندوي.
                resp = await asyncio.wait_for(
                    client.aio.models.generate_content(model=model, contents=prompt),
                    timeout=timeout,
                )
                if resp.text and resp.text.strip():
                    logger.info("Gemini بریالی: %s / کیلي #%d (%.1fs)",
                                model, key_idx, time.monotonic() - started)
                    return resp.text
                raise RuntimeError("خالي ځواب")
            except (asyncio.TimeoutError, TimeoutError) as err:
                last_error = err
                # ورو جوړه لنډ وخت پریږده — بل مډل ښایي سمدلاسه ځواب ورکړي.
                quota.block_seconds(model, key_idx, _SLOW_COOLDOWN, "ورو ځواب")
                logger.warning("Gemini ورو و (%s / کیلي #%d): تر %.0fs زیات — بل ته ځم",
                               model, key_idx, timeout)
                continue
            except Exception as err:  # noqa: BLE001 - cascade په قصد پراخ دی
                last_error = err
                quota.note(err, model, key_idx)   # کولډاون یې یاد وساته
                reason = "کوټه/لیمیټ" if _is_quota(err) else "خطا"
                logger.warning("Gemini %s (%s / کیلي #%d): %s — بل ته ځم", reason, model, key_idx, err)
                continue

    if skipped:
        logger.info("د کوټې حافظې %d (مډل/کیلي) جوړې پرېښودې — %d وازمویل شوې",
                    skipped, tried)
    if tried == 0:
        nxt = quota.earliest_unblock()
        when = (nxt.astimezone(quota_timezone()).strftime("%H:%M (%Z)")
                if nxt else "نامعلوم")
        raise RuntimeError(
            "ټولو مډلونو/کیلیانو کوټه ختمه ده. راتلونکې هڅه: " + when +
            " — د Google وړیا کچه د لاس‌انجلس په نیمه شپه بیا تازه کیږي."
        )
    raise RuntimeError(f"ټول مډلونه/کیلیانې ناکام شول: {last_error}")


# د منبع ډیفالټ (rule 8) — کله چې هیڅ رسنۍ/ارګان ونه موندل شي.
_DEFAULT_SOURCE = {"ps": "راپورونه", "fa": "گزارش‌ها", "en": "Reports"}

_VALID_LOCATIONS = ("Afghanistan", "Region", "Middle East", "World")

# ─────────── د ژبې پیژندنه (پښتو / فارسي / انګلیسي) ───────────
# عاجل راپورونه اکثر لنډ وي، نو یوازې «ځانګړي توري» بس نه دي — د توریو تر څنګ
# د دواړو ژبو د **عام کلمو** نښې هم شمېرو، بیا تر ټولو لوړ نمره ژبه ټاکو.

# یوازې پښتو دا توري کاروي (فارسي یې نه لري).
_PASHTO_LETTERS = set("ښږڅځډړټڼېۍګ")
# یوازې فارسي دا توري کاروي (پښتو د دې پر ځای «ګ» U+06AB کاروي).
_PERSIAN_LETTERS = set("گ")
# (زوړ نوم — لا هم د شاته-سمون لپاره ساتل شوی)
_PASHTO_MARKERS = "ښږڅځډړټڼ"

# د پرامپټ لپاره د ژبو نومونه.
LANG_LABELS = {"ps": "Pashto", "fa": "Persian", "en": "English"}

# د دواړو ژبو عام او **نه-شریک** کلمې. شریکې کلمې (هم، نه، باید، پر…) قصداً نشته.
_PASHTO_WORDS = {
    "د", "په", "کې", "دی", "ده", "دي", "دې", "چې", "لپاره", "شوی", "شوې", "شول",
    "وویل", "ویلي", "ته", "له", "سره", "څخه", "باندې", "پورې", "یې", "مو", "دا",
    "هغه", "ټول", "اوس", "کوي", "کړي", "کړل", "کړه", "کړ", "خو", "ولې", "دغه",
    "ډیر", "ډېر", "ښه", "وو", "لري", "نور", "خپل", "زموږ", "زمونږ", "موږ", "مونږ",
    "وايي", "وایي", "پیښه", "ورځ", "کال", "هیواد", "هېواد", "ښار", "خلک", "نن",
}
_PERSIAN_WORDS = {
    "در", "از", "به", "که", "این", "آن", "را", "است", "برای", "شد", "شده", "می",
    "های", "هست", "هستند", "بود", "کرد", "کند", "کنیم", "کنند", "شود", "خود",
    "یک", "با", "تا", "نیز", "اما", "ولی", "گفت", "اعلام", "کرده", "آنها", "ما",
    "شما", "بین", "روی", "طبق", "اکنون", "همچنین", "دارد", "دارند", "پس", "چون",
    "کشور", "شهر", "مردم", "امروز", "سال", "روز", "گزارش", "خبر", "وی", "او",
}

# د اصلي (main) کال د بیا-هڅې اعظمي شمیر — چې کومه ژبه بې سرلیکه پاتې نشي.
_MAX_MAIN_ATTEMPTS = 3


def language_scores(text: str) -> dict:
    """د متن لپاره د هرې ژبې نمره — لوړه نمره = ډیر احتمال.

    Score each language: Pashto/Persian exclusive letters (weight 3) plus
    exclusive common words (weight 2). English wins outright on ASCII ratio.
    Persian gets a small tie-break because Pashto text nearly always carries at
    least one Pashto-only letter, while Persian carries none of them.
    """
    t = text.replace("‌", " ")  # ZWNJ = د کلمو بیلونکی (می‌کنیم → می کنیم)
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return {"ps": 0.0, "fa": 0.0, "en": 0.0}

    ascii_ratio = sum(1 for c in letters if c.isascii()) / len(letters)
    if ascii_ratio > 0.5:
        return {"ps": 0.0, "fa": 0.0, "en": 10.0 + ascii_ratio}

    words = re.findall(r"[^\W\d_]+", t, flags=re.UNICODE)
    ps = 3 * sum(1 for c in t if c in _PASHTO_LETTERS)
    ps += 2 * sum(1 for w in words if w in _PASHTO_WORDS)
    fa = 3 * sum(1 for c in t if c in _PERSIAN_LETTERS)
    fa += 2 * sum(1 for w in words if w in _PERSIAN_WORDS)
    return {"ps": float(ps), "fa": fa + 0.5, "en": 0.0}


def detect_language(text: str) -> str | None:
    """د متن ژبه په کوډ کې وپیژنه: en / ps / fa (یا None که تش وي)."""
    if not text.strip() or not any(c.isalpha() for c in text):
        return None
    scores = language_scores(text)
    return max(scores, key=lambda k: scores[k])


def first_paragraph(text: str) -> str:
    """د راپور لومړی ناخالي لاین (سرلیک) — د fallback لپاره کټ مټ."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return text.strip()


def map_reports(texts: list[str]) -> dict:
    """هر راپور خپلې ژبې ته وټاکه — {lang: raw_text}. (یو راپور فی ژبه).

    ⚠️ هر راپور جلا نه پیژنو. ټول یوځای ګورو او هغه **ترکیب** غوره کوو چې ټوله
    نمره یې تر ټولو لوړه وي. په دې سره دوه راپورونه هیڅکله یوه ژبه نه اخلي (پخوا
    به دویم په چوپه خوله له منځه تللی وای)، او که یو راپور لږ مبهم و، د نورو
    راپورونو شتون یې پریکړه سمه کوي — د لنډو عاجل راپورونو لپاره خورا مهم.
    """
    items = [t.strip() for t in texts if t.strip()][:3]
    if not items:
        return {}
    scores = [language_scores(t) for t in items]
    langs = ("ps", "fa", "en")

    best, best_total = None, None
    for perm in permutations(langs, len(items)):
        total = sum(scores[i][perm[i]] for i in range(len(items)))
        if best_total is None or total > best_total:
            best_total, best = total, perm

    per = {best[i]: items[i] for i in range(len(items))}
    logger.info("د ژبو ټاکنه: %s", ", ".join(f"#{i + 1}→{best[i]}" for i in range(len(items))))
    return per


def _match_full_text(paragraph: str, texts: list[str]) -> str:
    """د سرلیک پاراګراف د خام راپورونو سره ورته کړه، بشپړ متن راوګرځوه.

    The source may be in the 2nd/3rd paragraph, so feed the full report — find
    the raw text whose start matches this paragraph. Falls back to the paragraph.
    """
    key = re.sub(r"\s+", " ", paragraph.strip())[:40]
    if not key:
        return paragraph
    for t in texts:
        if key in re.sub(r"\s+", " ", t):
            return t
    return paragraph


def _normalize_location(value: str) -> str:
    value = (value or "").strip()
    return value if value in _VALID_LOCATIONS else "World"


def verify_result(result: dict, per_lang: dict) -> list[str]:
    """د نهایي کیدلو مخکې ټوله نتیجه یو ځل چک کړه؛ ستونزې راوګرځوه.

    Final safety check before rendering: every language that had a report must
    have a non-empty title and source, and the location must be valid.
    """
    problems: list[str] = []
    for lang in per_lang:
        if not (result.get(f"title_{lang}") or "").strip():
            problems.append(f"{lang}: عنوان نشته")
        if not (result.get(f"source_{lang}") or "").strip():
            problems.append(f"{lang}: منبع نشته")
    if result.get("location") not in _VALID_LOCATIONS:
        problems.append(f"موقعیت ناسم: {result.get('location')}")
    if problems:
        logger.error("د تایید ستونزې: %s", "؛ ".join(problems))
    else:
        logger.info("تایید بریالی — ټول %d ژبې عنوان+منبع لري", len(per_lang))
    return problems


async def extract_source(text: str, config: Config) -> str:
    """د کاروونکي اختصاصي پرامپټ په مرسته د یوه راپور سرچینه راوباسه.

    Runs the dedicated (proven) source prompt through the same cascade and
    returns the normalized source entity, or "" on failure/empty.
    """
    if not text.strip():
        return ""
    raw = await _cascade(SOURCE_PROMPT + text.strip(), config)
    return _clean_source(raw)


async def _main_extract(reports: list[str], config: Config) -> dict:
    """اصلي کال د سرلیکونو + موقعیت لپاره، د بیا-هڅې سره چې سرلیک ورک نشي.

    Retry-and-merge keyed on the number of reports: repeat until we have at
    least as many non-empty paragraphs as reports (plus a location), or attempts
    run out. Titles come ONLY from Gemini's verbatim first paragraph — there is
    no code fallback that could dump a whole report into the title.
    """
    n_reports = len(reports)
    raw: dict = {}
    last_error: Exception | None = None
    for attempt in range(1, _MAX_MAIN_ATTEMPTS + 1):
        try:
            parsed = _parse_json(await _cascade(build_prompt(reports), config))
        except Exception as err:  # noqa: BLE001
            last_error = err
            logger.warning("اصلي کال هڅه %d ناکام: %s", attempt, err)
            continue
        for lg in ("ps", "fa", "en"):
            if not (raw.get(f"text_{lg}") or "").strip() and (parsed.get(f"text_{lg}") or "").strip():
                raw[f"text_{lg}"] = parsed[f"text_{lg}"]
            # سرچینه اوس د همدې ګډ کال برخه ده — هغه هم یوځای کړه.
            if not (raw.get(f"source_{lg}") or "").strip() and (parsed.get(f"source_{lg}") or "").strip():
                raw[f"source_{lg}"] = parsed[f"source_{lg}"]
        if not raw.get("location") and (parsed.get("location") or "").strip():
            raw["location"] = parsed["location"]
        got = sum(1 for lg in ("ps", "fa", "en") if (raw.get(f"text_{lg}") or "").strip())
        if got >= n_reports and raw.get("location"):
            break
        if attempt < _MAX_MAIN_ATTEMPTS:
            logger.warning("تایید: %d/%d سرلیکونه ترلاسه — بیا هڅه", got, n_reports)

    if not any((raw.get(f"text_{lg}") or "").strip() for lg in ("ps", "fa", "en")):
        raise RuntimeError(f"د اصلي تحلیل کوم سرلیک ترلاسه نشو: {last_error}")
    return raw


def _first_paragraph_block(text: str) -> str:
    """لومړی پاراګراف (تر لومړۍ تشې کرښې)؛ که یو پاراګراف وو، ټول متن."""
    blocks = re.split(r"\n\s*\n", text.strip())
    return blocks[0].strip() if blocks else text.strip()


def extract_urgent_titles(texts: list[str]) -> dict:
    """د عاجل خبر عنوانونه — **بې AI**، په کوډ کې.

    Urgent needs no AI: title = the first paragraph (whole report if it is a
    single paragraph), with any trailing period removed. Language is detected in
    code. Returns {lang: title}.
    """
    titles: dict = {}
    for lang, raw in map_reports([t for t in texts if t.strip()]).items():
        title = _first_paragraph_block(raw)
        # د پای «.» (یا عربي ۔ / ｡) لرې کړه.
        title = title.rstrip().rstrip(".۔｡。").rstrip()
        if title:
            titles[lang] = title
    return titles


async def extract_quote(text: str, config: Config) -> dict:
    """د یوه راپور څخه {name, rank, quote} د اختصاصي پرامپټ له لارې راوباسه."""
    raw = await _cascade(QUOTE_PROMPT + text.strip(), config)
    data = _parse_json(raw)
    quote = (data.get("quote") or "").strip()
    # د پای اضافي «.!۔؟。！» لرې کړه (خوندي شبکه — د متن نور برخې ته لاس نه ورکوي).
    quote = quote.rstrip(" .!۔؟。！?").rstrip()
    return {
        "name": (data.get("name") or "").strip(),
        "rank": (data.get("rank") or "").strip(),
        "quote": quote,
    }


async def analyze_quote(texts: list[str], config: Config) -> dict:
    """د نقل قول لپاره — د **ټولو** ژبو {name, rank, quote} په **یوه کال** کې.

    ⚠️ پخوا مو فی ژبه یو کال کاوه (تر ۳ کالونو). اوس ژبې په کوډ کې ټاکو
    (map_reports — چې د لنډو راپورونو لپاره یې ځیرکه کړه) او Gemini ته یې
    لیبل شوي ورکوو، نو یو ځواب بس دی.

    Returns {lang: {name, rank, quote}} for languages whose quote is non-empty.
    """
    if not config.gemini_api_keys:
        raise RuntimeError("د Gemini هیڅ کیلي نه ده تنظیم شوې (GEMINI_API_KEYS).")
    per = map_reports([t for t in texts if t.strip()])
    if not per:
        return {}

    labelled = "\n\n".join(
        f"[{lang}] ({LANG_LABELS[lang]} report)\n{txt}" for lang, txt in per.items()
    )
    prompt = QUOTE_COMBINED_PROMPT.format(rules=_QUOTE_RULES, reports=labelled)

    data: dict = {}
    last_error: Exception | None = None
    for attempt in range(1, _MAX_MAIN_ATTEMPTS + 1):
        try:
            data = _parse_json(await _cascade(prompt, config))
            break
        except Exception as err:  # noqa: BLE001
            last_error = err
            logger.warning("د نقل قول کال هڅه %d ناکام: %s", attempt, err)
    if not data:
        raise RuntimeError(f"د نقل قول تحلیل ناکام: {last_error}")

    result: dict = {}
    for lang in per:
        d = data.get(lang) or {}
        if not isinstance(d, dict):
            continue
        quote = (d.get("quote") or "").strip().rstrip(" .!۔؟。！?").rstrip()
        if quote:
            result[lang] = {
                "name": (d.get("name") or "").strip(),
                "rank": (d.get("rank") or "").strip(),
                "quote": quote,
            }
        else:
            logger.warning("[%s] نقل قول تش راغی", lang)
    return result


async def extract_titles(texts: list[str], config: Config) -> dict:
    """یوازې سرلیکونه راوباسه (د عاجل خبر لپاره — بې سرچینې/موقعیت).

    Lightweight: the main call only (with retry), returning {lang: headline}
    for the languages that have a report. No source or location work.
    """
    if not config.gemini_api_keys:
        raise RuntimeError("د Gemini هیڅ کیلي نه ده تنظیم شوې (GEMINI_API_KEYS).")
    reports = [t.strip() for t in texts if t.strip()]
    raw = await _main_extract(reports, config)
    titles: dict = {}
    for lang in ("ps", "fa", "en"):
        para = (raw.get(f"text_{lang}") or "").strip()
        if not para:
            continue
        para = para.split("\n")[0].strip()
        _, title = _split_source_title(para)
        if title:
            titles[lang] = title
    return titles


async def analyze(texts: list[str], config: Config) -> dict:
    """بشپړ تحلیل — سرلیک + سرچینه + موقعیت، **په یوه کال کې**.

    ⚠️ پخوا مو څلور کالونه کول: ۱ (سرلیک/موقعیت) + ۳ (هرې ژبې سرچینه). دا هم
    وخت خوري او هم د وړیا کوټې څلور برخې. اوس ټول یوه ګډه JSON ده.

    سرچینه په درې پوړیز ترتیب: (۱) د Gemini خپله سرچینه، (۲) د سرلیک د «:»
    مخکینۍ برخه، (۳) د ژبې ډیفالټ. نو هیڅکله تشه نه پاتې کیږي — او هیڅ اضافي
    کال نه غواړي.
    """
    if not config.gemini_api_keys:
        raise RuntimeError("د Gemini هیڅ کیلي نه ده تنظیم شوې (GEMINI_API_KEYS).")

    reports = [t.strip() for t in texts if t.strip()]
    raw = await _main_extract(reports, config)
    result: dict = {"location": _normalize_location(raw.get("location", ""))}

    present: dict = {}
    for lang in ("ps", "fa", "en"):
        para = (raw.get(f"text_{lang}") or "").strip()
        # اطمیناني: که Gemini د بدن متن هم راولیږه (د line-break سره)، یوازې لومړی
        # لاین (سرلیک) واخله — دا د سرلیک بدلون نه دی، یوازې د اضافي بدن پرې کول.
        if para:
            para = para.split("\n")[0].strip()
        colon_src, title = _split_source_title(para)
        result[f"title_{lang}"] = title
        if not para:
            result[f"source_{lang}"] = ""
            continue
        present[lang] = para
        src = _clean_source(raw.get(f"source_{lang}") or "") or colon_src
        result[f"source_{lang}"] = src or _DEFAULT_SOURCE[lang]

    verify_result(result, present)
    return result
