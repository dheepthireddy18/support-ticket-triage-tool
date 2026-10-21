"""Tests for manager-editable rule settings stored in SQLite."""

import json
from datetime import timedelta

import pytest

from ticket_triage.db import init_db
from ticket_triage.priority.config import (
    DEFAULT_KEYWORDS,
    import_rules,
    load_engine,
    load_keywords,
    load_rule_settings,
    load_worker_settings,
    seed_defaults,
)
from ticket_triage.priority.keyword_rule import KeywordRule
from ticket_triage.priority.waiting_rule import WaitingTimeRule


def write_json(tmp_path, data):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_init_db_seeds_defaults(db_conn):
    settings = load_rule_settings(db_conn)

    assert set(settings) == {"keyword", "repeat_contact", "waiting_time", "llm_urgency"}
    assert settings["llm_urgency"].enabled is False
    assert len(load_keywords(db_conn)) == len(DEFAULT_KEYWORDS)


def test_seed_is_idempotent_and_keeps_manager_changes(db_conn):
    db_conn.execute("UPDATE rule_settings SET weight = 3 WHERE rule_name = 'keyword'")
    db_conn.execute("DELETE FROM priority_keywords WHERE keyword = 'invoice'")
    db_conn.commit()

    seed_defaults(db_conn)
    seed_defaults(db_conn)

    assert load_rule_settings(db_conn)["keyword"].weight == 3
    assert "invoice" not in {kw.phrase for kw in load_keywords(db_conn)}


def test_load_engine_builds_enabled_rules_in_order(db_conn):
    engine = load_engine(db_conn)

    assert [rule.name for rule in engine.rules] == [
        "keyword",
        "repeat_contact",
        "waiting_time",
        "llm_urgency",
    ]
    assert [rule.enabled for rule in engine.rules] == [True, True, True, False]
    assert engine.high_threshold == 70
    assert engine.medium_threshold == 40


def test_load_worker_settings_defaults(db_conn):
    settings = load_worker_settings(db_conn)
    assert settings.check_interval_seconds == 60
    assert settings.escalate_after == timedelta(hours=24)


def test_import_updates_only_given_entries(db_conn, tmp_path):
    path = write_json(
        tmp_path,
        {
            "rules": {"waiting_time": {"weight": 0.5, "params": {"cap_hours": 48}}},
            "settings": {"escalate_after_hours": 12},
        },
    )

    import_rules(db_conn, path)

    engine = load_engine(db_conn)
    waiting = next(r for r in engine.rules if isinstance(r, WaitingTimeRule))
    assert waiting.weight == 0.5
    assert waiting.cap_hours == 48
    assert next(r for r in engine.rules if r.name == "keyword").weight == 1.0
    assert load_worker_settings(db_conn).escalate_after == timedelta(hours=12)


def test_import_keywords_replaces_the_whole_list(db_conn, tmp_path):
    path = write_json(tmp_path, {"keywords": [{"keyword": "refund", "score": 65}]})

    import_rules(db_conn, path)

    keyword_rule = next(r for r in load_engine(db_conn).rules if isinstance(r, KeywordRule))
    assert [kw.phrase for kw in keyword_rule.keywords] == ["refund"]


@pytest.mark.parametrize(
    "data",
    [
        {"rules": {"made_up_rule": {"weight": 1}}},
        {"rules": {"keyword": {"weight": -1}}},
        {"rules": {"keyword": {"colour": "red"}}},
        {"rules": {"waiting_time": {"params": {"cap_hours": 0}}}},
        {"rules": {"waiting_time": {"params": {"not_a_param": 1}}}},
        {"keywords": [{"keyword": "outage", "score": 150}]},
        {"keywords": [{"keyword": "outage", "score": 90, "min_level": "SEVERE"}]},
        {"settings": {"escalate_after_hours": 0}},
        {"settings": {"worker_check_interval_seconds": -5}},
        {"settings": {"high_threshold": 30, "medium_threshold": 60}},
        {"settings": {"unknown_setting": 1}},
        {"extra_section": {}},
        # Sections and entries of the wrong JSON type
        {"rules": []},
        {"rules": {"keyword": 1}},
        {"rules": {"keyword": {"params": []}}},
        {"keywords": {"keyword": "outage", "score": 90}},
        {"keywords": ["outage"]},
        {"settings": []},
        # A keyword listed twice
        {"keywords": [{"keyword": "outage", "score": 90}, {"keyword": "outage", "score": 50}]},
        # "enabled" must be a real boolean: bool("false") would be True
        {"rules": {"waiting_time": {"enabled": "false"}}},
        {"rules": {"waiting_time": {"enabled": 0}}},
    ],
)
def test_bad_import_raises_and_changes_nothing(db_conn, tmp_path, data):
    before = (load_rule_settings(db_conn), load_keywords(db_conn))

    with pytest.raises(ValueError):
        import_rules(db_conn, write_json(tmp_path, data))

    assert (load_rule_settings(db_conn), load_keywords(db_conn)) == before


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
