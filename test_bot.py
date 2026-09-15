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
    # هرې منو باید جوړه شي (استثنا وا نه چوي).
    assert commands.main_keyboard().keyboard
    assert commands.active_keyboard().keyboard
    for label in commands.BUTTON_LABELS:
        assert label.strip()


def test_main_menu_has_no_create_step():
    """⚠️ «🛠 جوړول» بټن لرې شو — حالتونه سمدلاسه په اصلي منو کې دي."""
    from modules import commands
    assert not hasattr(commands, "BTN_CREATE")
    assert not hasattr(commands, "create_keyboard")
    labels = [b.text for row in commands.main_keyboard().keyboard for b in row]
    assert labels == [commands.BTN_NEWS, commands.BTN_URGENT, commands.BTN_QUOTE]
    assert "جوړول" not in " ".join(commands.BUTTON_LABELS)
    assert "جوړول" not in commands.MENU_RESET_TEXT


def test_keyboard_is_not_persistent_so_back_can_hide_it():
    """⚠️ `is_persistent=True` د مبایل د «بیرته» بټن کار خرابوه.

    کیبورډ به تل ښکاره و، نو «بیرته» یې نه پټوله — کارونکی له ګروپ څخه وتلو.
    اوس false دی: «بیرته» لومړی منو پټوي، بیا یې چټ پریږدي.
    """
    from modules import commands
    for kb in (commands.main_keyboard(), commands.active_keyboard()):
        assert not kb.is_persistent, "منو باید پټېدونکې وي"
        assert kb.resize_keyboard


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
    for key in ("quote_title_size", "quote_name_size", "quote_rank_size",
                "quote_fill_headroom"):
        assert key in raw["design"], f"{key} باید په settings.json کې جلا درج وي"
    assert cfg.design.quote_fill_headroom > 0, "نقل قول باید تر ډیفالټ لوی هم شي"


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


def test_balance_score_compares_pixel_width_not_word_count():
    """⚠️ regression: پرتله باید **په پکسل** وي، نه د کلمو په شمېر.

    «۶۹ تن از منسوبین وزارت دفاع از» = ۷ کلمې = ۸۵۳px، خو
    «آموزش‌های مسلکی نظامی فارغ شدند» = ۵ کلمې = ۱۰۲۷px.
    د کلمو پر بنسټ «سم» ښکارېده (۷ ≥ ۵)، خو سترګه یې مات ویني.
    """
    from pathlib import Path as _P
    from modules.design.text_render import _balance_score

    d = load_config("config/settings.json").design
    fp = str(_P(d.fonts_dir) / d.title_font)
    upper = "۶۹ تن از منسوبین وزارت دفاع از"      # ۷ کلمې، خو لنډ
    lower = "آموزش‌های مسلکی نظامی فارغ شدند"     # ۵ کلمې، خو پراخ

    bad = _balance_score([upper, lower], 70, 70, fp, False, 1176)
    good = _balance_score([lower, upper], 70, 70, fp, False, 1176)
    assert bad[0] == 1, "پراخ ښکتنی لاین باید سرغړونه وګڼل شي"
    assert good[0] == 0
    assert good < bad

    # (سرغړونې، بد مات‌ځایونه، تش لاینونه، تشه، له ډیفالټ واټن)
    # «... دفاع **از**» یو بد مات‌ځای دی — «از» له راتلونکي سره تړلی دی.
    assert bad[1] == 1 and good[1] == 0, "د لاین په پای کې «از» باید بد مات‌ځای وي"

    full = _balance_score([upper, lower], 70, 70, fp, False, 900)
    empty = _balance_score([upper, lower], 70, 70, fp, False, 3000)
    assert full[2] < empty[2], "پراخ باکس = تش لاین"
    assert full[3] < empty[3], "پراخ باکس = ډیره تشه"


def test_news_title_never_has_a_wider_lower_line():
    """د خبر عنوان: ښکتنی لاین باید تر پاسني **د پام وړ** پراخ نشي.

    ⚠️ زغم (`_DESC_TOL`) ورکوو ځکه د ادارې خپلې لاسي نمونې ۹۷→۱۰۰، ۹۳→۹۸ او
    ۹۰→۹۹ لري. کلک قانون هغه ښکلي ډیزاینونه منع کول.
    """
    from pathlib import Path as _P
    from modules.design.layout import LANGS
    from modules.design.text_render import (
        _DESC_TOL, find_best_size, get_font, text_width,
    )

    d = load_config("config/settings.json").design
    fp = str(_P(d.fonts_dir) / d.title_font)
    box = [lg for lg in LANGS if lg["code"] == "fa"][0]["title_box"]
    for title in [
        "۶۹ تن از منسوبین وزارت دفاع از آموزش‌های مسلکی نظامی فارغ شدند",
        "پنتاگون می‌گوید که در جنگ با ایران ۱۸ عسکر آمریکایی کشته شدند",
        "نتانیاهو می‌گوید موضع خود را در مورد غزه، لبنان و سوریه تغییر نخواهد داد",
        "د کابل په ښار کې چاودنه وشوه",
    ]:
        size, lines = find_best_size(title, fp, box, False, d)
        font = get_font(fp, size)
        bw = box[2]
        w = [text_width(font, ln, False) for ln in lines]
        for i in range(len(w) - 1):
            assert w[i] >= w[i + 1] - _DESC_TOL * bw, (
                f"لاین {i + 1} ({w[i + 1]:.0f}px) تر {i} ({w[i]:.0f}px) د پام وړ "
                f"پراخ دی — {title[:30]}"
            )


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


# ---------- د نرخ-حد (flood) ساتنه ----------

def test_whole_package_needs_exactly_one_gemini_call():
    """⚠️ یو پکیج = **یو کال**. پخوا ۴ وو (۱ سرلیک/موقعیت + ۳ سرچینې).

    دا هم وخت سپموي او هم د وړیا کوټې څلور برخې.
    """
    import asyncio
    from modules.config import Config

    calls = []
    orig = gemini._cascade

    async def fake(prompt, config):
        calls.append(prompt)
        return ('{"text_ps":"الجزیره: پ سرلیک","source_ps":"الجزیره",'
                '"text_fa":"ف سرتیتر","source_fa":"باختر آژانس",'
                '"text_en":"E headline","source_en":"Reuters",'
                '"location":"World"}')

    gemini._cascade = fake
    try:
        res = asyncio.run(gemini.analyze(["پښتو دی", "فارسی است", "English is"],
                                         Config(gemini_api_keys=["k"])))
    finally:
        gemini._cascade = orig

    assert len(calls) == 1, f"باید یو کال وي، خو {len(calls)} شول"
    # ګډ پرامپټ دې درې واړه برخې ولري.
    assert "source_ps" in calls[0] and "location" in calls[0]
    assert "GRAMMAR CORRECTION" in calls[0], "د سرچینې قواعد باید شامل وي"
    # سرچینې د همدې یوه ځواب څخه راځي.
    assert res["source_ps"] == "الجزیره"
    assert res["source_fa"] == "باختر آژانس"
    assert res["source_en"] == "Reuters"
    # د «:» مخکینۍ برخه لا هم له سرلیک بیلیږي.
    assert res["title_ps"] == "پ سرلیک"


def test_quote_needs_exactly_one_gemini_call():
    """د نقل قول ټولې ژبې هم په یوه کال کې (پخوا فی ژبه یو کال)."""
    import asyncio
    from modules.config import Config

    calls = []
    orig = gemini._cascade

    async def fake(prompt, config):
        calls.append(prompt)
        return ('{"ps":{"name":"احمد","rank":"وزیر","quote":"پښتو نقل قول."},'
                '"fa":{"name":"احمد","rank":"وزیر","quote":"نقل قول فارسی!"},'
                '"en":{"name":"Ahmad","rank":"Minister","quote":"English quote."}}')

    gemini._cascade = fake
    try:
        out = asyncio.run(gemini.analyze_quote(
            ["د کابل په ښار کې ویې ویل", "در شهر کابل گفته شد", "Said in Kabul city"],
            Config(gemini_api_keys=["k"])))
    finally:
        gemini._cascade = orig

    assert len(calls) == 1, f"باید یو کال وي، خو {len(calls)} شول"
    assert set(out) == {"ps", "fa", "en"}
    # د پای نقطه/تعجب لرې کیږي (زموږ قانون).
    assert out["ps"]["quote"] == "پښتو نقل قول"
    assert out["fa"]["quote"] == "نقل قول فارسی"
    assert out["en"]["quote"] == "English quote"
    assert out["ps"]["rank"] == "وزیر"
    # ژبې په کوډ کې ټاکل شوې — پرامپټ یې لیبل شوي وړي.
    assert "[ps]" in calls[0] and "[fa]" in calls[0] and "[en]" in calls[0]


def test_quote_prompt_rules_are_kept_verbatim():
    """د کاروونکي قواعد باید په ګډ پرامپټ کې کټ مټ پاتې شي."""
    built = gemini.QUOTE_COMBINED_PROMPT.format(
        rules=gemini._QUOTE_RULES, reports="[ps] test")
    for must in ["ZERO CHANGES", "NO TRAILING PUNCTUATION", "VERBATIM", "DEEP SEARCH"]:
        assert must in built, must


def test_source_rules_are_kept_verbatim_in_combined_prompt():
    """د سرچینې قواعد (د کاروونکي تجربه شوی پرامپټ) باید کټ مټ وي."""
    built = gemini.build_prompt(["پښتو راپور"])
    for must in ["GRAMMAR CORRECTION", "نړيوالې رسنۍ", "باختر آژانس",
                 "ORGANIZATION OVER PERSON", "FALLBACK WHEN NO SOURCE EXISTS",
                 "قطر کې د افغانستان سفارت"]:
        assert must in built, must


def test_progress_pause_stops_the_ticker_before_sending():
    """د فایلونو له لیږلو مخکې ټیکر ودریږي — نرخ بودجه فایلونو ته پاتې شي."""
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

    async def go():
        p = Progress(_Bot(), 1, min_interval=0.05)
        await p.start()
        assert p._ticker is not None
        p.pause()
        assert p._ticker is None

    asyncio.run(go())


def test_ticker_interval_respects_telegram_group_budget():
    """ټلګرام ~۲۰ پیغامه/دقیقه پریږدي → ټیکر باید تر ۳ ثانیو ژر نه وي."""
    from modules.progress import Progress
    for requested in (0.1, 0.8, 1.0, 2.9):
        p = Progress(None, 1, min_interval=requested)
        assert p.tick_interval >= 3.0, f"{requested} → {p.tick_interval}"
        assert 60 / p.tick_interval <= 20


def test_send_designs_retries_after_flood_control():
    """⚠️ regression: flood به د فایلونو لیږل ماتول. اوس صبر او بیا هڅه کیږي."""
    import asyncio
    from telegram.error import RetryAfter
    from modules import commands

    attempts = []
    sleeps = []

    class _Bot:
        async def send_media_group(self, chat_id=None, media=None, **kw):
            attempts.append(kw.get("reply_to_message_id"))
            if len(attempts) < 3:
                raise RetryAfter(1)
            return []

    class _Ctx:
        bot = _Bot()

    async def _fast_sleep(sec):
        sleeps.append(sec)

    real_sleep = commands.asyncio.sleep
    commands.asyncio.sleep = _fast_sleep
    try:
        tmp = Path("data/pending"); tmp.mkdir(parents=True, exist_ok=True)
        f = tmp / "_t.jpg"; f.write_bytes(b"x")
        asyncio.run(commands._send_designs(_Ctx(), 1, [f], reply_to=99))
        f.unlink(missing_ok=True)
    finally:
        commands.asyncio.sleep = real_sleep

    assert len(attempts) == 3, f"باید بیا هڅه کړې وای: {attempts}"
    assert sleeps, "باید صبر کړی وای"


# ---------- کشیده: هر لاین ځای ولري، خو لږ کشیده ----------

_IMG_TEXT = ("رویداد بدخشان از حمایت مردمی برخوردار نیست و تلاش می‌شود از طریق "
             "شایعات، ناامنی ایجاد شود")


def test_every_rtl_line_has_kashida_slots():
    """⚠️ regression: «تلاش می‌شود از» هیڅ ځای نه درلود → تشه پاتې کېده.

    لامل: مو غوښتل مخکینی/وروستی توری هم عربي وي، چې د کلمې د پیل/پای نښلونه
    یې بندوله.
    """
    from modules.design.justify import _slots
    for line in ["تلاش می‌شود از", "حمایت مردمی", "از طریق شایعات،", "نیست و تلاش"]:
        assert _slots(line), f"هیڅ ځای نشته: {line!r}"
    # «حمایت مردمی» پخوا یوازې ۱ ځای درلود → ټوله پراخي په یوه ځای کې راټولېده.
    assert len(_slots("حمایت مردمی")) >= 4


def test_kashida_spreads_instead_of_piling_into_one_spot():
    """کشیده باید وویشل شي — نه دا چې «حمـــــــایت» جوړ کړي."""
    import re
    from modules.design.justify import justify_line, TATWEEL

    line = "حمایت مردمی"
    out = justify_line(line, lambda t: len(t), target_w=len(line) + 8)
    longest = max((len(m) for m in re.findall(TATWEEL + "+", out)), default=0)
    assert longest <= 4, f"په یوه ځای کې {longest} کشیدې راټولې شوې"
    assert out.count(TATWEEL) >= 5, "کشیده باید په څو ځایونو کې وویشل شي"


def test_fit_to_box_minimises_gap_left_after_kashida():
    """سایز باید د **کشیدې وروسته** پاتې تشې پر بنسټ وټاکل شي."""
    from pathlib import Path as _P
    from modules.design.layout import QUOTE_TITLE_BOX
    from modules.design.text_render import (
        fit_to_box, gap_tolerance, get_font, line_height,
        worst_gap_after_kashida, wrap_text,
    )

    d = load_config("config/settings.json").design
    fp = str(_P(d.fonts_dir) / d.quote_title_font)
    bw, bh = QUOTE_TITLE_BOX[2], QUOTE_TITLE_BOX[3]
    size, lines = fit_to_box(_IMG_TEXT, fp, bw, bh, d.title_line_gap,
                             d.quote_title_size, d.title_abs_min, False)
    chosen = worst_gap_after_kashida(lines, fp, size, bw, False)

    # د ډیفالټ (لوی) سایز سره پرتله — نوی انتخاب باید تر هغه ښه یا برابر وي.
    big = wrap_text(_IMG_TEXT, fp, d.quote_title_size, bw, False)
    if len(big) * line_height(get_font(fp, d.quote_title_size), d.title_line_gap) <= bh:
        assert chosen <= worst_gap_after_kashida(big, fp, d.quote_title_size, bw, False)
    # حد = د باکس ۲٪ **یا** د کشیدې ¾ — کومه چې لویه. کشیده یو بشپړ توری دی،
    # نو تر هغې کمه تشه ریاضي‌وار ناممکنه ده (وګوره `_GAP_KASHIDA_FRACTION`).
    tol = gap_tolerance(fp, size, bw)
    assert chosen <= tol, (
        f"پاتې تشه {100 * chosen / bw:.1f}٪ ده (حد {100 * tol / bw:.1f}٪)"
    )


def test_no_line_is_left_completely_unjustified():
    """هیڅ نا-وروستی لاین باید بې کشیدې لویه تشه ونه لري."""
    from pathlib import Path as _P
    from modules.design.layout import QUOTE_TITLE_BOX
    from modules.design.text_render import (
        fit_to_box, gap_tolerance, get_font, text_width,
    )
    from modules.design.justify import justify_line

    d = load_config("config/settings.json").design
    fp = str(_P(d.fonts_dir) / d.quote_title_font)
    bw, bh = QUOTE_TITLE_BOX[2], QUOTE_TITLE_BOX[3]
    size, lines = fit_to_box(_IMG_TEXT, fp, bw, bh, d.title_line_gap,
                             d.quote_title_size, d.title_abs_min, False)
    font = get_font(fp, size)
    tol = gap_tolerance(fp, size, bw)
    for i, ln in enumerate(lines[:-1]):
        filled = justify_line(ln, lambda t: text_width(font, t, False), bw, seed=i)
        gap = bw - text_width(font, filled, False)
        assert gap <= tol, f"لاین {i} کې {100 * gap / bw:.1f}٪ تشه پاتې ده"


# ---------- د نقل قول د متن اندازه (د ادارې شکایت: «متن ډیر کوچنی دی») ----------

_QUOTE_SAMPLES = [
    "رویداد بدخشان از حمایت مردمی برخوردار نیست و تلاش می‌شود از طریق "
    "شایعات، ناامنی ایجاد شود",
    "زه ډاډ ورکوم چې د افغانستان اقتصادي وضعیت به په راتلونکي کال کې ښه شي",
    "موږ له ټولو هېوادونو سره ښې اړیکې غواړو او هیچا ته اجازه نه ورکوو چې "
    "زموږ خاوره وکاروي",
    "ما اطمینان می‌دهیم که وضعیت اقتصادی افغانستان در سال آینده بهبود خواهد "
    "یافت و مردم شاهد تغییرات مثبت خواهند بود",
    "امنیت په ټول هېواد کې تامین دی",
]


def _quote_fit(text):
    """د نقل قول د باکس لپاره (سایز، لاینونه، fp، bw، bh، dcfg) راګرځوي."""
    from pathlib import Path as _P
    from modules.design.layout import QUOTE_TITLE_BOX
    from modules.design.text_render import fit_to_box

    d = load_config("config/settings.json").design
    fp = str(_P(d.fonts_dir) / d.quote_title_font)
    bw, bh = QUOTE_TITLE_BOX[2], QUOTE_TITLE_BOX[3]
    size, lines = fit_to_box(text, fp, bw, bh, d.title_line_gap,
                             d.quote_title_size, d.title_abs_min, False,
                             headroom=d.quote_fill_headroom)
    return size, lines, fp, bw, bh, d


def test_quote_size_stays_closest_to_the_default():
    """قانون: **ډیفالټ سایز یا ورته نږدې** — نه ډیر کوچنی، نه ډیر لوی.

    ⚠️ دوه پخوانۍ تېروتنې دواړه دلته بندېږي:
      • «تر ټولو لږه تشه» → تل ترټولو کوچنی فونټ (۴۷–۵۹px) غوره کاوه،
      • «تر ټولو لوی پاک سایز» → بل لور ته افراط (۹۳px د لنډ نقل قول لپاره).
    اوس باید **هیڅ بل پاک سایز** ډیفالټ ته تر ټاکل شوي نږدې نه وي.
    """
    from modules.design.text_render import (
        _is_clean, get_font, kashida_wrap, line_height,
    )

    for text in _QUOTE_SAMPLES:
        size, _lines, fp, bw, bh, d = _quote_fit(text)
        target = d.quote_title_size
        lo = max(d.title_abs_min, target - 20)
        for other in range(lo, target + d.quote_fill_headroom + 1):
            if abs(other - target) >= abs(size - target):
                continue                       # لرې یا برابر — پروا نلري
            lines = kashida_wrap(text, fp, other, bw, False)
            if len(lines) * line_height(get_font(fp, other), d.title_line_gap) > bh:
                continue                       # ځای نه کیږي — سم دی
            assert not _is_clean(lines, fp, other, bw, False), (
                f"{other}px ډیفالټ ته نږدې او پاک و، خو {size}px وټاکل شو: {text[:30]}"
            )


def test_short_quote_uses_the_default_size():
    """لنډ نقل قول باید دقیقاً ډیفالټ سایز واخلي — نه ښکته او نه پورته."""
    for text in ("امنیت په ټول هېواد کې تامین دی", "سوله زموږ لومړیتوب دی"):
        size, _lines, _fp, _bw, _bh, d = _quote_fit(text)
        assert size == d.quote_title_size, f"{size}px — ډیفالټ {d.quote_title_size} و"


def test_quote_never_grows_far_past_the_default():
    """متن هیڅکله تر ډیفالټ ډیر لوی نشي — headroom یوازې یو کوچنی ساتني بند دی."""
    for text in _QUOTE_SAMPLES:
        size, _lines, _fp, _bw, _bh, d = _quote_fit(text)
        top = d.quote_title_size + d.quote_fill_headroom
        assert size <= top, f"{size}px تر {top} پورته دی: {text[:30]}"


def test_long_quote_is_not_shrunk_to_a_tiny_size():
    """اوږد نقل قول هم باید مسلکي اندازه ولري — نه ۴۷–۴۸px لکه پخوا."""
    for text in _QUOTE_SAMPLES:
        size, lines, fp, bw, bh, d = _quote_fit(text)
        assert size >= 52, f"{size}px ډیر کوچنی دی: {text[:30]}"


def test_quote_lines_have_no_more_gap_than_kashida_allows():
    """پاتې تشه باید تر هغه حد کمه وي چې **کشیده یې ډکولی شي**.

    ⚠️ مطلق صفر ناممکن دی: کشیده یو بشپړ توری دی (په ۶۵px کې ~۲۰px = ۳.۶٪ د
    باکس)، نو پاتې تشه تل تر یوې کشیدې کمه وي — لږه یې نشو کولی. حد = د باکس
    ۲٪ **یا** د کشیدې ¾ (کومه چې لویه)، یعنې «کشیدې خپل کار تر پایه کړی دی».
    """
    from modules.design.text_render import gap_tolerance, worst_gap_after_kashida

    for text in _QUOTE_SAMPLES:
        size, lines, fp, bw, _bh, _d = _quote_fit(text)
        gap = worst_gap_after_kashida(lines, fp, size, bw, False)
        tol = gap_tolerance(fp, size, bw)
        assert gap <= tol, (
            f"{100 * gap / bw:.1f}٪ تشه پاتې ده (حد {100 * tol / bw:.1f}٪): {text[:30]}"
        )


def test_quote_is_no_longer_forced_small_by_a_fixed_gap_rule():
    """⚠️ regression: ثابت ۲٪ حد نقل قول تل کوچني سایز ته اړاوه.

    «ما اطمینان…» په ۵۸px کې ښه ځایېده (تشه ۲.۴٪ = تر یوې کشیدې ښه)، خو ثابت
    ۲٪ یې ردوله، نو الګوریتم ۵۴px ته ښکته کېده. د ادارې ملګري یې شکایت کاوه.
    """
    for text in _QUOTE_SAMPLES:
        size, _lines, _fp, _bw, _bh, d = _quote_fit(text)
        assert size >= 56, f"{size}px لا هم کوچنی دی: {text[:34]}"
        assert size <= d.quote_title_size + d.quote_fill_headroom


def test_kashida_wrap_beats_plain_wrap_on_leftover_gap():
    """د مات‌ځایونو انتخاب باید د **کشیدې وروسته** پاتې تشه کمه کړي، نه خامه تشه."""
    from pathlib import Path as _P
    from modules.design.layout import QUOTE_TITLE_BOX
    from modules.design.text_render import (
        kashida_wrap, worst_gap_after_kashida, wrap_text,
    )

    d = load_config("config/settings.json").design
    fp = str(_P(d.fonts_dir) / d.quote_title_font)
    bw = QUOTE_TITLE_BOX[2]
    better = 0
    for text in _QUOTE_SAMPLES:
        for size in (54, 61, 74):
            a = worst_gap_after_kashida(kashida_wrap(text, fp, size, bw, False),
                                        fp, size, bw, False)
            b = worst_gap_after_kashida(wrap_text(text, fp, size, bw, False),
                                        fp, size, bw, False)
            if a < b - 0.5:
                better += 1
    assert better, "kashida_wrap له ساده wrap څخه هیڅ ښه نه و"


# ---------- د منو د اړیکو پاکول ----------

def test_chatter_survives_reset_so_it_can_be_purged():
    """⚠️ regression: reset به chatter پاکاوه، نو منو-پیغامونه به بیا نه ړنګېدل."""
    from modules import commands
    state = _state()
    commands._track(state, 101, 201, 103)
    state.reset()                      # د «جوړول → عاجل» ترمنځ پیښیږي
    assert state.chatter == [101, 201, 103]


def test_reset_does_not_forget_the_menu_message():
    """د منو پیغام ID باید د reset ژوندی پاتې شي (کیبورډ یې راوړی)."""
    state = _state()
    state.menu_msg_id = 555
    state.reset()
    assert state.menu_msg_id == 555


def test_purge_deletes_menu_chatter_in_one_call_and_keeps_content():
    """د منو پیغامونه یو کال کې ړنګیږي؛ د پکیج محتوا نه لمس کیږي."""
    import asyncio
    from modules import commands

    bulk = []

    class _Bot:
        async def delete_messages(self, chat_id=None, message_ids=None):
            bulk.append(list(message_ids))

    class _Ctx:
        bot = _Bot()

    state = _state()
    commands._track(state, 101, 201, 103, 202, 112)
    state.buffer.append(BufferItem(type="text", message_id=110, content="راپور"))
    asyncio.run(commands._purge_chatter(_Ctx(), 1, state))

    assert len(bulk) == 1, "باید یو bulk کال وي (نه فی-پیغام)"
    assert bulk[0] == [101, 201, 103, 202, 112]
    assert 110 not in bulk[0], "د پکیج محتوا باید ونه ړنګیږي"
    assert state.chatter == []


def test_progress_is_deleted_before_files_so_menu_is_not_delayed():
    """د فایلونو وروسته باید یوازې **یو** کال پاتې شي — نه نو منو ناوخته راځي."""
    import asyncio
    from modules.config import load_config as _lc
    from modules.package import BotState as _BS, BufferItem as _BI
    from modules import commands

    order = []

    class _M:
        def __init__(self, i):
            self.message_id = i

    class _Bot:
        async def send_message(self, cid, text=None, **k):
            order.append("send")
            return _M(9)

        async def edit_message_text(self, **k):
            order.append("edit")
            return _M(9)

        async def delete_message(self, **k):
            order.append("del-progress")

        async def delete_messages(self, **k):
            order.append("purge")

        async def send_media_group(self, chat_id, media, **kw):
            order.append("FILES")
            return []

    class _App:
        def __init__(self, st):
            self.bot_data = {"state": st}

    class _Ctx:
        def __init__(self, st):
            self.application = _App(st)
            self.bot = _Bot()
            self.bot_data = self.application.bot_data

    cfg = _lc("config/settings.json")
    cfg.packages_dir = "data/pending/_t_purge"
    cfg.design.progress_min_interval = 0.0
    st = _BS(config=cfg)
    st.mode = "urgent"
    st.buffer = [_BI(type="text", message_id=110, content="د کابل په ښار کې خبر خپور شو")]
    asyncio.run(commands.run_urgent_pipeline(_Ctx(st), 1))

    after_files = order[order.index("FILES") + 1:]
    assert after_files.count("send") == 1
    # تر فایلونو وروسته، منو باید **لومړی** کال وي (نه وروسته له edit/delete).
    assert after_files[0] == "send", f"منو ځنډېدلې: {after_files}"
    import shutil
    shutil.rmtree("data/pending/_t_purge", ignore_errors=True)


# ---------- د Gemini د کال وخت-حد ----------

def test_slow_gemini_call_times_out_and_moves_on():
    """⚠️ regression: یو کال ۳۸ ثانیې ونیوې او ټول پایپ‌لاین یې بند کړ.

    په لاګ کې: gemini-3.5-flash ۳۸s ونیوې، حال دا چې gemini-3.6-flash
    ۲.۵s کې ځواب ورکړ. اوس وخت-حد ورو مډل پریږدي.
    """
    import asyncio, sys, time, types
    from modules.config import Config

    class _R:
        text = "OK"

    class _Models:
        async def generate_content(self, model=None, contents=None):
            if model == "m-slow":
                await asyncio.sleep(30)
            await asyncio.sleep(0.05)
            return _R()

    class _Aio:
        models = _Models()

    class _Client:
        def __init__(self, api_key=None):
            self.aio = _Aio()

    mod = types.ModuleType("google.genai"); mod.Client = _Client
    pkg = types.ModuleType("google"); pkg.genai = mod
    sys.modules["google"] = pkg
    sys.modules["google.genai"] = mod

    cfg = Config(gemini_api_keys=["k1"])
    cfg.models = ["m-slow", "m-fast"]
    cfg.gemini_timeout = 0.5
    gemini.quota.clear()
    try:
        t0 = time.monotonic()
        assert asyncio.run(gemini._cascade("p", cfg)) == "OK"
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, f"وخت-حد کار ونکړ ({elapsed:.1f}s)"
        # ورو جوړه باید لنډ وخت کولډاون واخلي.
        assert gemini.quota.is_blocked("m-slow", 1)
        assert not gemini.quota.is_blocked("m-fast", 1)
    finally:
        gemini.quota.clear()


def test_gemini_timeout_is_configurable():
    """بریالي کالونه ۲.۵–۵.۰s نیسي، نو ۱۲s دوه‌برابره ځای دی."""
    cfg = load_config("config/settings.json")
    assert cfg.gemini_timeout == 12.0
    import json as _j
    raw = _j.loads(Path("config/settings.json").read_text(encoding="utf-8"))
    assert raw["gemini"]["timeout"] == 12.0


# ---------- د مډل کچې کولډاون ----------

_OVERLOAD_ERR_FULL = (
    "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently "
    "experiencing high demand. Spikes in demand are usually temporary.', "
    "'status': 'UNAVAILABLE'}}"
)


def _stub_genai(attempts, behaviour):
    import sys, types

    class _R:
        text = "OK"

    class _Models:
        def __init__(self, key):
            self.key = key

        async def generate_content(self, model=None, contents=None):
            attempts.append((model, self.key))
            await behaviour(model, self.key)
            return _R()

    class _Aio:
        def __init__(self, key):
            self.models = _Models(key)

    class _Client:
        def __init__(self, api_key=None):
            self.aio = _Aio(api_key)

    mod = types.ModuleType("google.genai"); mod.Client = _Client
    pkg = types.ModuleType("google"); pkg.genai = mod
    sys.modules["google"] = pkg
    sys.modules["google.genai"] = mod


def test_overload_blocks_the_whole_model_not_just_one_key():
    """⚠️ regression: «This model is experiencing high demand» = د مډل ستونزه.

    په لاګ کې کیلي #۱ 503 ورکړه، بیا مو هماغه مډل له کیلي #۲ او #۳ سره وازموه —
    هره یوه ۲۰ ثانیې وخت-حد ته ورسېده. ۴۰ ثانیې بې‌ګټې.
    """
    import asyncio
    from modules.config import Config

    attempts = []

    async def behave(model, key):
        if model == "m1":
            raise RuntimeError(_OVERLOAD_ERR_FULL)

    _stub_genai(attempts, behave)
    cfg = Config(gemini_api_keys=["k1", "k2", "k3"])
    cfg.models = ["m1", "m2"]
    gemini.quota.clear()
    try:
        assert asyncio.run(gemini._cascade("p", cfg)) == "OK"
        # m1 باید **یوازې یو ځل** وازمویل شي — بیا ټول مډل بند.
        assert attempts == [("m1", "k1"), ("m2", "k1")], attempts
        assert gemini.quota.is_blocked("m1", 2), "نورې کیلۍ هم باید بندې وي"
        assert gemini.quota.is_blocked("m1", 3)
        assert not gemini.quota.is_blocked("m2", 1)
    finally:
        gemini.quota.clear()


def test_a_timeout_blocks_the_whole_model_immediately():
    """⚠️ ورووالی د **سرویس** حالت دی، نه د کیلي — نورې کیلۍ مه ازموه.

    په لاګ کې دوه کیلۍ ژر (429) ناکامې شوې او دریمه ۲۰s ورو وه؛ د «دوه ورو»
    شرط هیڅکله ونه لګېد، نو ۲۰ ثانیې ضایع شوې.
    """
    import asyncio
    from modules.config import Config

    attempts = []

    async def behave(model, key):
        if model == "m1":
            await asyncio.sleep(5)      # تر وخت-حد زیات

    _stub_genai(attempts, behave)
    cfg = Config(gemini_api_keys=["k1", "k2", "k3"])
    cfg.models = ["m1", "m2"]
    cfg.gemini_timeout = 0.3
    gemini.quota.clear()
    try:
        assert asyncio.run(gemini._cascade("p", cfg)) == "OK"
        m1_tries = [a for a in attempts if a[0] == "m1"]
        assert len(m1_tries) == 1, f"باید یوازې یوه کیلي وازمویل شي: {attempts}"
        assert gemini.quota.is_blocked("m1", 2) and gemini.quota.is_blocked("m1", 3)
    finally:
        gemini.quota.clear()


def test_model_block_appears_in_status_and_expires():
    from datetime import timedelta
    q = gemini.QuotaTracker()
    q.block_model("m1", 20, "بار زیاتوالی")
    assert q.is_blocked("m1", 1) and q.is_blocked("m1", 7)
    assert any("ټول مډل" in s for s in q.status())
    q._model_until["m1"] = gemini._now_utc() - timedelta(seconds=1)
    assert not q.is_blocked("m1", 1), "وخت چې تېر شي، مډل بیرته فعالیږي"


def test_repeatedly_slow_model_gets_escalating_cooldown():
    """تل-ورو مډل باید هره دوره وخت ونه خوري — کولډاون یې دوه برابره کیږي."""
    q = gemini.QuotaTracker()
    waits = [q.note_slow("m1") for _ in range(5)]
    assert waits == [45.0, 90.0, 180.0, 360.0, 720.0], waits
    assert q.note_slow("m1") == 900.0, "باید پر اعظمي حد ودریږي"
    # بریالی کال شمېره پاکوي.
    q.note_success("m1")
    assert q.note_slow("m1") == 45.0


def test_success_clears_slow_strikes_for_that_model():
    import asyncio
    from modules.config import Config

    attempts = []
    calls = {"n": 0}

    async def behave(model, key):
        calls["n"] += 1
        if model == "m1" and calls["n"] == 1:
            await asyncio.sleep(5)      # لومړی ځل ورو

    _stub_genai(attempts, behave)
    cfg = Config(gemini_api_keys=["k1"])
    cfg.models = ["m1", "m2"]
    cfg.gemini_timeout = 0.3
    gemini.quota.clear()
    try:
        asyncio.run(gemini._cascade("p", cfg))
        assert gemini.quota._slow_strikes.get("m1") == 1
        # m2 بریالی و → هیڅ strike نلري.
        assert "m2" not in gemini.quota._slow_strikes
    finally:
        gemini.quota.clear()


def test_quota_memory_survives_a_restart(tmp_path):
    """⚠️ حافظه یوازې په RAM کې وه — د **هر بیا-پیل** وروسته له سره زده کېده.

    ستاسو په لاګونو کې هره «لومړۍ دوره» د ورو مډل ۱۲ ثانیې ورکولې، ځکه
    کانټینر تازه پیل شوی و. اوس حافظه پر ډیسک ساتل کیږي.
    """
    f = tmp_path / "quota_state.json"

    q1 = gemini.QuotaTracker()
    q1.attach(f)
    q1.note(RuntimeError("429 quotaId: 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'"),
            "m1", 1)
    q1.note_slow("m2")
    assert f.exists()

    # کانټینر بیا پیل شو → نوی tracker، هماغه فایل.
    q2 = gemini.QuotaTracker()
    q2.attach(f)
    assert q2.is_blocked("m1", 1), "ورځنۍ کوټه باید پاتې وي"
    assert q2.is_blocked("m2", 1) and q2.is_blocked("m2", 7), "د مډل بندښت باید پاتې وي"
    assert not q2.is_blocked("m3", 1), "نور مډلونه باید خلاص وي"
    assert q2._slow_strikes.get("m2") == 1


def test_expired_blocks_are_dropped_on_load(tmp_path):
    """تېر شوي بندښتونه د لوستلو پر مهال نه راځي."""
    from datetime import timedelta
    f = tmp_path / "q.json"
    q1 = gemini.QuotaTracker()
    q1.attach(f)
    q1.block_model("m1", 60, "ورو")
    q1._model_until["m1"] = gemini._now_utc() - timedelta(seconds=5)
    q1._save()

    q2 = gemini.QuotaTracker()
    q2.attach(f)
    assert not q2.is_blocked("m1", 1)


def test_corrupt_quota_file_is_ignored(tmp_path):
    """خراب فایل باید رباټ ونه دروي."""
    f = tmp_path / "q.json"
    f.write_text("{ this is not json", encoding="utf-8")
    q = gemini.QuotaTracker()
    q.attach(f)          # باید خطا ورنکړي
    assert q.status() == []


# ---------- د خبر عنوان: لاینونه باید باکس ډک کړي ----------

_REAL_TITLES = [
    ("fa", "نتانیاهو جهت بحث درباره ایران و همکاری‌های امنیتی با ترامپ، وارد واشنگتن شد"),
    ("fa", "۷۷ فلسطینی در کرانه باختری فلسطین در سال جاری به شهادت رسیده‌اند"),
    ("fa", "پنتاگون می‌گوید که در جنگ با ایران ۱۸ عسکر آمریکایی کشته و ۶۲۴ نفر زخمی شده‌اند"),
    ("fa", "۶۹ تن از منسوبین وزارت دفاع از آموزش‌های مسلکی نظامی فارغ شدند"),
    ("ps", "روان کال د فلسطین په لوېدیځه غاړه کې ۷۷ فلسطینیان شهیدان شوي"),
    ("ps", "نتنیاهو له ټرمپ سره د ایران او امنیتي همکاریو د بحث په موخه واشنګټن ته رسېدلی"),
    ("ps", "د دفاع وزارت ۶۹ منسوبین له مسلکي پوځي زده کړو فارغ شول"),
    ("ps", "د کابل په ښار کې چاودنه وشوه"),
    ("en", "77 Palestinians Martyred in the West Bank Since the Beginning of This Year"),
    ("en", "Netanyahu Arrives in Washington for Talks With Trump on Iran and Security Cooperation"),
    ("en", "Pentagon Says 18 U.S. Soldiers Killed, 624 Wounded Since Iran Conflict Began"),
]


def _title_fill(code, title):
    from pathlib import Path as _P
    from modules.design.layout import LANGS
    from modules.design.text_render import find_best_size, get_font, text_width

    d = load_config("config/settings.json").design
    lg = [x for x in LANGS if x["code"] == code][0]
    fp = str(_P(d.fonts_dir) / d.title_font)
    box = lg["title_box"]
    size, lines = find_best_size(title, fp, box, lg["is_english"], d)
    font = get_font(fp, size)
    widths = [text_width(font, ln, lg["is_english"]) for ln in lines]
    return size, lines, widths, box[2]


def test_news_title_lines_actually_fill_the_box():
    """⚠️ regression: عنوان به د باکس نیمایي نیوله او پاتې یې تش و.

    لامل **سایز نه و**: `descending_wrap` وروستی لاین هم شمېره، نو لاینونه یې
    برابرول («۵۷٪ + ۵۷٪»). اوس وروستی لاین معاف دی، نو پاسني لاینونه ډکیږي
    («۹۳٪ + ۲۱٪») — دقیقاً لکه د ادارې لاسي ډیزاین.

    شرط: **هر نا-وروستی لاین** لږ تر لږه ۸۰٪ ډک. (وروستی لاین معاف دی.)
    """
    from modules.design.text_render import _MIN_LINE_FILL

    for code, title in _REAL_TITLES:
        size, lines, widths, bw = _title_fill(code, title)
        for i, w in enumerate(widths[:-1]):
            assert w / bw >= _MIN_LINE_FILL, (
                f"[{code}] لاین {i} یوازې {100 * w / bw:.0f}٪ ډک — سایز {size}: {title[:40]}"
            )


# ---------- د مات‌ځای ژبني قواعد (د لاین پای/پیل) ----------

_TIE_TITLES = [
    # ⚠️ ریښتیني بېلګې چې رباټ ماتې کړې وې:
    #   «... عمومي روغتون **د**» | «جوړېدو ...»  → «د جوړېدو» بېل شو
    ("ps", "د زابل شملزو ولسوالۍ کې د ۴۸ میلیونه افغانیو په لګښت عمومي روغتون "
           "د جوړېدو چارې پیل شوې"),
    #   «... زېربناوو د **په**» | «نښه کولو ...»  → «په نښه کول» بېل شو
    ("ps", "ايران د سیمې د انرژۍ مهمو زېربناوو د په نښه کولو ګواښ کړی"),
    ("fa", "روز گذشته ۵۳۶ خانواده مهاجر به کشور بازگشتند"),
    ("fa", "۶۹ تن از منسوبین وزارت دفاع از آموزش‌های مسلکی نظامی فارغ شدند"),
    ("fa", "نتانیاهو می‌گوید موضع خود را در مورد غزه، لبنان و سوریه تغییر نخواهد داد"),
    ("ps", "د بشري مرستو ادارې وايي چې په راتلونکو میاشتو کې به له زرګونو کورنیو "
           "سره مرسته وشي"),
]


def test_bad_breaks_detects_an_orphaned_particle():
    """د `bad_breaks` واحد ازموینه — «د»/«په» د لاین په پای کې یتیم دي."""
    from modules.design.text_render import bad_breaks

    assert bad_breaks(["عمومي روغتون د", "جوړېدو چارې پیل شوې"], False) == 1
    assert bad_breaks(["زېربناوو د په", "نښه کولو ګواښ کړی"], False) == 1
    assert bad_breaks(["عمومي روغتون د جوړېدو", "چارې پیل شوې"], False) == 0
    # وروستی لاین مات‌ځای نه لري، نو «د» پکې بې زیانه ده.
    assert bad_breaks(["چارې پیل شوې د"], False) == 0
    # انګلیسي معاف ده — د ادارې لاسي ډیزاین پخپله «... Return to» کاروي.
    assert bad_breaks(["536 Returnee Families Return to", "Afghanistan"], True) == 0


def test_title_never_orphans_a_binding_particle():
    """⚠️ regression: «... روغتون د | جوړېدو ...» — لاین نیمایي کې بندېده.

    پښتو/دري کې «د، په، له، تر، چې…» له **راتلونکي** ټکي سره تړلي دي. که د لاین
    په پای کې پاتې شي، جمله ماته ښکاري — لاسي ډیزاین دا کار هیڅکله نه کوي.
    """
    from modules.design.text_render import bad_breaks
    from modules.design.layout import LANGS

    for code, title in _TIE_TITLES + _REAL_TITLES:
        _size, lines, _widths, _bw = _title_fill(code, title)
        eng = [x for x in LANGS if x["code"] == code][0]["is_english"]
        assert bad_breaks(lines, eng) == 0, (
            f"[{code}] بد مات‌ځای: {' | '.join(lines)}"
        )


def test_location_rule_uses_the_main_subject():
    """⚠️ regression: «ایران د سیمې زېربناوې ګواښي» → World راته، باید Region وي.

    زاړه قاعده وایه «خبر **یوازې** د ګاونډیو په اړه وي»، نو د سعودي/امارات/قطر د
    نوم راتګ یې نړۍ ته اړاوه. اوس پرېکړه د خبر پر **اصلي محور** ده.
    """
    from modules import gemini

    for prompt in (gemini.PROMPT_TEMPLATE, gemini.COMBINED_PROMPT):
        assert "اصلي محور" in prompt, "د موقعیت قاعده باید پر اصلي محور ولاړه وي"
        assert "مه شمېره" in prompt, "شالید/هدف هیوادونه باید ونه شمېرل شي"
        assert "ایران" in prompt and "Region" in prompt
        assert "یوازې د افغانستان د پولې ګاونډیو" not in prompt, "زړه قاعده پاتې ده"


def test_news_title_never_has_a_wider_lower_line_on_real_titles():
    """ښکتنی لاین تر پاسني د پام وړ پراخ نه دی — پر ټولو ریښتینو عنوانونو."""
    from modules.design.text_render import _DESC_TOL
    for code, title in _REAL_TITLES:
        _, _, widths, bw = _title_fill(code, title)
        for i in range(len(widths) - 1):
            assert widths[i] >= widths[i + 1] - _DESC_TOL * bw, (
                f"[{code}] لاین {i + 1} تر {i} د پام وړ پراخ دی — {title[:40]}"
            )


# ═══════════ د ادارې د ۶ لاسي نمونو معیار (د حقیقت سرچینه) ═══════════
#
# دا شپږ جوړې (لاسي ↔ رباټ) کارونکي راولیږلې. له لاسي نمونو څخه دا قانون
# راووت او په کوډ کې پلی شو:
#
#   #  سایز  لاینه  ډکوالی            نا-وروستي لاینونه
#   ۱   ۶۹     ۲    ۹۷/۱۰۰            ۹۷
#   ۲   ۷۸     ۳    ۹۹/۹۹/۹           ۹۹، ۹۹
#   ۳   ۸۴     ۳    ۹۳/۹۸/۳۰          ۹۳، ۹۸
#   ۴   ۸۰     ۲    ۹۹/۹۵             ۹۹
#   ۵   ۵۴     ۳    ۱۰۰/۱۰۰/۸۶        ۱۰۰، ۱۰۰
#   ۶   ۷۳     ۳    ۹۰/۹۰/۹۹          ۹۰، ۹۰
#
# ⇒ هر نا-وروستی لاین **تل** ≥۹۰٪ ډک؛ وروستی لاین هیڅ حد نلري (۹٪ هم سم دی)؛
#   سایز ۵۴–۸۴ خوځېږي، نو «ثابت ډیفالټ» قانون نه دی — **ډکوالی قانون دی**.

_HAND_DESIGNS = [
    (1, True, 69, ["57-Year Feud Between Two Families in",
                   "Khost Resolved Through Reconciliation"]),
    (2, False, 78, ["ترامپ از زلینسکی خواسته است حملات",
                    "به پالایشگاه‌های دیزل روسیه را متوقف",
                    "کند"]),
    (3, True, 84, ["192 Afghan Families Return to",
                   "Afghanistan Through Four Land",
                   "Crossings"]),
    (4, False, 80, ["مقام‌های افغان و ترکمن درباره روابط و",
                    "همکاری‌های سرحدی گفت‌وگو کردند"]),
    (5, True, 54, ["The Commander of the Khan Younis Brigade of the",
                   "Al-Qassam Brigades, Muhammad Al-Yazouri, Has",
                   "Been Martyred in an Israeli Regime Aattack"]),
    (6, True, 73, ["Minister for Refugees and UNHCR",
                   "Representative Discuss Efforts to",
                   "Address Afghan Refugees' Problems"]),
]


def _hand_box(eng):
    from modules.design.layout import _TITLE_BOX_EN, _TITLE_BOX_RTL
    return _TITLE_BOX_EN if eng else _TITLE_BOX_RTL


def test_bot_reproduces_all_six_hand_designs():
    """⚠️ د معیار ازموینه: رباټ باید **عین** سایز او عین مات‌ځایونه جوړ کړي.

    که دا ازموینه ماته شي، یعنې د لاینونو قانون بیا له لاسي ډیزاین سره توپیر
    کوي — هماغه شکایت چې د ادارې ملګري یې کوي.
    """
    from pathlib import Path as _P
    from modules.design.text_render import find_best_size

    d = load_config("config/settings.json").design
    fp = str(_P(d.fonts_dir) / d.title_font)
    for n, eng, size, lines in _HAND_DESIGNS:
        got_size, got_lines = find_best_size(" ".join(lines), fp, _hand_box(eng), eng, d)
        assert got_size == size, f"نمونه {n}: سایز {got_size} ≠ لاسي {size}"
        assert got_lines == lines, (
            f"نمونه {n}: مات‌ځایونه توپیر لري\n  رباټ: {got_lines}\n  لاسي: {lines}"
        )


def test_every_non_last_line_is_at_least_ninety_percent_full():
    """د لاسي ډیزاین اصلي قانون — نا-وروستی لاین تل ≥۹۰٪ ډک.

    استثنا: هغه لنډ عنوان چې په **هیڅ** سایز کې باکس نه ډکوي (لکه «زلزله») —
    هلته د ډیفالټ سایز ته ورګرځو.
    """
    from pathlib import Path as _P
    from modules.design.text_render import (
        _MIN_LINE_FILL, find_best_size, get_font, text_width,
    )

    d = load_config("config/settings.json").design
    fp = str(_P(d.fonts_dir) / d.title_font)
    cases = [(eng, " ".join(l)) for _n, eng, _s, l in _HAND_DESIGNS]
    cases += [([x for x in __import__("modules.design.layout", fromlist=["LANGS"]).LANGS
                if x["code"] == c][0]["is_english"], t) for c, t in _REAL_TITLES]
    for eng, title in cases:
        box = _hand_box(eng)
        bw = box[2]
        size, lines = find_best_size(title, fp, box, eng, d)
        if len(lines) < 2:
            continue                      # یو-لاینه عنوان — نا-وروستی لاین نشته
        font = get_font(fp, size)
        for i, ln in enumerate(lines[:-1]):
            fill = text_width(font, ln, eng) / bw
            assert fill >= _MIN_LINE_FILL - 0.005, (
                f"لاین {i} یوازې {100 * fill:.0f}٪ ډک دی (سایز {size}): {title[:40]}"
            )


def test_size_is_the_largest_that_keeps_the_fill_rule():
    """سایز باید **تر ټولو لوی** وي چې ۹۰٪ قانون یې ساتلی وي.

    ⚠️ دا هغه شکایت بندوي چې «سایز ډیر لوی دی حال دا چې بل سایز هم قانوني او
    هم ښکلی و» — او برعکس یې هم («لاینونه ډیر لنډ دي»).
    """
    from pathlib import Path as _P
    from modules.design.text_render import (
        _MIN_LINE_FILL, descending_wrap, find_best_size, get_font,
        line_height, text_width, bad_breaks,
    )

    d = load_config("config/settings.json").design
    fp = str(_P(d.fonts_dir) / d.title_font)
    for n, eng, _size, lines in _HAND_DESIGNS:
        box = _hand_box(eng)
        bw, bh = box[2], box[3]
        chosen, _ = find_best_size(" ".join(lines), fp, box, eng, d)
        for bigger in range(chosen + 1, d.title_max_size + 1):
            L = descending_wrap(" ".join(lines), fp, bigger, bw, eng)
            if len(L) * line_height(get_font(fp, bigger), d.title_line_gap) > bh:
                continue
            font = get_font(fp, bigger)
            fills = [text_width(font, x, eng) / bw for x in L[:-1]] or [1.0]
            clean = min(fills) >= _MIN_LINE_FILL and bad_breaks(L, eng) == 0
            assert not clean, (
                f"نمونه {n}: {bigger}px هم پاک و خو {chosen}px وټاکل شو"
            )


def test_title_size_band_matches_the_hand_designs():
    """د لټون بند باید د لاسي نمونو (۵۴–۸۴px) ټوله ساحه ونیسي."""
    d = load_config("config/settings.json").design
    assert d.title_min_size <= 54, "لاسي نمونه ۵۴px لري"
    assert d.title_max_size >= 84, "لاسي نمونه ۸۴px لري"
    raw = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
    for key in ("title_min_size", "title_max_size"):
        assert key in raw["design"], f"{key} باید په settings.json کې وي"


def test_short_title_falls_back_to_the_default_size():
    """لنډ عنوان چې هیڅکله ۹۰٪ نه ډکوي — ډیفالټ سایز واخلي، نه اعظمي."""
    from pathlib import Path as _P
    from modules.design.text_render import find_best_size
    from modules.design.layout import LANGS

    d = load_config("config/settings.json").design
    fp = str(_P(d.fonts_dir) / d.title_font)
    box = [x for x in LANGS if x["code"] == "ps"][0]["title_box"]
    for t in ("زلزله", "د کابل په ښار کې چاودنه وشوه"):
        size, _ = find_best_size(t, fp, box, False, d)
        assert size == d.title_target_size, f"«{t}» → {size}px"


# ---------- د واټساپ د څو راپورونو بېلول ----------

_WA_PASTE = """[۸/۲۴, ۲:۰۳ ب.ظ] ساجد عمر هندوکش ساده: په پنجشېر کې هېڅ سياسي بندي نشته

د پنجشیر سیمه‌ییزو چارواکو ویلي، په دې ولایت کې د زندانیانو شمېر کم دی.
[۸/۲۴, ۲:۰۳ ب.ظ] ساجد عمر هندوکش ساده: هیچ زندانی سیاسی در پنجشیر وجود ندارد

مقام‌های محلی پنجشیر اعلام کرده‌اند که شمار زندانیان کم است.
[۸/۲۴, ۲:۰۴ ب.ظ] ساجد عمر هندوکش ساده: No Political Prisoners in Panjshir

Local officials in Panjshir have stated that the number of prisoners is low."""


def test_split_whatsapp_splits_three_reports():
    """درې واټساپ راپورونه چې په یوه پیغام کې کاپي شوي — درې برخې شي."""
    from modules.utils import split_whatsapp
    parts = split_whatsapp(_WA_PASTE)
    assert len(parts) == 3
    assert parts[0].startswith("په پنجشېر کې")
    assert parts[1].startswith("هیچ زندانی سیاسی")
    assert parts[2].startswith("No Political Prisoners")
    # سرلیکونه نه پاتې کیږي.
    assert all("ب.ظ" not in p and "ساجد عمر" not in p for p in parts)


def test_split_whatsapp_keeps_zwnj():
    """⚠️ ZWNJ (‌) د پښتو/فارسي ریښتینی توری دی — پاکېدل یې متن خرابوي."""
    from modules.utils import split_whatsapp
    parts = split_whatsapp(_WA_PASTE)
    assert "سیمه‌ییزو" in parts[0], "ZWNJ ورک شو → «سیمهییزو»"
    assert "مقام‌های" in parts[1]


def test_split_whatsapp_handles_latin_formats():
    """د واټساپ انګلیسي بڼې هم — د براکټ سره او بې براکټه."""
    from modules.utils import split_whatsapp
    assert split_whatsapp(
        "[24/08/2026, 2:03 PM] Sajid: First body\n"
        "[24/08/2026, 2:04 PM] Ali: Second body"
    ) == ["First body", "Second body"]
    assert split_whatsapp(
        "24/08/2026, 14:03 - Sajid: A\n24/08/2026, 14:04 - Ali: B"
    ) == ["A", "B"]


def test_split_whatsapp_leaves_a_plain_report_alone():
    """بې سرلیکه راپور باید ونه بېل شي — که دوه‌ټکي (:) هم ولري."""
    from modules.utils import split_whatsapp
    plain = "سرخط: دلته یو خبر\n\nمتن یې دا دی، او دویمه پراګراف."
    assert split_whatsapp(plain) == [plain]
    assert split_whatsapp("یو راپور") == ["یو راپور"]
    assert split_whatsapp("   ") == []


def test_one_pasted_message_fills_three_text_slots():
    """د پای-تر-پای چک: یو پیغام → درې متني توکي په بفر کې."""
    import asyncio
    from modules import monitor

    state = _state()
    reacted = []

    class _Msg:
        text = _WA_PASTE
        caption = None
        chat_id = 5
        message_id = 77

    async def fake_react_ordered(context, st, chat_id, mid, index):
        reacted.append(index)

    async def fake_react(*a, **k):
        reacted.append("reject")
        return True

    async def no_refresh(*a, **k):
        return None

    old_o, old_r, old_f = monitor.react_ordered, monitor.react, monitor.refresh_count
    monitor.react_ordered, monitor.react = fake_react_ordered, fake_react
    monitor.refresh_count = no_refresh
    try:
        asyncio.run(monitor._add_text(None, state, _Msg()))
    finally:
        monitor.react_ordered, monitor.react = old_o, old_r
        monitor.refresh_count = old_f

    assert state.counts() == (0, 3), "درې راپورونه باید بېل شوي وای"
    # ټول د یوه پیغام دي، نو ریپلای یې هماغه پیغام ته کیږي.
    assert {i.message_id for i in state.buffer} == {77}
    assert reacted == [2], "یو ری‌اکشن — د اخري توکي شمېره"


def test_extra_reports_beyond_the_limit_are_dropped_not_crashed():
    """څلورم راپور د حد له امله نه منل کیږي — خو دری واړه پاتې کیږي."""
    import asyncio
    from modules import monitor

    state = _state()

    class _Msg:
        text = _WA_PASTE + "\n[۸/۲۴, ۲:۰۵ ب.ظ] بل څوک: څلورم راپور"
        caption = None
        chat_id = 5
        message_id = 78

    async def noop(*a, **k):
        return True

    old_o, old_r, old_f = monitor.react_ordered, monitor.react, monitor.refresh_count
    monitor.react_ordered = monitor.react = monitor.refresh_count = noop
    try:
        asyncio.run(monitor._add_text(None, state, _Msg()))
    finally:
        monitor.react_ordered, monitor.react = old_o, old_r
        monitor.refresh_count = old_f
    assert state.counts() == (0, 3)


# ---------- د تایید ګام لرې کول + په بټن کې شمېره ----------

def test_no_confirmation_step_remains():
    """⚠️ «📦 د پکیج خلاصه» + «✅ تایید / ❌ لغوه» ګام لرې شو."""
    from pathlib import Path as _P
    from modules import commands
    src = _P("modules/commands.py").read_text(encoding="utf-8")
    assert "cmp_confirm" not in src and "cmp_cancel" not in src
    assert "InlineKeyboardMarkup" not in src, "شیشه‌یي کیبورډ نور نه کارول کیږي"
    assert "تایید کوئ چې پروسس پیل شي" not in src
    assert commands.cmd_cmp.__doc__ and "سمدلاسه" in commands.cmd_cmp.__doc__


def test_complete_button_shows_the_item_count():
    """«✅ بشپړول | ۲» — بټن د پکیج د محتوا شمېره ښیي."""
    from modules import commands
    assert commands.complete_label(0).endswith("۰")
    assert commands.complete_label(3).endswith("۳")
    labels = [b.text for row in commands.active_keyboard(2).keyboard for b in row]
    assert labels[0] == commands.complete_label(2)
    assert commands.BTN_COMPLETE in labels[0]


def test_button_match_ignores_the_count_suffix():
    """د شمېرې تاړی باید د بټن پېژندنه ونه ماتوي."""
    from modules import commands
    assert commands.match_button("✅ بشپړول | ۳") == commands.BTN_COMPLETE
    assert commands.match_button("✅ بشپړول") == commands.BTN_COMPLETE
    assert commands.match_button("📰 خبر") == commands.BTN_NEWS
    assert commands.match_button("سلام ټولو ته") is None
    # د واقعي راپور متن باید بټن ونه ګڼل شي (نه‌نو پکیج ته نه ورځي).
    assert commands.match_button("د زابل په ولسوالۍ کې روغتون جوړ شو") is None


def test_button_filter_accepts_the_counted_label():
    """د `filters.Text` پرځای زموږ فلټر — تاړي سره هم سمون کوي."""
    from modules import commands

    class _M:
        text = "✅ بشپړول | ۴"

    class _Other:
        text = "یو عادي راپور"

    assert commands.BUTTON_FILTER.filter(_M())
    assert not commands.BUTTON_FILTER.filter(_Other())


def test_refresh_count_sends_new_then_deletes_old():
    """⚠️ ترتیب حیاتي دی: لومړی نوی ولیږه، بیا زوړ ړنګ کړه.

    د پیغام ړنګول خپل ریپلای-کیبورډ هم بېرته اخلي، نو که لومړی زوړ ړنګ کړو،
    منو به یوه شیبه ورکه شي.
    """
    import asyncio
    from modules import commands
    from modules.package import BufferItem

    order = []

    class _Bot:
        async def send_message(self, chat_id, text, reply_markup=None, **kw):
            order.append(("send", text, reply_markup))

            class _S:
                message_id = 999
            return _S()

        async def delete_message(self, chat_id, message_id):
            order.append(("delete", message_id))

    state = _state()
    state.listening = True
    state.count_msg_id = 500
    state.buffer.append(BufferItem(type="text", message_id=1, content="یو"))
    state.buffer.append(BufferItem(type="text", message_id=2, content="دوه"))

    class _Ctx:
        bot = _Bot()

        class application:
            bot_data = {"state": state}

    asyncio.run(commands.refresh_count(_Ctx(), 42))
    assert [o[0] for o in order] == ["send", "delete"], "ترتیب ناسم دی"
    assert order[1][1] == 500, "زوړ پیغام باید ړنګ شي"
    assert state.count_msg_id == 999
    labels = [b.text for row in order[0][2].keyboard for b in row]
    assert labels[0] == commands.complete_label(2)
    assert 999 in state.chatter, "د دورې په پای کې باید ړنګ شي"
