"""
Priority rules for the Support Ticket Triage Tool.

Each rule subclasses PriorityRule and scores a ticket from 0 to 100. The
PriorityEngine combines rule scores into one score and level per ticket.
Settings live in SQLite (see config.py); results are saved by repository.py.
"""

from ticket_triage.priority.base import (
    LEVELS,
    PriorityRule,
    RuleResult,
    TriageContext,
)
from ticket_triage.priority.engine import (
    PriorityEngine,
    PriorityResult,
    build_default_engine,
)
from ticket_triage.priority.keyword_rule import Keyword, KeywordRule
from ticket_triage.priority.repeat_rule import RepeatContactRule
from ticket_triage.priority.waiting_rule import WaitingTimeRule

__all__ = [
    "LEVELS",
    "Keyword",
    "KeywordRule",
    "PriorityEngine",
    "PriorityResult",
    "PriorityRule",
    "RepeatContactRule",
    "RuleResult",
    "TriageContext",
    "WaitingTimeRule",
    "build_default_engine",
]
