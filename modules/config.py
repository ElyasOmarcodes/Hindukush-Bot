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

    # ⚠️ سایز **پایله** ده، نه قانون. اصلي قانون دا دی چې هر نا-وروستی لاین
    # ≥۹۰٪ ډک وي (وګوره `_MIN_LINE_FILL`)؛ له هغو سایزونو چې دا شرط پوره کوي،
    # تر ټولو لوی اخیستل کیږي. د ادارې لاسي نمونې ۵۴–۸۴px کاروي، نو بند پراخ دی.
    title_min_size: int = 45      # د لټون کوچنی حد
    title_max_size: int = 85      # د لټون لوی حد (لاسي نمونه ۸۴ لري)
    title_target_size: int = 70   # یوازې د «هیڅ پاک سایز نشته» حالت لپاره
    title_normal_min: int = 63    # (زوړ — نور نه کارول کیږي)
    title_normal_max: int = 77    # (زوړ — نور نه کارول کیږي)
    title_extended_min: int = 58  # (زوړ)
    title_fill_headroom: int = 0  # (زوړ)
    # مطلق کوچنی حد — که عنوان اوږد وي، سایز تر دې پورې کوچنی کیږي چې ټول ځای شي
    # (هیڅکله کلیپ نه کوو — تل ټول عنوان ښکاره کوو).
    title_abs_min: int = 20
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

    # د وروستي خروجي اندازه (px) — دواړه خبر او عاجل دې اندازې ته resize کیږي.
    output_size: int = 1920
    # د خروجي کیفیت — نږدې بې-تاوانه (FullHD)، optimize + progressive حجم کموي.
    jpeg_quality_max: int = 96
    jpeg_quality_min: int = 92
    target_kb: int = 1600

    progress_min_interval: float = 1.0

    # ─────────── د عاجل خبر ډیزاین (بېل ټمپلیټ، یوازې عنوان + تاریخ) ───────────
    urgent_canvas_size: int = 1561
    urgent_title_font: str = "FModam-FBold[@mimvid].ttf"
    urgent_title_size: int = 80
    urgent_title_color_hex: str = "FFFFFF"
    urgent_highlight_color_hex: str = "DA0404"
    urgent_highlight_fraction: float = 0.40  # اخیري ۳۵–۴۵٪
    urgent_date_font: str = "couriernew.ttf"
    urgent_date_size: int = 54
    urgent_date_color_hex: str = "DA0404"

    # ─────────── د نقل قول ډیزاین (شخص انځور + نقل قول + نوم + رتبه) ───────────
    quote_canvas_size: int = 1353
    quote_title_font: str = "FModam-FBold[@mimvid].ttf"
    quote_title_size: int = 65
    # که پخپله ډیفالټ سایز پاک نه و (تشه یا زیاتې کشیدې غواړي)، دومره پورته هم
    # وکتل شي. مقصد **ډیفالټ ته نږدې** پاتې کېدل دی — نه تر ډیفالټه ډیر لوی متن.
    quote_fill_headroom: int = 10
    quote_title_color_hex: str = "FFFFFF"
    quote_highlight_color_hex: str = "FEA20E"   # لکه د خبر عنوان هایلایټ
    quote_highlight_fraction: float = 0.40      # اخیري ۳۵–۴۵٪
    # که نقل قول د باکس تر دې برخې زیات ډک کړي → پورته پیل؛ نه‌نو د پورته/مرکز منځ.
    quote_title_fill_threshold: float = 0.75
    quote_name_font: str = "FModam-FBold[@mimvid].ttf"
    quote_name_size: int = 35
    quote_name_color_hex: str = "D8DFE7"
    quote_rank_font: str = "FModam-FRegular[@mimvid].ttf"
    quote_rank_size: int = 40
    quote_rank_color_hex: str = "FEB407"

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
    # د نېټې لپاره وخت-سیمه (عاجل خبر). سرور په UTC چلیږي، خبرونه په کابل وخت.
    timezone: str = "Asia/Kabul"

    gemini_api_keys: list[str] = field(default_factory=list)
    # د یوه Gemini کال اعظمي وخت (ثانیې). بریالي کالونه ۲.۵–۵.۰ ثانیې نیسي،
    # نو ۱۲ دوه‌برابره ځای ورکوي. که وخت تېر شي، **ټول مډل** پریږدو (ورووالی د
    # سرویس حالت دی، نه د کیلي) او سمدلاسه بل مډل ته ځو.
    gemini_timeout: float = 12.0
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
    # `.env` یا `env` که موجود وي، ولوله (اختیاري dependency).
    # `env.example` یوازې نمونه ده — هیڅکله نه لوستل کیږي.
    try:
        from dotenv import load_dotenv

        for candidate in (".env", "env"):
            if Path(candidate).is_file():
                load_dotenv(candidate)
                break
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
        timezone=data.get("timezone", "Asia/Kabul"),
        gemini_api_keys=list(gm.get("api_keys", []) or []),
        gemini_timeout=float(gm.get("timeout", 12.0)),
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
    if os.environ.get("TIMEZONE"):
        cfg.timezone = os.environ["TIMEZONE"].strip()
    if os.environ.get("ADMIN_USER_IDS"):
        cfg.admin_user_ids = _split_csv_ints(os.environ["ADMIN_USER_IDS"])
    if os.environ.get("GEMINI_API_KEYS"):
        cfg.gemini_api_keys = _split_csv(os.environ["GEMINI_API_KEYS"])

    return cfg
