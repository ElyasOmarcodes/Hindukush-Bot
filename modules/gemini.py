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
1. د هر راپور **لومړی پاراګراف** (سرلیک) **کټ مټ (VERBATIM)** راوباسه — هیڅ بدلون، هیڅ \
لنډول، هیڅ بیا لیکل، هیڅ ژباړه.
2. ⚠️ **ډیر مهم:** که د پاراګراف په پیل کې د سرچینې نوم وي (لکه «الجزیره:» یا «BBC:»)، \
هغه **هرومرو ورسره ونیسه** — مه یې غورځوه او مه یې جلا کوه. زه به یې پخپله بیل کړم.
3. ژبې: `text_ps` = د پښتو راپور لومړی پاراګراف، `text_fa` = فارسي، `text_en` = انګلیسي.
4. که کومه ژبه نه وي ورکړل شوې، هغه ساحه خالي رشته "" پریږده.

د موقعیت (location) ټاکل: د محتوا پر بنسټ یو وټاکه:
"Afghanistan" | "Region" | "Middle East" | "World".

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
    if not text:
        raise ValueError("د Gemini ځواب خالي دی")
    cleaned = text.strip()
    # د markdown کوډ-حصار لرې کول.
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    # لومړی { … وروستی } راواخله.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    data = json.loads(cleaned)
    return build_result(data)


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


async def analyze(texts: list[str], config: Config) -> dict:
    """راپورونه Gemini ته واستوه — د قوت پر بنسټ cascade.

    Cascade order (spec): strongest model first. For each model try key #1 then
    key #2 (…), then drop to the next model. Any failure — quota or otherwise —
    silently advances to the next (model, key) pair; only if *every* pair fails
    do we raise. This keeps fallback invisible to the user.
    """
    if not config.gemini_api_keys:
        raise RuntimeError("د Gemini هیڅ کیلي نه ده تنظیم شوې (GEMINI_API_KEYS).")

    # وارد کول دلته کوو چې د یونټ-ټیسټ پر مهال google-genai ته اړتیا نه وي.
    from google import genai

    prompt = build_prompt(texts)
    clients = [(i + 1, genai.Client(api_key=k)) for i, k in enumerate(config.gemini_api_keys)]
    last_error: Exception | None = None

    # بهرنۍ حلقه = مډل (له قوي څخه ضعیف)، دنننۍ حلقه = کیلي.
    for model in config.gemini_models:
        for key_idx, client in clients:
            try:
                logger.info("Gemini هڅه: %s / کیلي #%d", model, key_idx)
                resp = await client.aio.models.generate_content(model=model, contents=prompt)
                data = extract_json(resp.text)
                logger.info("Gemini بریالی: %s / کیلي #%d", model, key_idx)
                return data
            except Exception as err:  # noqa: BLE001 - cascade په قصد پراخ دی
                last_error = err
                reason = "کوټه/لیمیټ" if _is_quota(err) else "خطا"
                logger.warning("Gemini %s (%s / کیلي #%d): %s — بل ته ځم", reason, model, key_idx, err)
                continue

    raise RuntimeError(f"ټول مډلونه/کیلیانې ناکام شول: {last_error}")
