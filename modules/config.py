"""د تنظیماتو لوستل — settings.json + د .env پټ معلومات.

Configuration loading. Structural settings come from ``config/settings.json``
while secrets (bot token, group id, Gemini keys, admins) are read from
environment variables / ``.env`` so they are never committed to git.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DesignConfig:
    """د ډیزاین (Pillow) تنظیمات — ټول عددونه له settings.json.

    Design/rendering knobs. Every number is config-driven so the look can be
    tuned without touching code (see spec §14).
    """

    assets_dir: str = "assets"
    title_font: str = "FModam-FBlack[@mimvid].ttf"
    source_font: str = "FModam-FRegular[@mimvid].ttf"

    canvas_size: int = 1650
    photo_width: int = 1650
    photo_height: int = 1309
    photo_fit: str = "stretch"  # "stretch" | "cover"

    title_target_size: int = 72
    title_normal_min: int = 65
    title_normal_max: int = 79
    title_extended_min: int = 58  # ⚠️ یوازې انګلیسي
    title_valign: str = "top"     # "top" | "middle"
    # د عنوان د لاینونو ترمنځ فاصله (px) — د فونټ طبیعي لوړوالي سربېره.
    title_line_gap: int = 15

    source_size: int = 49
    source_min_size: int = 24
    source_color_hex: str = "FEB407"
    # د سرچینې وروسته نښه (لکه «:»)؛ تش "" یې بندوي.
    source_suffix: str = ":"

    highlight_color_hex: str = "FEA20E"
    highlight_fraction: float = 0.45
    normal_color_hex: str = "FFFFFF"

    # د خروجي کیفیت — نږدې بې-تاوانه (FullHD)، optimize + progressive حجم کموي.
    jpeg_quality_max: int = 96
    jpeg_quality_min: int = 92
    target_kb: int = 1600

    progress_min_interval: float = 1.0

    @property
    def fonts_dir(self) -> str:
        return f"{self.assets_dir}/fonts"

    @property
    def templates_dir(self) -> str:
        return f"{self.assets_dir}/templates"

    @property
    def locations_dir(self) -> str:
        return f"{self.assets_dir}/locations"


@dataclass
class Config:
    """د رباټ ټول تنظیمات په یوه ځای کې.  All bot settings in one object."""

    bot_token: str = ""
    group_id: int = 0
    admin_user_ids: list[int] = field(default_factory=list)
    packages_dir: str = "data/packages"
    pending_dir: str = "data/pending"

    gemini_api_keys: list[str] = field(default_factory=list)
    # د مډلونو ترتیب لښتی — له تر ټولو قوي څخه ضعیف. cascade **یوازې** دا ترتیب کاروي.
    # Fixed, ordered model list (strongest first). No other models are used.
    models: list[str] = field(default_factory=lambda: [
        "gemini-3.5-flash",
        "gemini-3.6-flash",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemma-4-31b-it",
        "gemma-4-26b-a4b-it",
    ])

    max_items: int = 4
    max_images: int = 1
    max_texts: int = 3

    reaction_sequence: list[str] = field(default_factory=lambda: ["1️⃣", "2️⃣", "3️⃣", "4️⃣"])
    reaction_fallback: list[str] = field(default_factory=lambda: ["👍", "❤️", "🔥", "🎉"])
    reaction_reject: str = "❌"
    reaction_clean: str = "🗑"

    design: DesignConfig = field(default_factory=DesignConfig)

    @property
    def gemini_models(self) -> list[str]:
        """د cascade لپاره د مډلونو ترتیب (قوي → ضعیف)."""
        return list(self.models)


def _split_csv_ints(value: str) -> list[int]:
    return [int(x) for x in value.replace(" ", "").split(",") if x]


def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def load_config(settings_path: str | Path = "config/settings.json") -> Config:
    """settings.json ولوله، بیا د چاپیریال متغیرونو څخه پټ معلومات ور برسیره کړه.

    Load ``settings.json`` and overlay secrets from the environment. Env vars
    always win so a committed ``settings.json`` can safely keep blanks.
    """
    # .env که موجود وي، ولوله (اختیاري dependency).
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # pragma: no cover - dotenv اختیاري دی
        pass

    data: dict = {}
    path = Path(settings_path)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))

    tg = data.get("telegram", {})
    gm = data.get("gemini", {})
    pk = data.get("package", {})
    rx = data.get("reactions", {})
    dz = data.get("design", {})

    cfg = Config(
        bot_token=tg.get("bot_token", ""),
        group_id=int(tg.get("group_id", 0) or 0),
        admin_user_ids=list(tg.get("admin_user_ids", []) or []),
        packages_dir=tg.get("packages_dir", "data/packages"),
        pending_dir=tg.get("pending_dir", "data/pending"),
        gemini_api_keys=list(gm.get("api_keys", []) or []),
        max_items=int(pk.get("max_items", 4)),
        # NOTE: `models` filled below (only if settings.json provides a list).
        max_images=int(pk.get("max_images", 1)),
        max_texts=int(pk.get("max_texts", 3)),
    )
    # د مډلونو لښتی یوازې که settings.json یې ورکړي (نه‌نو د ثابت default).
    if gm.get("models"):
        cfg.models = list(gm["models"])
    if rx.get("sequence"):
        cfg.reaction_sequence = list(rx["sequence"])
    if rx.get("fallback"):
        cfg.reaction_fallback = list(rx["fallback"])
    cfg.reaction_reject = rx.get("reject", cfg.reaction_reject)
    cfg.reaction_clean = rx.get("clean", cfg.reaction_clean)

    # ---- د ډیزاین برخه (یوازې هغه کیلیانې چې موجودې دي، ورکوو) ----
    known = {f.name for f in DesignConfig.__dataclass_fields__.values()}
    cfg.design = DesignConfig(**{k: v for k, v in dz.items() if k in known})

    # ---- د چاپیریال (env / .env) پټ معلومات ور برسیره کول ----
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg.bot_token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    if os.environ.get("TELEGRAM_GROUP_ID"):
        cfg.group_id = int(os.environ["TELEGRAM_GROUP_ID"].strip())
    if os.environ.get("ADMIN_USER_IDS"):
        cfg.admin_user_ids = _split_csv_ints(os.environ["ADMIN_USER_IDS"])
    if os.environ.get("GEMINI_API_KEYS"):
        cfg.gemini_api_keys = _split_csv(os.environ["GEMINI_API_KEYS"])

    return cfg
