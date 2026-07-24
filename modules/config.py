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
    line_spacing: float = 1.45

    source_size: int = 35
    source_min_size: int = 24

    highlight_color_hex: str = "FEA20E"
    highlight_fraction: float = 0.45
    normal_color_hex: str = "FFFFFF"

    jpeg_quality_max: int = 95
    jpeg_quality_min: int = 82
    target_kb: int = 950

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
    # د مډلونو ترتیب لښتی — له تر ټولو قوي څخه ضعیف. cascade همدا ترتیب کاروي.
    # A strongest-first list of models; the cascade walks it in order.
    models: list[str] = field(default_factory=lambda: ["gemini-2.5-flash", "gemini-2.5-flash-lite"])
    # پخوانۍ (backward-compat) — که `models` تش وي کارول کیږي.
    primary_model: str = "gemini-2.5-flash"
    fallback_model: str = "gemini-2.5-flash-lite"

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
        if self.models:
            return self.models
        models = [self.primary_model]
        if self.fallback_model and self.fallback_model != self.primary_model:
            models.append(self.fallback_model)
        return models


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
        models=list(gm.get("models", []) or []),
        primary_model=gm.get("primary_model", "gemini-2.5-flash"),
        fallback_model=gm.get("fallback_model", "gemini-2.5-flash-lite"),
        max_items=int(pk.get("max_items", 4)),
        max_images=int(pk.get("max_images", 1)),
        max_texts=int(pk.get("max_texts", 3)),
    )
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
