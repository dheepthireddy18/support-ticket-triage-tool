"""
Manager-editable rule settings, stored in SQLite.

Tables (see schema.sql): rule_settings, priority_keywords, app_settings.
init_db() seeds the defaults below; a manager changes them with
`ticket-triage --import-rules <file>.json` or any SQLite browser.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from ticket_triage.models import PRIORITY_HIGH
from ticket_triage.priority.base import PriorityRule
from ticket_triage.priority.engine import PriorityEngine
from ticket_triage.priority.keyword_rule import Keyword, KeywordRule
from ticket_triage.priority.repeat_rule import RepeatContactRule
from ticket_triage.priority.waiting_rule import WaitingTimeRule


@dataclass(frozen=True)
class RuleSetting:
    enabled: bool = True
    weight: float = 1.0
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkerSettings:
    check_interval_seconds: float
    escalate_after: timedelta


DEFAULT_RULE_SETTINGS: dict[str, RuleSetting] = {
    "keyword": RuleSetting(params={"baseline": 10}),
    "repeat_contact": RuleSetting(params={"window_days": 7, "steps": {"1": 0, "2": 50, "3": 80}}),
    "waiting_time": RuleSetting(params={"cap_hours": 72}),
    "llm_urgency": RuleSetting(enabled=False, params={"model": "claude-haiku-5-5"}),
}

DEFAULT_KEYWORDS: list[Keyword] = [
    Keyword("outage", 90, PRIORITY_HIGH),
    Keyword("urgent", 80),
    Keyword("locked out", 80),
    Keyword("payment failed", 70),
    Keyword("charged", 60),
    Keyword("invoice", 30),
    Keyword("feature request", 10),
]

DEFAULT_APP_SETTINGS: dict[str, str] = {
    "high_threshold": "70",
    "medium_threshold": "40",
    "worker_check_interval_seconds": "60",
    "escalate_after_hours": "24",
}


def _keyword_rule(setting: RuleSetting, keywords: list[Keyword]) -> PriorityRule:
    return KeywordRule(keywords, weight=setting.weight, enabled=setting.enabled, **setting.params)


def _repeat_rule(setting: RuleSetting, keywords: list[Keyword]) -> PriorityRule:
    params = dict(setting.params)
    if "steps" in params:
        params["steps"] = {int(count): score for count, score in params["steps"].items()}
    return RepeatContactRule(weight=setting.weight, enabled=setting.enabled, **params)


def _waiting_rule(setting: RuleSetting, keywords: list[Keyword]) -> PriorityRule:
    return WaitingTimeRule(weight=setting.weight, enabled=setting.enabled, **setting.params)


def _llm_rule(setting: RuleSetting, keywords: list[Keyword]) -> PriorityRule:
    from ticket_triage.priority.llm_rule import LLMUrgencyRule

    return LLMUrgencyRule(weight=setting.weight, enabled=setting.enabled, **setting.params)


# Every known rule, in the order the engine runs them.
RULE_BUILDERS: dict[str, Callable[[RuleSetting, list[Keyword]], PriorityRule]] = {
    "keyword": _keyword_rule,
    "repeat_contact": _repeat_rule,
    "waiting_time": _waiting_rule,
    "llm_urgency": _llm_rule,
}


def default_settings() -> tuple[dict[str, RuleSetting], list[Keyword]]:
    return dict(DEFAULT_RULE_SETTINGS), list(DEFAULT_KEYWORDS)


def build_rules(settings: dict[str, RuleSetting], keywords: list[Keyword]) -> list[PriorityRule]:
    """Build rule objects from settings. Raises ValueError for unknown rules or bad params."""
    unknown = set(settings) - set(RULE_BUILDERS)
    if unknown:
        raise ValueError(f"unknown rule(s) {sorted(unknown)}; known rules: {list(RULE_BUILDERS)}")

    rules = []
    for name, builder in RULE_BUILDERS.items():
        if name not in settings:
            continue
        try:
            rules.append(builder(settings[name], keywords))
        except TypeError as exc:  # Unknown parameter name in params
            raise ValueError(f"bad params for rule {name!r}: {exc}") from exc
    return rules


def _build_engine(
    settings: dict[str, RuleSetting], keywords: list[Keyword], app: dict[str, str]
) -> PriorityEngine:
    return PriorityEngine(
        build_rules(settings, keywords),
        high_threshold=float(app["high_threshold"]),
        medium_threshold=float(app["medium_threshold"]),
    )


def _worker_settings(app: dict[str, str]) -> WorkerSettings:
    interval = float(app["worker_check_interval_seconds"])
    hours = float(app["escalate_after_hours"])
    if interval <= 0:
        raise ValueError(f"worker_check_interval_seconds must be more than 0, got {interval}")
    if hours <= 0:
        raise ValueError(f"escalate_after_hours must be more than 0, got {hours}")
    return WorkerSettings(interval, timedelta(hours=hours))


# --- SQLite ------------------------------------------------------------------


def seed_defaults(conn: sqlite3.Connection) -> None:
    """Insert default settings that are missing. Never overwrites a manager's changes."""
    conn.executemany(
        "INSERT OR IGNORE INTO rule_settings (rule_name, enabled, weight, params) VALUES (?, ?, ?, ?)",
        [
            (name, int(s.enabled), s.weight, json.dumps(s.params))
            for name, s in DEFAULT_RULE_SETTINGS.items()
        ],
    )
    # Keywords are seeded only into an empty table, so a deleted keyword stays deleted.
    if conn.execute("SELECT COUNT(*) FROM priority_keywords").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO priority_keywords (keyword, score, min_level) VALUES (?, ?, ?)",
            [(kw.phrase, kw.score, kw.min_level) for kw in DEFAULT_KEYWORDS],
        )
    conn.executemany(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
        list(DEFAULT_APP_SETTINGS.items()),
    )
    conn.commit()


def load_rule_settings(conn: sqlite3.Connection) -> dict[str, RuleSetting]:
    rows = conn.execute("SELECT rule_name, enabled, weight, params FROM rule_settings")
    return {
        row["rule_name"]: RuleSetting(
            enabled=bool(row["enabled"]),
            weight=float(row["weight"]),
            params=json.loads(row["params"]) if row["params"] else {},
        )
        for row in rows
    }


def load_keywords(conn: sqlite3.Connection) -> list[Keyword]:
    rows = conn.execute("SELECT keyword, score, min_level FROM priority_keywords ORDER BY keyword")
    return [Keyword(row["keyword"], row["score"], row["min_level"]) for row in rows]


def load_app_settings(conn: sqlite3.Connection) -> dict[str, str]:
    stored = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM app_settings")}
    return {**DEFAULT_APP_SETTINGS, **stored}


def load_engine(conn: sqlite3.Connection) -> PriorityEngine:
    """Build the engine from the settings stored in the database."""
    return _build_engine(load_rule_settings(conn), load_keywords(conn), load_app_settings(conn))


def load_worker_settings(conn: sqlite3.Connection) -> WorkerSettings:
    return _worker_settings(load_app_settings(conn))


def _expect(value: Any, kind: type, what: str) -> Any:
    """Return value unchanged, or raise ValueError if it is not of the JSON type `kind`."""
    if not isinstance(value, kind):
        names = {dict: "an object", list: "a list", bool: "true or false"}
        raise ValueError(f"{what} must be {names[kind]}, got {value!r}")
    return value


def import_rules(conn: sqlite3.Connection, path: Path | str) -> None:
    """
    Apply a manager's JSON settings file. Shape (every section optional):

        {
          "rules":    {"waiting_time": {"enabled": true, "weight": 0.5, "params": {"cap_hours": 48}}},
          "keywords": [{"keyword": "outage", "score": 90, "min_level": "HIGH"}],
          "settings": {"escalate_after_hours": 12}
        }

    "rules" and "settings" update only the entries given. "keywords" replaces
    the whole keyword list. Everything is validated before anything is saved.
    """
    with Path(path).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("rules file must contain a JSON object")
    unknown_sections = set(data) - {"rules", "keywords", "settings"}
    if unknown_sections:
        raise ValueError(f"unknown section(s) {sorted(unknown_sections)} in rules file")

    settings = load_rule_settings(conn)
    for name, changes in _expect(data.get("rules", {}), dict, '"rules"').items():
        _expect(changes, dict, f"rule {name!r}")
        unknown_fields = set(changes) - {"enabled", "weight", "params"}
        if unknown_fields:
            raise ValueError(f"unknown field(s) {sorted(unknown_fields)} for rule {name!r}")
        current = settings.get(name, RuleSetting())
        settings[name] = replace(
            current,
            enabled=_expect(changes.get("enabled", current.enabled), bool, f"{name!r} enabled"),
            weight=float(changes.get("weight", current.weight)),
            params={
                **current.params,
                **_expect(changes.get("params", {}), dict, f"{name!r} params"),
            },
        )

    keywords = load_keywords(conn)
    if "keywords" in data:
        keywords = []
        for item in _expect(data["keywords"], list, '"keywords"'):
            _expect(item, dict, "each keyword")
            keywords.append(Keyword(str(item["keyword"]), item["score"], item.get("min_level")))
        phrases = [kw.phrase for kw in keywords]
        duplicates = sorted({p for p in phrases if phrases.count(p) > 1})
        if duplicates:
            raise ValueError(f"duplicate keyword(s) {duplicates}")

    app = load_app_settings(conn)
    _expect(data.get("settings", {}), dict, '"settings"')
    unknown_keys = set(data.get("settings", {})) - set(DEFAULT_APP_SETTINGS)
    if unknown_keys:
        raise ValueError(f"unknown setting(s) {sorted(unknown_keys)}")
    app.update({key: str(value) for key, value in data.get("settings", {}).items()})

    # Validate by building exactly what a run would build.
    _build_engine(settings, keywords, app)
    _worker_settings(app)

    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO rule_settings (rule_name, enabled, weight, params) VALUES (?, ?, ?, ?)",
            [(n, int(s.enabled), s.weight, json.dumps(s.params)) for n, s in settings.items()],
        )
        if "keywords" in data:
            conn.execute("DELETE FROM priority_keywords")
            conn.executemany(
                "INSERT INTO priority_keywords (keyword, score, min_level) VALUES (?, ?, ?)",
                [(kw.phrase, kw.score, kw.min_level) for kw in keywords],
            )
        conn.executemany(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", list(app.items())
        )
