"""Tests for reading priority settings from the rules file (data/priority_rules.json)."""

import json

import pytest
from conftest import RULES_FILE

from ticket_triage.priority.config import (
    DEFAULT_KEYWORDS,
    build_engine,
    default_config,
    load_config,
)
from ticket_triage.priority.keyword_rule import KeywordRule
from ticket_triage.priority.waiting_rule import WaitingTimeRule


def write_json(tmp_path, data):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_shipped_rules_file_loads_and_builds_all_rules():
    config = load_config(RULES_FILE)
    engine = build_engine(config)

    assert [rule.name for rule in engine.rules] == [
        "keyword",
        "repeat_contact",
        "waiting_time",
    ]
    assert all(rule.enabled for rule in engine.rules)
    assert engine.high_threshold == 70
    assert engine.medium_threshold == 40


def test_no_file_gives_the_built_in_defaults():
    config = load_config(None)

    assert config == default_config()
    assert len(config.keywords) == len(DEFAULT_KEYWORDS)


def test_empty_file_gives_the_built_in_defaults(tmp_path):
    assert load_config(write_json(tmp_path, {})) == default_config()


def test_partial_file_changes_only_what_it_sets(tmp_path):
    path = write_json(
        tmp_path,
        {
            "rules": {"waiting_time": {"weight": 0.5, "params": {"cap_hours": 48}}},
            "thresholds": {"high": 80},
        },
    )

    config = load_config(path)
    engine = build_engine(config)

    waiting = next(r for r in engine.rules if isinstance(r, WaitingTimeRule))
    assert waiting.weight == 0.5
    assert waiting.cap_hours == 48
    assert next(r for r in engine.rules if r.name == "keyword").weight == 1.0
    assert engine.high_threshold == 80
    assert engine.medium_threshold == 40  # Not in the file: default kept


def test_keywords_replace_the_whole_list(tmp_path):
    config = load_config(write_json(tmp_path, {"keywords": [{"keyword": "refund", "score": 65}]}))

    keyword_rule = next(r for r in build_engine(config).rules if isinstance(r, KeywordRule))
    assert [kw.phrase for kw in keyword_rule.keywords] == ["refund"]


@pytest.mark.parametrize(
    "data",
    [
        {"rules": {"made_up_rule": {"weight": 1}}},
        {"rules": {"keyword": {"weight": -1}}},
        {"rules": {"keyword": {"weight": "heavy"}}},
        {"rules": {"keyword": {"colour": "red"}}},
        {"rules": {"waiting_time": {"params": {"cap_hours": 0}}}},
        {"rules": {"waiting_time": {"params": {"not_a_param": 1}}}},
        {"keywords": [{"keyword": "outage", "score": 150}]},
        {"keywords": [{"keyword": "outage", "score": 90, "min_level": "SEVERE"}]},
        {"keywords": [{"keyword": "outage"}]},
        {"keywords": [{"keyword": "outage", "score": 90, "colour": "red"}]},
        {"thresholds": {"high": 30, "medium": 60}},
        {"thresholds": {"high": "seventy"}},
        {"thresholds": {"low": 10}},
        {"settings": {"escalate_after_hours": 12}},  # The old flat format is rejected clearly
        {"extra_section": {}},
        # Sections and entries of the wrong JSON type
        [],
        {"rules": []},
        {"rules": {"keyword": 1}},
        {"rules": {"keyword": {"params": []}}},
        {"keywords": {"keyword": "outage", "score": 90}},
        {"keywords": ["outage"]},
        {"thresholds": 70},
        # A keyword listed twice
        {"keywords": [{"keyword": "outage", "score": 90}, {"keyword": "outage", "score": 50}]},
        # "enabled" must be a real boolean: bool("false") would be True
        {"rules": {"waiting_time": {"enabled": "false"}}},
        {"rules": {"waiting_time": {"enabled": 0}}},
    ],
)
def test_bad_file_raises_value_error(tmp_path, data):
    with pytest.raises(ValueError):
        load_config(write_json(tmp_path, data))


def test_invalid_json_raises_value_error(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid JSON"):
        load_config(path)


def test_missing_file_raises_os_error(tmp_path):
    with pytest.raises(OSError):
        load_config(tmp_path / "nope.json")
