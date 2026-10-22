# support-ticket-triage-tool

## Project Overview

This Support Ticket Triage Tool is a command-line application that helps support teams identify urgent customer issues.
The tool will:
- Read support tickets from a CSV or JSON file.
- Validate incoming ticket records and flag invalid data.
- Assign priorities to valid tickets.
- Generate a daily summary report for support managers.

## Ticket Schema
|Field|Type|Description|Required|
|-----|----|-----------|--------|
|id|UUID|Unique identifier for each ticket|Yes|
|subject|String|Short summary of the customer's issue|Yes|
|body|String|Detailed description of the issue|Yes|
|customer|String| Customer name or identifier|Yes|
|created_at|ISO 8601 datetime|Date and time the ticket was created|Yes|


Example of a Ticket:
```
{
  "id": "e4eb2955-8d61-49a2-ac15-f016a366951a",
  "subject": "Unable to log in",
  "body": "The customer cannot log in after resetting their password.",
  "customer": "CUST-101",
  "created_at": "2026-10-19T08:30:00Z"
}
```

## Setup

Requires Python 3.10 or newer

```
git clone https://github.com/dheepthireddy18/support-ticket-triage-tool.git
cd support-ticket-triage-tool
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"         # installs the tool and pytest
```

## Running the Triage Tool

```
python -m ticket_triage --input sample_tickets.json
```

or 

```
ticket-triage --input sample_tickets.json --output daily_summary.txt
```

Give file names only, not paths. The input file is read from the `data/` folder and the report is saved in the `reports/` folder (created automatically if it does not exist). `--output` is optional and defaults to `daily_summary.txt`. A sample input file with five valid tickets and three invalid (missing customer, invalid date, duplicate id) is in `data/sample_tickets.json`.

## Priority Rules

Each valid ticket is scored from 0 to 100 by three rules, and the scores are combined into a level: `HIGH`, `MEDIUM` or `LOW`. Each ticket is scored once, the first time the tool sees it: its level goes into the ticket's `priority_level` column, and every rule's score and reason goes into the `ticket_rule_results` table, so you can always see why a ticket got its level:

```
sqlite3 data/tickets.db "SELECT rule_name, score, reason FROM ticket_rule_results WHERE ticket_id = '<id>';"
```

| Rule | What it looks at |
|---|---|
| `keyword` | Urgent words in the subject or body. The highest-scoring match wins, and a keyword can set a minimum level (e.g. `outage` is always at least HIGH). |
| `repeat_contact` | How many tickets the same customer sent in the 7 days up to this one |
| `waiting_time` | Hours since `created_at`, reaching 100 at 72 hours |

The score is the weighted average of the rules. A score of 70 or more is HIGH, 40 or more is MEDIUM, and anything lower is LOW. When rules disagree, the weights decide, except that a keyword's minimum level always wins.

### Changing the rules without editing code

All settings live in `data/priority_rules.json`, which is tracked in Git, so every rule change shows up in a diff:

| Section | What it controls |
|---|---|
| `thresholds` | The scores for `high` (default 70) and `medium` (default 40) |
| `rules` | Each rule's `enabled`, `weight` and `params` |
| `keywords` | The keyword list: each entry has a `keyword`, a `score` and an optional `min_level` |

Anything you leave out keeps its built-in default. A `keywords` list replaces the whole keyword list. An invalid file (an unknown rule, a negative weight, a score outside 0–100, a value of the wrong type) is rejected with a message naming the problem.

### Adding a new rule

1. Subclass `PriorityRule` in `src/ticket_triage/priority/`, give it a unique `name`, and implement `evaluate(ticket, ctx)` so it returns `self.result(score, reason)`.
2. Register it in `RULE_BUILDERS` and `DEFAULT_RULE_SETTINGS` in `priority/config.py`, and add an entry for it under `rules` in `data/priority_rules.json`.

## Running the Tests

```
pytest                              # all tests
pytest tests/test_integration.py -v # integration test only
```

The integration test runs the whole tool as a separate process on the sample
file and checks that the report is written and contains every valid ticket.

The test runs the tool as a separate process, so the project itself must be
installed first. This installs the `ticket_triage` package from `src/`.

```
pip install -e ".[dev]"
```

## Continuous Integration

GitHub Actions:- (`.github/workflows/ci.yml`) runs the full test suite on every
pull request and on pushes to `main`.
