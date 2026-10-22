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

### Options

| Option | Description |
|--------|-------------|
| `--input FILE` | Tickets file inside `data/`. Optional: without it, the report is built from the tickets already in the database. |
| `--output FILE` | Report file name inside `reports/` (default `daily_summary.txt`). |
| `--top N` | How many tickets to list under "Needs attention first" and "Waiting longest" (default `5`, `0` or more). The totals and counts always cover every ticket. |
| `--db PATH` | SQLite database file (default `data/tickets.db`). |
| `--init-db` | Create the database tables and exit. |

```
ticket-triage --input sample_tickets.json --top 3
```

## The Daily Report

The report is plain text, laid out so a support manager can read it top to bottom:

```
Daily Support Ticket Summary
Date: 2026-10-19 12:00 UTC
Total tickets: 13
Rejected records: 2

Needs attention first
---------------------
  demo-001 | ACME | Production outage: orders failing | HIGH | waiting 2d 3h
  ...

Counts by priority
------------------
  HIGH: 6
  MEDIUM: 3
  LOW: 3
  Unclassified: 1

Waiting longest
---------------
  demo-010 | HOOLI | Feature request: dark mode | LOW | waiting 6d 0h
  ...
```

1. **Header**: the date, the total number of tickets and how many records were rejected.
2. **Needs attention first**: HIGH priority tickets, oldest first. This is what a manager should act on first. It shows "No urgent tickets today" when there are none.
3. **Counts by priority**: HIGH, MEDIUM and LOW. An "Unclassified: N" line appears only when some tickets have no priority yet.
4. **Waiting longest**: the oldest tickets of any priority, so nothing is forgotten.

Each ticket row shows the id, customer, subject, priority and how long the ticket has been waiting (for example `2d 3h`, `5h 12m`). Sections are never left blank.

### Demo data

To see a full report without any input file, create a demo database and run the tool on it:

```
python -m ticket_triage.demo                  # creates data/demo.db (reset each time)
python -m ticket_triage --db data/demo.db     # report from the demo database
```

The demo data includes more HIGH tickets than the default `--top`, a tie on creation time, one unclassified ticket and two rejected records.

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
