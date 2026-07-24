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
    # د سرچینې د بیلولو خوندي شبکه (که Gemini یې نه وي بیله کړې).
    return ensure_sources(data)


def _split_source_title(title: str) -> tuple[str, str]:
    """که سرلیک د پیل نږدې «:» ولري، (سرچینه, سرلیک) بیل کړه؛ نه‌نو ("", سرلیک).

    Split "الجزیره: خبر" into ("الجزیره:", "خبر"). Only splits when the colon is
    near the start (a source is short), so mid-sentence colons are untouched.
    """
    positions = [p for p in (title.find(":"), title.find("：")) if p != -1]
    if not positions:
        return "", title
    idx = min(positions)
    if idx == 0 or idx > 40:  # colon د پیل کې یا ډیر لرې → سرچینه نه ده
        return "", title
    source = title[: idx + 1].strip()
    rest = title[idx + 1 :].strip()
    if not rest:  # colon په پای کې — سرچینه نه ده
        return "", title
    return source, rest


def ensure_sources(data: dict) -> dict:
    """د هرې ژبې لپاره: که سرچینه خالي وي خو سرلیک «:» ولري، په کوډ کې یې بیل کړه.

    Guarantees the source appears whenever the title carries a "source:" prefix,
    even if the model left ``source_*`` empty (spec §6 rule 3).
    """
    for lang in ("ps", "fa", "en"):
        src = (data.get(f"source_{lang}") or "").strip()
        title = (data.get(f"title_{lang}") or "").strip()
        if not src and title:
            new_src, new_title = _split_source_title(title)
            if new_src:
                data[f"source_{lang}"] = new_src
                data[f"title_{lang}"] = new_title
    return data


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
