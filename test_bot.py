"""د NewsBot یونټ-ټیسټونه (بې شبکې).

Unit tests for NewsBot. None of these contact Telegram or Gemini — they check
the pure logic: filename sanitizing, config loading/overlay, buffer validation
and limits, prompt building and JSON extraction.
"""

import os

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


def test_allowlist_configured():
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
    assert QUOTE_RANK_BOX == (62, 1002, 554, 110)
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
