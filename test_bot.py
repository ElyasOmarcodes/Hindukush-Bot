"""د NewsBot یونټ-ټیسټونه (بې شبکې).

Unit tests for NewsBot. None of these contact Telegram or Gemini — they check
the pure logic: filename sanitizing, config loading/overlay, buffer validation
and limits, prompt building and JSON extraction.
"""

import json
import os
from pathlib import Path

import pytest

from modules import gemini
from modules.config import load_config
from modules.package import (
    BotState,
    BufferItem,
    can_add,
    collect_texts,
    validate_package,
)
from modules.utils import escape_markdown, sanitize_filename, timestamp_folder


# ---------- utils ----------

def test_sanitize_removes_hidden_unicode():
    dirty = "خبر‌‍مهم/بد:نوم"
    clean = sanitize_filename(dirty)
    assert "‌" not in clean and "‍" not in clean
    assert "/" not in clean and ":" not in clean


def test_sanitize_empty_fallback():
    assert sanitize_filename("") == "untitled"
    assert sanitize_filename("///") == "untitled"


def test_escape_markdown():
    assert escape_markdown("a*b_c") == "a\\*b\\_c"


def test_timestamp_folder_format():
    folder = timestamp_folder()
    # 2026-06-27_1530.. → لومړي ۱۰ توري نیټه ده
    assert folder[4] == "-" and "_" in folder


# ---------- config ----------

def test_config_env_overlay(monkeypatch, tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text('{"telegram": {"bot_token": ""}}', encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "ENV-TOKEN")
    monkeypatch.setenv("TELEGRAM_GROUP_ID", "-100123")
    monkeypatch.setenv("GEMINI_API_KEYS", "k1, k2 ,k3")
    monkeypatch.setenv("ADMIN_USER_IDS", "10,20")
    cfg = load_config(settings)
    assert cfg.bot_token == "ENV-TOKEN"
    assert cfg.group_id == -100123
    assert cfg.gemini_api_keys == ["k1", "k2", "k3"]
    assert cfg.admin_user_ids == [10, 20]


def test_config_defaults_when_missing(tmp_path):
    cfg = load_config(tmp_path / "does_not_exist.json")
    assert cfg.max_items == 4
    # د ثابت لښت لومړی (قوي) مډل.
    assert cfg.gemini_models[0] == "gemini-3.5-flash"
    assert cfg.gemini_models[-1] == "gemma-4-26b-a4b-it"
    assert len(cfg.gemini_models) == 8


# ---------- package: limits & validation ----------

def _state():
    return BotState(config=load_config("config/settings.json"))


def test_can_add_respects_text_limit():
    state = _state()
    for i in range(3):
        state.buffer.append(BufferItem(type="text", message_id=i, content=f"t{i}"))
    ok, reason = can_add(state, "text")
    assert not ok and "متن" in reason


def test_can_add_respects_image_limit():
    state = _state()
    state.buffer.append(BufferItem(type="image", message_id=1, path="x.jpg"))
    ok, _ = can_add(state, "image")
    assert not ok


def test_validate_requires_text():
    state = _state()
    state.buffer.append(BufferItem(type="image", message_id=1, path="x.jpg"))
    errors = validate_package(state)
    assert any("متني" in e for e in errors)


def test_validate_ok():
    state = _state()
    state.buffer.append(BufferItem(type="image", message_id=1, path="x.jpg"))
    state.buffer.append(BufferItem(type="text", message_id=2, content="سرلیک"))
    assert validate_package(state) == []


def test_listening_off_by_default():
    state = _state()
    assert state.listening is False


def test_reset_keeps_listening_flag():
    state = _state()
    state.listening = True
    state.buffer.append(BufferItem(type="text", message_id=1, content="x"))
    state.reset()
    assert state.buffer == []
    # /cmp دوره باید رباټ کاڼه نه کړي — listening باید پاتې شي.
    assert state.listening is True


def test_collect_texts_ordered():
    state = _state()
    state.buffer.append(BufferItem(type="text", message_id=1, content="A"))
    state.buffer.append(BufferItem(type="image", message_id=2, path="x.jpg"))
    state.buffer.append(BufferItem(type="text", message_id=3, content="B"))
    assert collect_texts(state) == ["A", "B"]


# ---------- gemini: prompt & parsing ----------

def test_build_prompt_contains_reports():
    prompt = gemini.build_prompt(["راپور یو", "report two"])
    assert "راپور یو" in prompt and "report two" in prompt
    assert "JSON" in prompt


def test_extract_json_plain():
    data = gemini.extract_json('{"title_ps": "س", "location": "World"}')
    assert data["title_ps"] == "س"
    assert data["source_en"] == ""  # ورک کیلیانې خالي ډکیږي


def test_extract_json_with_fences():
    raw = '```json\n{"location": "Region"}\n```'
    data = gemini.extract_json(raw)
    assert data["location"] == "Region"


def test_extract_json_empty_raises():
    with pytest.raises(ValueError):
        gemini.extract_json("")


def test_ensure_sources_splits_colon_from_title():
    # Gemini سرچینه خالي پریښوده خو سرلیک «:» لري → کوډ یې بیلوي.
    data = {"title_ps": "الجزیره: بمبار پیښه وشوه", "source_ps": ""}
    out = gemini.ensure_sources(data)
    assert out["source_ps"] == "الجزیره:"
    assert out["title_ps"] == "بمبار پیښه وشوه"


def test_ensure_sources_keeps_existing_source():
    data = {"title_en": "Big news here", "source_en": "Reuters:"}
    out = gemini.ensure_sources(data)
    assert out["source_en"] == "Reuters:"
    assert out["title_en"] == "Big news here"


def test_ensure_sources_ignores_midsentence_colon():
    # د پیل نه لرې «:» باید سرچینه ونه ګڼل شي.
    long_title = "a" * 45 + ": rest of a very long sentence here"
    data = {"title_en": long_title, "source_en": ""}
    out = gemini.ensure_sources(data)
    assert out["source_en"] == ""
    assert out["title_en"] == long_title


def test_extract_json_text_format_splits_source():
    # نوی فارمټ: Gemini بشپړ پاراګراف (د سرچینې سره) راوباسي، کوډ یې بیلوي.
    data = gemini.extract_json('{"text_ps": "الجزیره: بمبار پیښه وشوه", "location": "World"}')
    assert data["source_ps"] == "الجزیره:"
    assert data["title_ps"] == "بمبار پیښه وشوه"


def test_extract_json_text_without_source():
    data = gemini.extract_json('{"text_en": "A headline with no source prefix", "location": "World"}')
    assert data["source_en"] == ""
    assert data["title_en"] == "A headline with no source prefix"


def test_split_source_pipe_separator():
    src, title = gemini._split_source_title("BBC | Breaking news today")
    assert src == "BBC"
    assert title == "Breaking news today"


def test_clean_source_strips_labels_and_quotes():
    assert gemini._clean_source("Output: الجزیره") == "الجزیره"
    assert gemini._clean_source('"رویټرز"\nextra line') == "رویټرز"
    assert gemini._clean_source("  باختر آژانس  ") == "باختر آژانس"
    assert gemini._clean_source("") == ""


def test_design_source_settings():
    cfg = load_config("config/settings.json")
    assert cfg.design.source_color_hex == "FEB407"
    assert cfg.design.source_size == 49


def test_source_box_moved_up():
    from modules.design.layout import _SOURCE_BOX_RTL, _SOURCE_BOX_EN
    assert _SOURCE_BOX_RTL[1] == 960  # top
    assert _SOURCE_BOX_EN[1] == 960


def test_langs_use_three_separate_templates():
    from modules.design.layout import LANGS
    tpls = {lg["code"]: lg["template"] for lg in LANGS}
    assert tpls == {"ps": "news_pa.png", "fa": "news_fa.png", "en": "news_en.png"}


def test_default_source_per_language():
    assert gemini._DEFAULT_SOURCE == {"ps": "راپورونه", "fa": "گزارش‌ها", "en": "Reports"}


def test_detect_language():
    assert gemini.detect_language("This is English news") == "en"
    assert gemini.detect_language("د افغانستان په اړه خبر دی ښه") == "ps"  # ښ pashto marker
    assert gemini.detect_language("این یک خبر فارسی است") == "fa"
    assert gemini.detect_language("123 456") is None


def test_first_paragraph():
    assert gemini.first_paragraph("سرلیک دلته\n\nمتن خبر") == "سرلیک دلته"
    assert gemini.first_paragraph("\n\n  Headline  \nbody") == "Headline"


def test_map_reports_one_per_language():
    reports = ["This is English", "دا پښتو خبر دی ښکاره", "این خبر فارسی"]
    per = gemini.map_reports(reports)
    assert per["en"] == "This is English"
    assert "ps" in per and "fa" in per


def test_verify_result_flags_missing_title():
    per = {"ps": "x", "en": "y"}
    result = {"title_ps": "عنوان", "source_ps": "الجزیره", "title_en": "", "source_en": "Reuters", "location": "World"}
    problems = gemini.verify_result(result, per)
    assert any("en" in p and "عنوان" in p for p in problems)


def test_verify_result_clean_passes():
    per = {"ps": "x"}
    result = {"title_ps": "عنوان", "source_ps": "الجزیره", "location": "Afghanistan"}
    assert gemini.verify_result(result, per) == []


def test_normalize_location_invalid_to_world():
    assert gemini._normalize_location("Mars") == "World"
    assert gemini._normalize_location("Afghanistan") == "Afghanistan"


def test_source_prompt_has_new_rules():
    # د ۴ ګونو اصولو شتون: ارګان پر شخص، ډیفالټ راپورونه.
    assert "ORGANIZATION OVER PERSON" in gemini.SOURCE_PROMPT
    assert "راپورونه" in gemini.SOURCE_PROMPT
    assert "گزارش‌ها" in gemini.SOURCE_PROMPT


def test_menu_keyboards_build():
    from modules import commands
    assert commands.BTN_CREATE in commands.BUTTON_LABELS
    # هرې منو باید جوړه شي (استثنا وا نه چوي).
    assert commands.main_keyboard().keyboard
    assert commands.create_keyboard().keyboard
    assert commands.active_keyboard().keyboard


def test_design_line_gap_and_quality():
    cfg = load_config("config/settings.json")
    assert cfg.design.title_line_gap == 15
    assert cfg.design.jpeg_quality_max == 96
    assert cfg.design.jpeg_quality_min == 92


def test_urgent_templates_and_boxes():
    from modules.design.layout import URGENT_TEMPLATES, URGENT_TITLE_BOX, URGENT_DATE_BOX
    assert URGENT_TEMPLATES == {"ps": "Urgent_Pashto.png", "fa": "Urgent_Persian.png", "en": "Urgent_English.png"}
    assert URGENT_TITLE_BOX == (163, 584, 1223, 390)
    assert URGENT_DATE_BOX == (267, 1241, 410, 46)


def test_urgent_config_values():
    cfg = load_config("config/settings.json")
    assert cfg.design.urgent_canvas_size == 1561
    assert cfg.design.urgent_title_size == 80
    assert cfg.design.urgent_highlight_color_hex == "DA0404"
    assert cfg.design.urgent_date_color_hex == "DA0404"


def test_extract_urgent_titles_code_only():
    reports = ["د کابل خبر لومړی ښکاره.\n\nدوهم پاراګراف", "این خبر فارسی است.", "English headline."]
    t = gemini.extract_urgent_titles(reports)
    assert t["ps"] == "د کابل خبر لومړی ښکاره"   # body dropped, period removed
    assert t["fa"] == "این خبر فارسی است"
    assert t["en"] == "English headline"


def test_kashida_justify_fills_width():
    from modules.design.justify import justify_line, TATWEEL
    line = "دا یوه پښتو کرښه ده چې کشیده اخلي"
    # measure = char count as a stand-in for width
    out = justify_line(line, lambda t: len(t), target_w=len(line) + 6)
    assert TATWEEL in out
    assert len(out) >= len(line)


def test_kashida_not_applied_to_english_measure():
    from modules.design.justify import justify_line, TATWEEL
    # لاتین متن کوم slot نلري → بې بدلون
    out = justify_line("English text here", lambda t: len(t), target_w=100)
    assert TATWEEL not in out


def test_kashida_never_breaks_lam_alef():
    """د «ل» او «ا» ترمنځ کشیده منع ده: تلـاش ❌ / تلاش ✅"""
    from modules.design.justify import justify_line, TATWEEL, _can_insert_at
    line = "تلاش تلاش تلاش تلاش"
    for i, ch in enumerate(line):
        if ch == "ل" and i + 1 < len(line) and line[i + 1] in "اآأإٱ":
            assert not _can_insert_at(line, i)
    out = justify_line(line, lambda t: len(t), target_w=len(line) + 20)
    assert "ل" + TATWEEL + "ا" not in out


def test_kashida_uses_every_slot_not_stopping_at_first_overshoot():
    """یو slot چې ډک شي، دوره نه ودریږي — نور slotونه هم ډکیږي (تشه لږیږي)."""
    from modules.design.justify import justify_line
    line = "له اسرائیل سره د امنیتي"
    target = len(line) + 10
    out = justify_line(line, lambda t: len(t), target_w=target)
    # پاتې تشه باید تر ۱ کرکټر زیاته نه وي (پخوا یوځل ماتیدل ډیره تشه پریښوده).
    assert target - len(out) <= 1
    assert len(out) <= target


def test_kashida_cap_grows_until_line_is_full():
    """اولیت د لاین ډکیدل دي — که ۴ کشیده بس نه وې، سقف پورته ځي (۵، ۶، …)."""
    from modules.design.justify import justify_line, PREFERRED_KASHIDA_PER_SLOT, TATWEEL
    # یو slot لرونکې کرښه چې لویه تشه لري → تر «غوره سقف» زیات کشیده اړین دی.
    line = "همه‌جانبه دست"
    target = len(line) + 12
    out = justify_line(line, lambda t: len(t), target_w=target)
    assert out.count(TATWEEL) > PREFERRED_KASHIDA_PER_SLOT
    assert target - len(out) <= 1          # کرښه بشپړه ډکه شوه
    assert out.count(" ") == line.count(" ")  # فاصلې لا هم طبیعي دي


def test_justify_never_stretches_word_spacing():
    """زموږ قانون: د کلمو ترمنځ فاصله هیڅکله نه غزول کیږي — یوازې کشیده."""
    from modules.design.justify import justify_line
    line = "دا یوه پښتو کرښه ده چې کشیده اخلي"
    out = justify_line(line, lambda t: len(t), target_w=len(line) + 25)
    # کلمې هماغه دي، یوازې کشیده ور اضافه شوی؛ د فاصلو شمیر بدل نه دی.
    assert out.count(" ") == line.count(" ")
    assert "  " not in out
    assert [w.replace("ـ", "") for w in out.split()] == line.split()


def test_quote_sizes_from_settings_json():
    """د نقل قول هره برخه خپل سایز په settings.json کې جلا لري."""
    cfg = load_config("config/settings.json")
    assert cfg.design.quote_title_size == 65
    assert cfg.design.quote_name_size == 35
    assert cfg.design.quote_rank_size == 40
    raw = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
    for key in ("quote_title_size", "quote_name_size", "quote_rank_size"):
        assert key in raw["design"], f"{key} باید په settings.json کې جلا درج وي"


def test_pillow_has_raqm():
    """د RTL شکل‌ورکول پر libraqm ولاړ دي — بې هغې هر ډیزاین ماتیږي.

    که Pillow له سرچینې جوړ شي (نه له wheel)، راقم ورک شي او په چلولو کې دا
    خطا راځي: «not supported without libraqm». دا ټیسټ یې مخکې له deploy نیسي.
    """
    from modules.design.text_render import has_raqm
    assert has_raqm(), (
        "Pillow بې libraqm جوړ شوی — د Dockerfile له لارې یې نصب کړه "
        "(pip install --only-binary=Pillow)."
    )


def test_rtl_text_width_works():
    """د پښتو متن عرض واقعاً محاسبه کیږي (د راقم عملي ازموینه)."""
    from pathlib import Path as _P
    from modules.design.text_render import get_font, text_width
    d = load_config("config/settings.json").design
    font = get_font(str(_P(d.fonts_dir) / d.title_font), 60)
    assert text_width(font, "د کابل خبر", False) > 0


def test_long_rtl_filename_is_byte_capped():
    """ext4 حد **په بایټ** دی — پښتو توری ۲ بایټه، نو ۱۵۰ توري = ۳۰۰ بایټه.

    دا هغه ریښتینې ستونزه وه چې د عاجل خبر ډیزاین یې ماتوه (عاجل عنوان بشپړ
    پاراګراف وي → اوږد نوم → OSError → «هیڅ عاجل انځور جوړ نشو»).
    """
    long_fa = ("تلاش می‌کنیم از طریق یک توافق امنیتی با اسرائیل به صلحی همه‌جانبه "
               "دست یابیم و این یک متن طولانی برای آزمایش نام فایل است که باید "
               "بیش از حد مجاز شود و خطا ندهد")
    name = sanitize_filename(long_fa)
    assert len(name.encode("utf-8")) <= 200
    assert name and not name.endswith("_")
    # واقعاً د لیکلو وړ دی (پخوا OSError: File name too long).
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        Path(td, name + ".jpg").write_text("x", encoding="utf-8")


def test_jalali_date_uses_kabul_not_server_time(monkeypatch):
    """⚠️ regression: نېټه باید د **کابل** په وخت وي، نه د سرور (UTC).

    Railway په UTC چلیږي. د کابل د نیمې شپې او ۴:۳۰ سهار ترمنځ، UTC لا هم د
    تیرې ورځې دی — نو `JalaliDate.today()` به نېټه یوه ورځ شاته ښودله.
    """
    from datetime import datetime, timedelta, timezone as _tz
    from persiantools.jdatetime import JalaliDate
    from modules.design import urgent as du

    kabul = _tz(timedelta(hours=4, minutes=30))
    # د کابل ۳:۳۰ سهار = UTC ۲۳:۰۰ د تیرې ورځې.
    moment = datetime(2026, 7, 26, 3, 30, tzinfo=kabul)

    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(du, "datetime", _FakeDT)
    got = du.current_jalali_date("Asia/Kabul")
    want = JalaliDate(moment.date())
    assert got == f"{want.year}/{want.month:02d}/{want.day}"
    # د UTC نېټه به یوه ورځ شاته وای — ډاډ ترلاسه کړه چې هغه نه ده.
    utc_day = JalaliDate(moment.astimezone(_tz.utc).date())
    assert utc_day.day != want.day, "د ټیسټ شېبه باید د ورځې د بدلون په سر کې وي"
    assert got.endswith(f"/{want.day}")


def test_unknown_timezone_falls_back_to_kabul_offset():
    """که tzdata نه وي، UTC+04:30 کارول کیږي — نېټه بیا هم سمه ده."""
    from modules.design import urgent as du
    assert du.current_jalali_date("Not/AZone").count("/") == 2


def test_urgent_language_detection_on_short_reports():
    """عاجل راپورونه لنډ وي — پښتو او فارسي باید سره ګډ نشي."""
    assert gemini.detect_language("دوه تنه ووژل شول") == "ps"
    assert gemini.detect_language("دو نفر کشته شدند") == "fa"
    assert gemini.detect_language("در شهر کابل انفجاری رخ داد") == "fa"
    assert gemini.detect_language("د کابل په ښار کې چاودنه وشوه") == "ps"
    assert gemini.detect_language("Two people were killed") == "en"


def test_map_reports_never_collides_two_reports_into_one_language():
    """دوه راپورونه هیڅکله یوه ژبه نه اخلي — پخوا به دویم چوپ ورک شوی و."""
    out = gemini.map_reports(["دوه تنه ووژل شول", "دو نفر کشته شدند", "Two killed"])
    assert set(out) == {"ps", "fa", "en"}
    assert out["ps"] == "دوه تنه ووژل شول"
    assert out["fa"] == "دو نفر کشته شدند"
    # یو راپور یوازې → یوازې خپله ژبه.
    assert gemini.map_reports(["دو نفر کشته شدند"]) == {"fa": "دو نفر کشته شدند"}
    # درې راپورونه = درې جلا ژبې، هیڅ یو نه ورکیږي.
    three = gemini.map_reports(["د کابل خبر دی", "این خبر کابل است", "Kabul news"])
    assert len(three) == 3


def test_urgent_titles_for_persian_only_package():
    """یوازې فارسي راپور → یوازې فارسي سرلیک (نه ناکامي)."""
    titles = gemini.extract_urgent_titles(
        ["تلاش می‌کنیم از طریق یک توافق امنیتی با اسرائیل به صلحی همه‌جانبه دست یابیم."]
    )
    assert set(titles) == {"fa"}
    assert not titles["fa"].endswith(".")


def test_first_text_message_id_used_for_reply():
    """خروجي فایلونه د پکیج لومړي متني پیغام ته ریپلای کیږي."""
    from modules.package import first_text_message_id
    cfg = load_config("config/settings.json")
    state = BotState(config=cfg)
    state.buffer = [
        BufferItem(type="image", message_id=10, path="x.jpg"),
        BufferItem(type="text", message_id=11, content="پښتو راپور"),
        BufferItem(type="text", message_id=12, content="گزارش فارسی"),
    ]
    assert first_text_message_id(state) == 11
    assert first_text_message_id(BotState(config=cfg)) is None


def test_progress_keeps_checklist_and_shows_summary():
    """ژوندی حالت: چیک‌لیسټ د پای کې **نه پاکیږي**، لنډیز ورسره ښکاري."""
    import asyncio
    from modules.progress import Progress, URGENT_PROGRESS_STEPS

    class _M:
        def __init__(self, i):
            self.message_id = i

    class _Bot:
        def __init__(self):
            self.last = None
            self.sends = 0

        async def send_message(self, cid, text=None, **k):
            self.sends += 1
            self.last = text
            return _M(1)

        async def edit_message_text(self, text=None, **k):
            self.last = text
            return _M(1)

    bot = _Bot()

    async def go():
        p = Progress(bot, 1, min_interval=0.0, steps=URGENT_PROGRESS_STEPS, title="🔴 عاجل خبر")
        await p.start()
        for i in range(len(URGENT_PROGRESS_STEPS)):
            await p.step(i)
        await p.finish(ok=True, summary="📤 *۳* انځورونه ولیږل شول")

    asyncio.run(go())
    assert bot.sends == 1                       # یوازې یو پیغام — هیڅ اضافي
    assert "🔴 عاجل خبر" in bot.last            # سرلیک
    assert "✅ بشپړ شو" in bot.last
    assert "۱۰۰٪" in bot.last                   # پټه + سلنه (پښتو شمېرې)
    assert bot.last.count("✅") >= len(URGENT_PROGRESS_STEPS)  # چیک‌لیسټ پاتې دی
    assert "پښتو ډیزاین" in bot.last and "ګروپ ته لیږل" in bot.last
    assert "۳* انځورونه" in bot.last            # لنډیز


def test_progress_failure_marks_the_failed_step():
    """ناکامي: هغه ګام چې مات شو ❌ نښه کیږي، نور یې ښکاري."""
    import asyncio
    from modules.progress import Progress, URGENT_PROGRESS_STEPS

    class _M:
        def __init__(self, i):
            self.message_id = i

    class _Bot:
        last = None

        async def send_message(self, cid, text=None, **k):
            self.last = text
            return _M(1)

        async def edit_message_text(self, text=None, **k):
            self.last = text
            return _M(1)

    bot = _Bot()

    async def go():
        p = Progress(bot, 1, min_interval=0.0, steps=URGENT_PROGRESS_STEPS)
        await p.start()
        await p.step(3)
        await p.finish(ok=False, err="هیڅ عاجل انځور جوړ نشو")

    asyncio.run(go())
    assert "❌ ناکام" in bot.last
    assert "❌ 🎨 فارسي ډیزاین" in bot.last     # دقیقاً کوم ګام مات شو
    assert "هیڅ عاجل انځور جوړ نشو" in bot.last


def test_progress_never_sleeps_in_the_pipeline():
    """تازه کول کار نه ځنډوي — د پایپ‌لاین دننه هیڅ ویده کیدل نشته."""
    import asyncio
    import time as _t
    from modules.progress import Progress

    class _M:
        def __init__(self, i):
            self.message_id = i

    class _Bot:
        def __init__(self):
            self.edits = 0

        async def send_message(self, cid, text=None, **k):
            return _M(1)

        async def edit_message_text(self, text=None, **k):
            self.edits += 1
            return _M(1)

    bot = _Bot()

    async def go():
        p = Progress(bot, 1, min_interval=5.0)  # لوړ حد
        await p.start()
        for i in range(6):
            await p.step(i)
        await p.finish(ok=True)

    t0 = _t.monotonic()
    asyncio.run(go())
    assert _t.monotonic() - t0 < 1.0   # هیڅ ویده کیدل نشته
    assert bot.edits >= 1              # وروستی حالت تل په زور ښکاري


def test_progress_ticker_keeps_message_alive_during_a_long_step():
    """⚠️ regression: اوږد ګام (جیمنای) کې پیغام باید **جامد نشي**.

    پخوا به ژر تازه‌کول غورځول کیدل، نو د اوږد انتظار پر مهال به پیغام په ۰٪
    ولاړ و او بیا به یې په پای کې ټول ګامونه یو ځل ټک کړل. اوس د شالید ټیکر
    تېر وخت هم ځغلوي، نو د انتظار پر مهال هم حرکت ښکاري.
    """
    import asyncio
    from modules.progress import Progress

    frames = []

    class _M:
        def __init__(self, i):
            self.message_id = i

    class _Bot:
        async def send_message(self, cid, text=None, **k):
            frames.append(text)
            return _M(1)

        async def edit_message_text(self, text=None, **k):
            frames.append(text)
            return _M(1)

    class _Tick(Progress):
        """د ټیسټ لپاره: تېر وخت هره ثانیه بدلیږي — دلته یې ژر بدلوو."""

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._n = 0

        def _render(self, *a, **k):
            self._n += 1
            return super()._render(*a, **k) + f"\n{self._n}"

    async def go():
        p = _Tick(_Bot(), 1, min_interval=0.01)
        p.tick_interval = 0.02
        await p.start()
        await p.step(1)           # جیمنای پیل شو
        await asyncio.sleep(0.3)  # ← اوږد انتظار، هیڅ step نه راځي
        await p.finish(ok=True)

    asyncio.run(go())
    # د انتظار پر مهال ټیکر څو ځله تازه کړی — نه یوازې پیل او پای.
    assert len(frames) >= 5, f"جامد و: یوازې {len(frames)} فریمه"


def test_progress_ticker_is_stopped_after_finish_and_delete():
    """ټیکر باید د دورې وروسته پاتې نشي (شالید کې روان)."""
    import asyncio
    from modules.progress import Progress

    class _M:
        def __init__(self, i):
            self.message_id = i

    class _Bot:
        async def send_message(self, cid, text=None, **k):
            return _M(1)

        async def edit_message_text(self, **k):
            return _M(1)

        async def delete_message(self, **k):
            pass

    async def go():
        p = Progress(_Bot(), 1, min_interval=0.05)
        await p.start()
        assert p._ticker is not None
        await p.finish(ok=True)
        assert p._ticker is None
        p2 = Progress(_Bot(), 1, min_interval=0.05)
        await p2.start()
        await p2.delete()
        assert p2._ticker is None

    asyncio.run(go())


def test_summary_and_missing_note():
    """لنډیز د ژوندي پیغام دننه؛ د نشتې ژبې خبرتیا جلا (دورې وروسته پاتې کیږي)."""
    from modules.commands import _missing_note, _summary
    s = _summary(2, ["ps", "fa"])
    assert "پښتو" in s and "فارسي" in s and "۲" in s

    note = _missing_note(["ps", "fa"])
    assert note and "انګلیسي" in note and "نه و راستولی" in note
    assert _missing_note(["ps", "fa", "en"]) is None


def test_progress_start_sends_no_reply_keyboard():
    """⚠️ regression: ReplyKeyboardMarkup د پیغام د edit کولو مخه نیسي.

    که د پرمختګ پیغام له بوټ-کیبورډ سره ولیږل شي، ټلګرام یې بیا نه تازه کوي او
    ژوندی حالت **جامد** پاتې کیږي. نو start() باید هیڅ reply_markup ونه لیږي.
    """
    import asyncio
    import inspect
    from modules.progress import Progress

    assert "reply_markup" not in inspect.signature(Progress.start).parameters

    seen = {}

    class _M:
        def __init__(self, i):
            self.message_id = i

    class _Bot:
        async def send_message(self, cid, text=None, **k):
            seen.update(k)
            return _M(7)

    async def go():
        await Progress(_Bot(), 1).start()

    asyncio.run(go())
    assert seen.get("reply_markup") is None


def test_progress_delete_removes_live_message():
    """د بریالۍ دورې وروسته ژوندی پیغام ړنګیږي."""
    import asyncio
    from modules.progress import Progress

    deleted = []

    class _M:
        def __init__(self, i):
            self.message_id = i

    class _Bot:
        async def send_message(self, cid, text=None, **k):
            return _M(42)

        async def edit_message_text(self, **k):
            return _M(42)

        async def delete_message(self, chat_id=None, message_id=None):
            deleted.append(message_id)

    async def go():
        p = Progress(_Bot(), 1, min_interval=0.0)
        await p.start()
        await p.delete()
        await p.delete()  # دوهم ځل هیڅ نه کوي

    asyncio.run(go())
    assert deleted == [42]


def test_progress_edit_falls_back_without_markdown():
    """که Markdown پارس ناکام شي، بې فارمټ بیا هڅه کیږي (جامد نه پاتې کیږي)."""
    import asyncio
    from modules.progress import Progress

    tries = []

    class _M:
        def __init__(self, i):
            self.message_id = i

    class _Bot:
        async def send_message(self, cid, text=None, **k):
            return _M(1)

        async def edit_message_text(self, **k):
            tries.append(k.get("parse_mode"))
            if k.get("parse_mode"):
                raise RuntimeError("Can't parse entities")
            return _M(1)

    async def go():
        p = Progress(_Bot(), 1, min_interval=0.0)
        await p.start()
        await p.step(1)

    asyncio.run(go())
    assert tries == ["Markdown", None]


def test_rejected_reaction_emoji_is_not_retried():
    """ټلګرام 1️⃣ نه مني — یو ځل رد شي، بیا یې نه ازمویو (بې‌ګټې کال نشته)."""
    import asyncio
    from modules import monitor

    monitor._REJECTED_EMOJI.clear()
    calls = []

    class _Bot:
        async def set_message_reaction(self, **kw):
            calls.append(kw["reaction"][0].emoji)
            raise RuntimeError("Reaction_invalid")

    class _Ctx:
        bot = _Bot()

    ctx = _Ctx()
    asyncio.run(monitor.react(ctx, 1, 1, "1️⃣"))
    asyncio.run(monitor.react(ctx, 1, 2, "1️⃣"))
    asyncio.run(monitor.react(ctx, 1, 3, "1️⃣"))
    assert len(calls) == 1, "رد شوی ایموجي باید یوازې یو ځل وازمویل شي"
    monitor._REJECTED_EMOJI.clear()


def test_missing_langs_reported():
    """کومې ژبې راپور نه وي راغلی، هغه یې کارونکي ته نوم په نوم وایي."""
    from modules.commands import missing_langs
    assert missing_langs(["ps"]) == ["فارسي", "انګلیسي"]
    assert missing_langs(["ps", "fa", "en"]) == []
    assert missing_langs({"ps": 1, "en": 2}.keys()) == ["فارسي"]


def test_allowlist_comes_from_env_not_settings(monkeypatch):
    """د مجازو لیست په settings.json کې نه — د ADMIN_USER_IDS راز/متغیر څخه راځي."""
    monkeypatch.delenv("ADMIN_USER_IDS", raising=False)
    cfg = load_config("config/settings.json")
    assert cfg.admin_user_ids == []  # تش = هرڅوک (dev)

    monkeypatch.setenv("ADMIN_USER_IDS", "535727838, 5730846799,8416890401")
    cfg = load_config("config/settings.json")
    assert cfg.admin_user_ids == [535727838, 5730846799, 8416890401]


def test_funny_message_has_name():
    from modules import funny
    assert len(funny.FUNNY_MESSAGES) >= 10
    msg = funny.random_message("احمد")
    assert "احمد" in msg


def test_quote_prompt_no_change_and_no_trailing_rules():
    assert "even 1%" in gemini.QUOTE_PROMPT
    assert "NO TRAILING PUNCTUATION" in gemini.QUOTE_PROMPT


def test_quote_prompt_and_parse():
    assert "VERBATIM" in gemini.QUOTE_PROMPT
    assert '"name"' in gemini.QUOTE_PROMPT and '"quote"' in gemini.QUOTE_PROMPT
    # د تمه شوي JSON بڼه پارس کیږي.
    d = gemini._parse_json('{"name": "جوزپ بورېل", "rank": "مسوول", "quote": "خبر دلته دی"}')
    assert d["name"] == "جوزپ بورېل" and d["quote"] == "خبر دلته دی"


def test_quote_progress_uses_gemini_step():
    from modules.progress import QUOTE_PROGRESS_STEPS
    labels = " ".join(label for _icon, label in QUOTE_PROGRESS_STEPS)
    assert "نقل قول" in labels


def test_quote_layout_and_config():
    from modules.design.layout import (
        QUOTE_TEMPLATES, QUOTE_PERSON_BOX, QUOTE_TITLE_BOX, QUOTE_NAME_BOX, QUOTE_RANK_BOX,
    )
    assert QUOTE_TEMPLATES == {"ps": "Quotation_Pashto.png", "fa": "Quotation_Persian.png", "en": "Quotation_English.png"}
    assert QUOTE_PERSON_BOX == (677, 0, 686, 1175)
    assert QUOTE_TITLE_BOX == (62, 223, 554, 687)
    assert QUOTE_NAME_BOX == (62, 962, 554, 57)
    # رتبه پورته-تنظیم ده او نوم ته نږدې شوه (۲۸px → ~۱۴px تشه).
    assert QUOTE_RANK_BOX == (62, 1011, 554, 101)
    cfg = load_config("config/settings.json")
    assert cfg.design.quote_canvas_size == 1353
    assert cfg.design.quote_name_color_hex == "D8DFE7"
    assert cfg.design.quote_rank_color_hex == "FEB407"


def test_render_quote_produces_image(tmp_path):
    from PIL import Image
    from modules.design.quote import render_quote_one
    person = tmp_path / "p.jpg"
    Image.new("RGB", (800, 1400), (60, 70, 90)).save(person)
    cfg = load_config("config/settings.json")
    p = render_quote_one("ps", "دا یو ازموینه‌یي نقل قول دی چې دلته لیکل شوی", "احمد", "وزیر", str(person), cfg, tmp_path)
    assert p.exists()
    assert Image.open(p).size == (1920, 1920)


def test_urgent_progress_has_no_gemini():
    from modules.progress import URGENT_PROGRESS_STEPS
    labels = " ".join(label for _icon, label in URGENT_PROGRESS_STEPS)
    assert "جیمنای" not in labels
    assert "سرلیکونه" in labels


def test_title_sizes_updated():
    cfg = load_config("config/settings.json")
    assert cfg.design.title_target_size == 70
    assert cfg.design.title_normal_min == 63
    assert cfg.design.title_normal_max == 77


def test_balance_score_prefers_non_increasing_and_no_widow():
    from modules.design.text_render import _balance_score
    # [5,4] (upper fuller) بهتر تر [4,5] (لاندې زیات).
    assert _balance_score(["a b c d e", "a b c d"], 70, 70) < _balance_score(["a b c d", "a b c d e"], 70, 70)
    # بې-یتیم بهتر تر یتیم وروستي لاین.
    assert _balance_score(["a b c", "a b"], 70, 70) < _balance_score(["a b c d", "a"], 70, 70)
    # نږدې-ډیفالټ سایز غوره کیږي کله چې ترتیب مساوي وي.
    assert _balance_score(["a b", "a"], 70, 70) < _balance_score(["a b", "a"], 64, 70)


def test_output_size_config():
    cfg = load_config("config/settings.json")
    assert cfg.design.output_size == 1920
    assert cfg.design.urgent_date_size == 54


def test_jalali_date_format():
    from modules.design.urgent import current_jalali_date
    d = current_jalali_date()
    parts = d.split("/")
    assert len(parts) == 3 and len(parts[0]) == 4  # yyyy/mm/d


def test_render_urgent_produces_1561_image(tmp_path):
    from PIL import Image
    from modules.design.urgent import render_urgent_one
    cfg = load_config("config/settings.json")
    p = render_urgent_one("ps", "د ازموینې لپاره یو عاجل سرلیک دلته دی", cfg, tmp_path)
    assert p.exists()
    assert Image.open(p).size == (1920, 1920)  # output_size


def test_botstate_mode_default_news():
    state = _state()
    assert state.mode == "news"


def test_reset_keeps_mode():
    state = _state()
    state.mode = "urgent"
    state.reset()
    # reset یوازې بفر پاکوي — mode د بټن له لارې بدلیږي.
    assert state.mode == "urgent"


def test_source_prompt_has_key_rules():
    # د کاروونکي اختصاصي پرامپټ باید بشپړ ضمیمه شي.
    assert "Nominative" in gemini.SOURCE_PROMPT
    assert "نړيوالې رسنۍ" in gemini.SOURCE_PROMPT
    assert gemini.SOURCE_PROMPT.rstrip().endswith("following text:")


def test_gemini_models_uses_ordered_list(tmp_path):
    settings = tmp_path / "s.json"
    settings.write_text('{"gemini": {"models": ["m-strong", "m-mid", "m-weak"]}}', encoding="utf-8")
    cfg = load_config(settings)
    # cascade باید همدا ترتیب (قوي → ضعیف) وکاروي.
    assert cfg.gemini_models == ["m-strong", "m-mid", "m-weak"]


# ---------- design ----------

def test_parse_color_rrggbb():
    from modules.design.text_render import parse_color
    assert parse_color("FEA20E") == (254, 162, 14)
    assert parse_color("#FFFFFF") == (255, 255, 255)


def test_parse_color_argb_drops_alpha():
    from modules.design.text_render import parse_color
    assert parse_color("FFFEA20E") == (254, 162, 14)


def test_normalize_location_defaults_to_world():
    from modules.design.render import _normalize_location
    assert _normalize_location("Afghanistan") == "Afghanistan"
    assert _normalize_location("Mars") == "World"
    assert _normalize_location("") == "World"


def test_location_map_has_pashto_regin_typo():
    from modules.design.layout import LOCATION_MAP
    # د فایل ریښتینی نوم "Regin" دی، نه "Region".
    assert LOCATION_MAP["ps"]["Region"].endswith("Pa Regin.png")


def test_sanitize_removes_zwnj_and_isolates():
    # ZWNJ (U+200C) + LRI (U+2066) باید پاک شي.
    assert "‌" not in sanitize_filename("خبر‌⁦مهم")


def test_render_all_produces_three_images(tmp_path):
    from PIL import Image
    from modules.design import render_all
    photo = tmp_path / "news.jpg"
    Image.new("RGB", (1200, 900), (40, 80, 120)).save(photo)
    data = {
        "title_ps": "د ازموینې سرلیک دلته دی",
        "source_ps": "الجزیره:",
        "title_fa": "عنوان آزمایشی اینجاست",
        "source_fa": "الجزیره:",
        "title_en": "A test headline goes here for the English card",
        "source_en": "Reuters:",
        "location": "Afghanistan",
    }
    cfg = load_config("config/settings.json")
    paths = render_all(data, str(photo), tmp_path / "out", cfg)
    assert len(paths) == 3
    for p in paths:
        assert p.exists()
        assert Image.open(p).size == (1920, 1920)  # output_size


# ---------- د کوټې کولډاون (Gemini) ----------

_DAILY_ERR = (
    "429 RESOURCE_EXHAUSTED {'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED',"
    " 'details': [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations':"
    " [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaValue': '20'}]},"
    " {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '52s'}]}}"
)
_MINUTE_ERR = "429 RESOURCE_EXHAUSTED quotaId: 'GenerateRequestsPerMinute...', 'retryDelay': '13s'"
_OVERLOAD_ERR = "503 UNAVAILABLE The model is overloaded."


def test_quota_classifies_daily_vs_short():
    assert gemini._is_daily_quota(RuntimeError(_DAILY_ERR)) is True
    assert gemini._is_daily_quota(RuntimeError(_MINUTE_ERR)) is False
    assert gemini._is_overload(RuntimeError(_OVERLOAD_ERR)) is True
    assert gemini._retry_delay(RuntimeError(_MINUTE_ERR)) == 13.0
    assert gemini._retry_delay(RuntimeError("no delay here"), 42.0) == 42.0


def test_quota_reset_is_la_midnight_and_dst_aware():
    """ریسیټ د America/Los_Angeles نیمه شپه ده — DST پخپله سم کیږي."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    la = ZoneInfo("America/Los_Angeles")

    summer = gemini.next_quota_reset(datetime(2026, 7, 26, 20, 0, tzinfo=la))
    winter = gemini.next_quota_reset(datetime(2026, 1, 15, 20, 0, tzinfo=la))
    # دواړه په LA کې ۰۰:۰۰ دي…
    assert summer.astimezone(la).hour == 0 and summer.astimezone(la).minute == 0
    assert winter.astimezone(la).hour == 0
    # …خو د UTC یې توپیر لري (PDT=UTC-7، PST=UTC-8) — یعنې ثابت offset نه دی.
    assert summer.hour == 7 and winter.hour == 8


def test_daily_quota_blocks_until_reset_then_frees():
    from datetime import timedelta
    q = gemini.QuotaTracker()
    q.note(RuntimeError(_DAILY_ERR), "gemini-3.5-flash", 1)
    assert q.is_blocked("gemini-3.5-flash", 1)
    assert not q.is_blocked("gemini-3.5-flash", 2)      # بله کیلي خلاصه
    assert not q.is_blocked("gemini-3.1-pro-preview", 1)  # بل مډل خلاص
    assert q.blocked_until("gemini-3.5-flash", 1) == gemini.next_quota_reset()

    # وخت چې تېر شي → پخپله بیرته لیست ته راځي.
    q._until[("gemini-3.5-flash", 1)] = gemini._now_utc() - timedelta(seconds=1)
    assert not q.is_blocked("gemini-3.5-flash", 1)


def test_short_limit_uses_retry_delay_not_whole_day():
    q = gemini.QuotaTracker()
    q.note(RuntimeError(_MINUTE_ERR), "m", 1)
    until = q.blocked_until("m", 1)
    assert until is not None
    secs = (until - gemini._now_utc()).total_seconds()
    assert 10 < secs < 20, "فی-دقیقې لیمیټ باید ټوله ورځ ونه بندوي"


def _fake_genai(calls, fail_with=None):
    """د google.genai بدیل — هر کال ثبتوي."""
    import sys, types

    class _Resp:
        text = "OK"

    class _Models:
        def __init__(self, key):
            self.key = key

        async def generate_content(self, model=None, contents=None):
            calls.append((model, self.key))
            if fail_with and fail_with(model, self.key):
                raise RuntimeError(_DAILY_ERR)
            return _Resp()

    class _Aio:
        def __init__(self, key):
            self.models = _Models(key)

    class _Client:
        def __init__(self, api_key=None):
            self.aio = _Aio(api_key)

    mod = types.ModuleType("google.genai")
    mod.Client = _Client
    pkg = types.ModuleType("google")
    pkg.genai = mod
    sys.modules["google"] = pkg
    sys.modules["google.genai"] = mod


def test_cascade_skips_pairs_already_known_to_be_rate_limited():
    """⚠️ اصلي موخه: بندې جوړې بیخي نه ازمویل کیږي — وخت نه ضایع کیږي."""
    import asyncio
    from modules.config import Config

    cfg = Config(gemini_api_keys=["k1", "k2"])
    cfg.models = ["m1", "m2"]

    # لومړی ځل: m1 دواړه کیلیانې ورځنۍ کوټه ورکوي، m2 بریالی.
    calls = []
    _fake_genai(calls, fail_with=lambda model, key: model == "m1")
    gemini.quota.clear()
    try:
        assert asyncio.run(gemini._cascade("p", cfg)) == "OK"
        assert calls == [("m1", "k1"), ("m1", "k2"), ("m2", "k1")]

        # دویم ځل: m1 باید **بیخي ونه ازمویل شي**.
        calls.clear()
        assert asyncio.run(gemini._cascade("p", cfg)) == "OK"
        assert calls == [("m2", "k1")], f"m1 بیا ازمویل شو: {calls}"
    finally:
        gemini.quota.clear()


def test_cascade_reports_next_reset_when_everything_is_blocked():
    """که هرڅه بند وي، سمدلاسه څرګنده خطا — نه دا چې بیا ټول وازمویي."""
    import asyncio
    from modules.config import Config

    cfg = Config(gemini_api_keys=["k1"])
    cfg.models = ["m1"]
    calls = []
    _fake_genai(calls, fail_with=lambda model, key: True)
    gemini.quota.clear()
    try:
        asyncio.run(gemini._cascade("p", cfg))
    except RuntimeError:
        pass
    calls.clear()
    try:
        asyncio.run(gemini._cascade("p", cfg))
        raise AssertionError("باید خطا راکړې وای")
    except RuntimeError as err:
        assert "کوټه ختمه" in str(err)
        assert calls == [], "هیڅ کال نه دی پکار — ټول بند دي"
    finally:
        gemini.quota.clear()
