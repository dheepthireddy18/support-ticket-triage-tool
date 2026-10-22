"""
Manager-editable priority settings, read from a JSON file.

The repo ships data/priority_rules.json; a manager changes rules by editing it.
Shape (every section optional; missing parts fall back to the defaults below):

    {
      "thresholds": {"high": 70, "medium": 40},
      "rules":      {"waiting_time": {"enabled": true, "weight": 0.5, "params": {"cap_hours": 48}}},
      "keywords":   [{"keyword": "outage", "score": 90, "min_level": "HIGH"}]
    }

"thresholds" and each entry in "rules" change only the fields they set. "keywords" replaces the whole keyword list. Everything is validated when
the file is read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from ticket_triage.models import PRIORITY_HIGH
from ticket_triage.priority.base import PriorityRule
from ticket_triage.priority.engine import PriorityEngine
from ticket_triage.priority.keyword_rule import Keyword, KeywordRule
from ticket_triage.priority.repeat_rule import RepeatContactRule
from ticket_triage.priority.waiting_rule import WaitingTimeRule

DEFAULT_RULES_FILE = "priority_rules.json"


@dataclass(frozen=True)
class RuleSetting:
    enabled: bool = True
    weight: float = 1.0
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PriorityConfig:
    rules: dict[str, RuleSetting]
    keywords: list[Keyword]
    high_threshold: float
    medium_threshold: float


DEFAULT_RULE_SETTINGS: dict[str, RuleSetting] = {
    "keyword": RuleSetting(params={"baseline": 10}),
    "repeat_contact": RuleSetting(params={"window_days": 7, "steps": {"1": 0, "2": 50, "3": 80}}),
    "waiting_time": RuleSetting(params={"cap_hours": 72}),
    "llm_urgency": RuleSetting(enabled=False, params={"model": "claude-haiku-5-5"}),
    "jev_urgency": RuleSetting(enabled=False, params={"model": "jev-latest"}),
}

DEFAULT_KEYWORDS: list[Keyword] = [
    Keyword("outage", 90, PRIORITY_HIGH),
    Keyword("security", 90, PRIORITY_HIGH),
    Keyword("urgent", 80),
    Keyword("locked out", 80),
    Keyword("payment failed", 70),
    Keyword("charged", 60),
    Keyword("invoice", 30),
    Keyword("feature request", 10),
]

DEFAULT_HIGH_THRESHOLD = 70.0
DEFAULT_MEDIUM_THRESHOLD = 40.0


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
    # Imported here so the tool never needs the LLM code (or its packages) unless asked.
    from ticket_triage.priority.llm_rule import LLMUrgencyRule

    return LLMUrgencyRule(weight=setting.weight, enabled=setting.enabled, **setting.params)


def _jev_rule(setting: RuleSetting, keywords: list[Keyword]) -> PriorityRule:
    from ticket_triage.priority.jev_rule import JevUrgencyRule

    return JevUrgencyRule(weight=setting.weight, enabled=setting.enabled, **setting.params)


# Every known rule, in the order the engine runs them.
RULE_BUILDERS: dict[str, Callable[[RuleSetting, list[Keyword]], PriorityRule]] = {
    "keyword": _keyword_rule,
    "repeat_contact": _repeat_rule,
    "waiting_time": _waiting_rule,
    "llm_urgency": _llm_rule,
    "jev_urgency": _jev_rule,
}


def default_config() -> PriorityConfig:
    """The built-in settings, used when no rules file exists."""
    return PriorityConfig(
        rules=dict(DEFAULT_RULE_SETTINGS),
        keywords=list(DEFAULT_KEYWORDS),
        high_threshold=DEFAULT_HIGH_THRESHOLD,
        medium_threshold=DEFAULT_MEDIUM_THRESHOLD,
    )


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


def build_engine(config: PriorityConfig) -> PriorityEngine:
    return PriorityEngine(
        build_rules(config.rules, config.keywords),
        high_threshold=config.high_threshold,
        medium_threshold=config.medium_threshold,
    )


# --- Reading the file --------------------------------------------------------


def _expect(value: Any, kind: type, what: str) -> Any:
    """Return value unchanged, or raise ValueError if it is not of the JSON type `kind`."""
    if not isinstance(value, kind):
        names = {dict: "an object", list: "a list", bool: "true or false"}
        raise ValueError(f"{what} must be {names[kind]}, got {value!r}")
    return value


def _number(value: Any, what: str) -> float:
    """Return a JSON number as a float. true/false and strings are rejected."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number, got {value!r}")
    return float(value)


def _only(section: dict[str, Any], allowed: set[str], what: str) -> dict[str, Any]:
    unknown = set(section) - allowed
    if unknown:
        raise ValueError(f"unknown field(s) {sorted(unknown)} in {what}")
    return section


def parse_config(data: Any, base: PriorityConfig | None = None) -> PriorityConfig:
    """Apply a parsed rules file on top of `base` (default: the built-in settings)."""
    config = base or default_config()
    _expect(data, dict, "rules file")
    _only(data, {"thresholds", "rules", "keywords"}, "rules file")

    thresholds = _only(
        _expect(data.get("thresholds", {}), dict, '"thresholds"'), {"high", "medium"}, '"thresholds"'
    )
    high = _number(thresholds.get("high", config.high_threshold), "thresholds.high")
    medium = _number(thresholds.get("medium", config.medium_threshold), "thresholds.medium")

    rules = dict(config.rules)
    for name, changes in _expect(data.get("rules", {}), dict, '"rules"').items():
        _only(_expect(changes, dict, f"rule {name!r}"), {"enabled", "weight", "params"}, f"rule {name!r}")
        current = rules.get(name, RuleSetting())
        rules[name] = replace(
            current,
            enabled=_expect(changes.get("enabled", current.enabled), bool, f"{name!r} enabled"),
            weight=_number(changes.get("weight", current.weight), f"{name!r} weight"),
            params={
                **current.params,
                **_expect(changes.get("params", {}), dict, f"{name!r} params"),
            },
        )

    keywords = config.keywords
    if "keywords" in data:
        keywords = []
        for item in _expect(data["keywords"], list, '"keywords"'):
            _only(_expect(item, dict, "each keyword"), {"keyword", "score", "min_level"}, "a keyword")
            if "keyword" not in item or "score" not in item:
                raise ValueError(f'each keyword needs "keyword" and "score", got {item!r}')
            keywords.append(
                Keyword(str(item["keyword"]), _number(item["score"], "keyword score"), item.get("min_level"))
            )
        phrases = [kw.phrase for kw in keywords]
        duplicates = sorted({p for p in phrases if phrases.count(p) > 1})
        if duplicates:
            raise ValueError(f"duplicate keyword(s) {duplicates}")

    result = PriorityConfig(
        rules=rules,
        keywords=keywords,
        high_threshold=high,
        medium_threshold=medium,
    )
    build_engine(result)  # Validate by building exactly what a run would build
    return result


def load_config(path: Path | str | None) -> PriorityConfig:
    """
    Read and validate a rules file. None means "no file": the built-in defaults.
    Raises OSError if the file can't be read and ValueError if it is invalid.
    """
    if path is None:
        return default_config()
    with Path(path).open(encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"not valid JSON: {exc}") from exc
    return parse_config(data)
