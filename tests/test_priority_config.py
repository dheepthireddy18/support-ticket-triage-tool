"""Tests for reading priority settings from the rules file (data/priority_rules.json)."""

import json
from datetime import timedelta

import pytest
from conftest import RULES_FILE

from ticket_triage.db import init_db
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
        "llm_urgency",
    ]
    assert [rule.enabled for rule in engine.rules] == [True, True, True, False]
    assert engine.high_threshold == 70
    assert engine.medium_threshold == 40
    assert config.worker.check_interval_seconds == 60
    assert config.worker.escalate_after == timedelta(hours=24)


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
    assert config.worker == default_config().worker


def test_keywords_replace_the_whole_list(tmp_path):
    config = load_config(write_json(tmp_path, {"keywords": [{"keyword": "refund", "score": 65}]}))

    keyword_rule = next(r for r in build_engine(config).rules if isinstance(r, KeywordRule))
    assert [kw.phrase for kw in keyword_rule.keywords] == ["refund"]


def test_worker_section_sets_both_intervals(tmp_path):
    path = write_json(tmp_path, {"worker": {"check_interval_seconds": 5, "escalate_after_hours": 1.5}})

    worker = load_config(path).worker

    assert worker.check_interval_seconds == 5
    assert worker.escalate_after == timedelta(hours=1.5)


def test_enabling_the_llm_rule(tmp_path):
    config = load_config(write_json(tmp_path, {"rules": {"llm_urgency": {"enabled": True}}}))

    llm = next(r for r in build_engine(config).rules if r.name == "llm_urgency")
    assert llm.enabled is True


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
        {"worker": {"escalate_after_hours": 0}},
        {"worker": {"check_interval_seconds": -5}},
        {"worker": {"check_interval_seconds": "abc"}},
        {"worker": {"unknown": 1}},
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
        {"worker": []},
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


def test_init_db_no_longer_creates_settings_tables(db_conn):
    tables = {row["name"] for row in db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    assert {"tickets", "rejected_tickets", "ticket_rule_results"} <= tables
    assert not {"rule_settings", "priority_keywords", "app_settings"} & tables


def test_reset_also_drops_legacy_settings_tables(tmp_path):
    path = tmp_path / "old.db"
    conn = init_db(path)
    conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    conn.close()

    conn = init_db(path)  # A normal start leaves the old table alone
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='app_settings'").fetchone()
    conn.close()

    conn = init_db(path, reset=True)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='app_settings'").fetchone() is None
    conn.close()


def test_older_database_gets_new_columns(tmp_path):
    """A tickets table from the first schema version is upgraded by init_db."""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE tickets (
            id TEXT PRIMARY KEY, content_hash TEXT UNIQUE NOT NULL, subject TEXT NOT NULL,
            body TEXT NOT NULL, customer TEXT NOT NULL, created_at TEXT NOT NULL,
            inserted_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
            priority_level TEXT, classified_at TEXT, metadata TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    upgraded = init_db(path)
    columns = {row["name"] for row in upgraded.execute("PRAGMA table_info(tickets)")}
    upgraded.close()

    assert {"priority_score", "escalation_count", "last_escalated_at", "resolved_at"} <= columns
