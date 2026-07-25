"""د Gemini تحلیل — د راپورونو سرلیک/سرچینه راویستل + cascade fallback.

Gemini analysis. Builds the strict "verbatim title / source split" prompt,
calls Gemini with a cascade across keys and models (retrying on 503/UNAVAILABLE),
and parses the JSON reply robustly.
"""

from __future__ import annotations

import json
import logging
import re

from .config import Config

logger = logging.getLogger("gemini")

# د تحلیل لارښود — Gemini یوازې کټ مټ پاراګراف راوباسي؛ بیلول په کوډ کې کیږي.
PROMPT_TEMPLATE = """تاسو د یوې خبري ادارې مرستیال یاست. لاندې تر دریو راپورونه درکړل \
شوي دي (کیدای شي پښتو، فارسي او انګلیسي وي).

⚠️ ستا دنده (۱۰۰٪ قلف):
1. د هر راپور **یوازې لومړی پاراګراف = سرلیک** راوباسه (معمولاً لومړی لاین/جمله). \
سرلیک **کټ مټ (VERBATIM)** کاپي کړه — هیڅ بدلون، هیڅ ژباړه.
2. ⚠️ **یوازې سرلیک** راوباسه، **نه** د راپور بشپړ متن او **نه** د بدن (body) نور \
پاراګرافونه. که سرلیک اوږد وي هم، یوازې لومړی پاراګراف راوباسه.
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


def build_prompt(texts: list[str]) -> str:
    """د راپورونو لیست د تحلیل پرامپټ ته واړوه.  Build the analysis prompt."""
    numbered = "\n\n".join(f"[راپور {i}]\n{t}" for i, t in enumerate(texts, 1))
    return PROMPT_TEMPLATE.format(reports=numbered)


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
    last_error: Exception | None = None

    for model in config.gemini_models:
        for key_idx, client in clients:
            try:
                resp = await client.aio.models.generate_content(model=model, contents=prompt)
                if resp.text and resp.text.strip():
                    logger.info("Gemini بریالی: %s / کیلي #%d", model, key_idx)
                    return resp.text
                raise RuntimeError("خالي ځواب")
            except Exception as err:  # noqa: BLE001 - cascade په قصد پراخ دی
                last_error = err
                reason = "کوټه/لیمیټ" if _is_quota(err) else "خطا"
                logger.warning("Gemini %s (%s / کیلي #%d): %s — بل ته ځم", reason, model, key_idx, err)
                continue

    raise RuntimeError(f"ټول مډلونه/کیلیانې ناکام شول: {last_error}")


# د منبع ډیفالټ (rule 8) — کله چې هیڅ رسنۍ/ارګان ونه موندل شي.
_DEFAULT_SOURCE = {"ps": "راپورونه", "fa": "گزارش‌ها", "en": "Reports"}

_VALID_LOCATIONS = ("Afghanistan", "Region", "Middle East", "World")

# د پښتو ځانګړي توري چې فارسي یې نه کاروي — د ژبې پیژندنې لپاره.
_PASHTO_MARKERS = "ښږڅځډړټڼ"

# د اصلي (main) کال د بیا-هڅې اعظمي شمیر — چې کومه ژبه بې سرلیکه پاتې نشي.
_MAX_MAIN_ATTEMPTS = 3


def detect_language(text: str) -> str | None:
    """د متن ژبه په کوډ کې وپیژنه: en / ps / fa (یا None که تش وي)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    if sum(1 for c in letters if c.isascii()) / len(letters) > 0.5:
        return "en"
    if any(c in _PASHTO_MARKERS for c in text):
        return "ps"
    return "fa"


def first_paragraph(text: str) -> str:
    """د راپور لومړی ناخالي لاین (سرلیک) — د fallback لپاره کټ مټ."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return text.strip()


def map_reports(texts: list[str]) -> dict:
    """هر راپور خپلې ژبې ته وټاکه — {lang: raw_text}. (یو راپور فی ژبه)."""
    per: dict = {}
    for t in texts:
        t = t.strip()
        if not t:
            continue
        lang = detect_language(t)
        if lang and lang not in per:
            per[lang] = t
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


async def analyze(texts: list[str], config: Config) -> dict:
    """بشپړ تحلیل — سرلیک (کټ مټ، یوازې د Gemini لومړی پاراګراف) + موقعیت + سرچینه.

    Titles come only from Gemini's verbatim first paragraph (with retry so a
    one-off omission self-heals); a language with no paragraph is simply left
    out (its image is skipped). Source uses the dedicated prompt with the
    per-language default. verify_result logs any anomaly before returning.
    """
    if not config.gemini_api_keys:
        raise RuntimeError("د Gemini هیڅ کیلي نه ده تنظیم شوې (GEMINI_API_KEYS).")

    reports = [t.strip() for t in texts if t.strip()]
    raw = await _main_extract(reports, config)
    result: dict = {"location": _normalize_location(raw.get("location", ""))}

    present: dict = {}
    for lang in ("ps", "fa", "en"):
        para = (raw.get(f"text_{lang}") or "").strip()
        _, title = _split_source_title(para)
        result[f"title_{lang}"] = title
        if not para:
            result[f"source_{lang}"] = ""
            continue
        present[lang] = para
        # سرچینه د بشپړ راپور څخه (اختصاصي پرامپټ) + د ژبې ډیفالټ که ونه موندل شي.
        full = _match_full_text(para, reports)
        try:
            src = await extract_source(full, config)
        except Exception as err:  # noqa: BLE001 - سرچینه اختیاري ده
            logger.warning("[%s] د سرچینې استخراج ناکام: %s", lang, err)
            src = ""
        result[f"source_{lang}"] = src or _DEFAULT_SOURCE[lang]

    verify_result(result, present)
    return result
