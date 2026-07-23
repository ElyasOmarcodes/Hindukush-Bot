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

# د تحلیل لارښود — قواعد ۱۰۰٪ قلف دي (سرلیک کټ مټ کاپي).
PROMPT_TEMPLATE = """تاسو د یوې خبري ادارې تحلیلګر یاست. لاندې راپورونه درکړل شوي دي \
(کیدای شي پښتو، فارسي او انګلیسي وي). د هر یوه لپاره سرلیک او سرچینه راوباسئ.

⚠️ د سرلیک قواعد (۱۰۰٪ قلف):
1. سرلیک تل د راپور لومړی پاراګراف دی.
2. سرلیک باید VERBATIM (کټ مټ) کاپي شي — هیڅ بدلون، هیڅ لنډول، هیڅ بیا لیکل.
3. استثنا: که په لومړي پاراګراف کې د «:» نښه وي، نو تر «:» مخکې برخه = سرچینه، \
تر «:» وروسته برخه = سرلیک.
   بېلګه: "الجزیره: بمبار پیښه وشوه" → source = "الجزیره:"، title = "بمبار پیښه وشوه".
4. که «:» نه وي، ټول لومړی پاراګراف = سرلیک، سرچینه = خالي رشته.
5. که کوم ژبنی راپور نه وي ورکړل شوی، د هغې ژبې سرلیک او سرچینه خالي رشته "" کړه.

د موقعیت (location) ټاکل: د محتوا پر بنسټ یو وټاکه:
"Afghanistan" | "Region" | "Middle East" | "World".

یوازې JSON راوګرځوه، پرته له کوم markdown یا تشریح:
{{
  "title_ps": "...", "source_ps": "...",
  "title_fa": "...", "source_fa": "...",
  "title_en": "...", "source_en": "...",
  "location": "Middle East"
}}

راپورونه:
{reports}
"""

_EXPECTED_KEYS = (
    "title_ps", "source_ps", "title_fa", "source_fa",
    "title_en", "source_en", "location",
)


def build_prompt(texts: list[str]) -> str:
    """د راپورونو لیست د تحلیل پرامپټ ته واړوه.  Build the analysis prompt."""
    numbered = "\n\n".join(f"[راپور {i}]\n{t}" for i, t in enumerate(texts, 1))
    return PROMPT_TEMPLATE.format(reports=numbered)


def extract_json(text: str) -> dict:
    """د Gemini د ځواب څخه JSON راوباسه، حتی که ```json حصار ولري.

    Extract the JSON object from a model reply, tolerating ```json fences and
    surrounding prose. Missing keys are filled with empty strings.
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
    # ډاډ ترلاسه کړه چې ټولې تمه شوې کیلیانې شته.
    for key in _EXPECTED_KEYS:
        data.setdefault(key, "")
    return data


def _is_retryable(err: Exception) -> bool:
    msg = str(err).upper()
    return any(tok in msg for tok in ("503", "UNAVAILABLE", "OVERLOAD", "429", "RESOURCE_EXHAUSTED"))


async def analyze(texts: list[str], config: Config) -> dict:
    """راپورونه Gemini ته واستوه او پارس شوې پایله راوګرځوه.

    Send reports to Gemini and return the parsed result dict. Cascades across
    all configured keys and models, retrying only on transient errors.
    """
    if not config.gemini_api_keys:
        raise RuntimeError("د Gemini هیڅ کیلي نه ده تنظیم شوې (GEMINI_API_KEYS).")

    # وارد کول دلته کوو چې د یونټ-ټیسټ پر مهال google-genai ته اړتیا نه وي.
    from google import genai

    prompt = build_prompt(texts)
    last_error: Exception | None = None

    for key_idx, api_key in enumerate(config.gemini_api_keys, 1):
        client = genai.Client(api_key=api_key)
        for model in config.gemini_models:
            try:
                logger.info("Gemini هڅه: کیلي #%d / %s", key_idx, model)
                resp = await client.aio.models.generate_content(
                    model=model, contents=prompt
                )
                data = extract_json(resp.text)
                logger.info("Gemini بریالی: کیلي #%d / %s", key_idx, model)
                return data
            except Exception as err:  # noqa: BLE001 - cascade په قصد پراخ دی
                last_error = err
                if _is_retryable(err):
                    logger.warning(
                        "Gemini لنډمهاله خطا (کیلي #%d / %s): %s — بل ته ځم",
                        key_idx, model, err,
                    )
                    continue
                logger.error("Gemini د تحلیل خطا: %s", err)
                raise

    raise RuntimeError(f"ټولې کیلیانې/مډلونه ناکام شول: {last_error}")
