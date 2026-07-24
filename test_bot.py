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
    assert cfg.gemini_models[0] == "gemini-2.5-flash"


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
        assert Image.open(p).size == (1650, 1650)
