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

After writing the report, the tool keeps running a background **escalation worker** until you press Ctrl+C (see below). Add `--once` to exit straight after the report instead.

## Priority Rules

Every valid ticket gets a score from 0 to 100 and a level (`HIGH`, `MEDIUM` or `LOW`). Both are saved in SQLite (`data/tickets.db` by default, or `--db <path>`), and the report reads them from there.

### How a ticket gets its priority

Each rule scores the ticket from 0 to 100 and gives a reason:

| Rule | What it looks at | Default |
|---|---|---|
| `keyword` | Urgent words in the subject or body. The highest-scoring match wins. | `outage` 90 (always at least HIGH), `urgent` 80, `locked out` 80, `payment failed` 70, `charged` 60, `invoice` 30, `feature request` 10; no match scores 10 |
| `repeat_contact` | How many tickets the same customer sent in the 7 days up to this one, counting earlier runs stored in the database | 1 ticket scores 0, 2 score 50, 3 or more score 80 |
| `waiting_time` | Hours since `created_at` | Rises evenly from 0 to 100 at 72 hours |
| `llm_urgency` | Claude rates the urgency (optional, off by default) | Gives no score when no API key is set or the call fails |

The score is the **weighted average** of the rules that gave a score. A score of 70 or more is HIGH, 40 or more is MEDIUM, and anything lower is LOW. **When rules disagree**, the average decides, and a rule with a higher weight counts for more. The one exception is a floor: a keyword such as `outage` makes a ticket at least HIGH, whatever the average is. Every rule's score and reason is saved in the `ticket_rule_results` table, so you can always see why a ticket got its level.

### Changing the rules without editing code

All priority settings live in one file, `data/priority_rules.json`, which is tracked in Git, so every rule change shows up in a diff and can be reviewed. To change a rule, edit the file and run the tool again. The file has four sections:

| Section | What it controls |
|---|---|
| `thresholds` | The scores for `high` (default 70) and `medium` (default 40) |
| `rules` | Each rule's `enabled`, `weight` and `params` |
| `keywords` | The keyword list: each entry has a `keyword`, a `score` and an optional `min_level` floor |
| `worker` | `check_interval_seconds` (default 60) and `escalate_after_hours` (default 24) |

Anything you leave out keeps its built-in default. A `keywords` list replaces the whole keyword list. If the file is invalid (unknown rule, negative weight, score outside 0–100, a value of the wrong type), the tool stops before doing any work and names the problem.

To try different settings without changing the shared file, put a copy in `data/` and pass its name:

```
ticket-triage --input sample_tickets.json --rules my_rules.json
```

If `data/priority_rules.json` is missing, the tool uses the built-in defaults and prints a notice.

### Escalation worker

Every 60 seconds the worker checks open tickets: tickets that have been prioritized but not resolved. A ticket that has stayed at the same level for 24 hours moves up one level (LOW to MEDIUM, MEDIUM to HIGH), and the reason is saved in `ticket_rule_results`. Both intervals are set in the `worker` section of the rules file. You can override them for one run:

```
ticket-triage --input sample_tickets.json --check-interval 30 --escalate-after 12
```

### Resolving a ticket

```
ticket-triage --resolve 7c1d3a52-95b4-4f0e-8a6b-2d9e4f7a1b03
```

This marks the ticket resolved, so it's no longer escalated and drops out of the report. Its priority history is kept. The command exits with 0 when the ticket is resolved or was already resolved, and with 1 when no ticket has that ID.

### Adding a new rule

1. Subclass `PriorityRule` in `src/ticket_triage/priority/`, give it a unique `name`, and implement `evaluate(ticket, ctx)` so it returns `self.result(score, reason)`.
2. Register it in `RULE_BUILDERS` and `DEFAULT_RULE_SETTINGS` in `priority/config.py`, and add an entry for it under `rules` in `data/priority_rules.json`.

The engine, CLI, report and worker need no changes.

### Optional LLM rule

```
pip install -e ".[llm]"
```

The rule uses the class Azure sandbox. Copy `.env.example` to `.env` in the project folder (`.env` is already in `.gitignore`, so Git never sees it) and fill in your values. Put one setting on each line, with no quotes or spaces:

```
AZURE_AI_KEY=<your key>
AZURE_AI_ENDPOINT=<the endpoint>
AZURE_AI_DEPLOYMENT=<the deployment name>
```

The rule calls `{endpoint}/anthropic/v1/messages` and uses the deployment name as the model. If no `AZURE_AI_*` settings are present, it uses `ANTHROPIC_API_KEY` with Anthropic's own API instead. Values already set in your shell win over `.env`.

Then set `"enabled": true` under `llm_urgency` in `data/priority_rules.json`. The tool still works without a key. The rule just gives no score, and its reason in `ticket_rule_results` says why (for example `incomplete Azure settings, missing AZURE_AI_ENDPOINT`).

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
