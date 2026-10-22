# support-ticket-triage-tool

## Project Overview

This Support Ticket Triage Tool is a command-line application that helps support teams identify urgent customer issues.
The tool will:
- Read support tickets from a CSV or JSON file.
- Validate incoming ticket records and flag invalid data.
- Assign priorities to valid tickets.
- Generate a daily summary report for support managers, as text or a PDF, and optionally email it.

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
pip install -e ".[dev,pdf]"     # optional: also installs reportlab for --format pdf
pip install -e ".[dev,pdf,email]"  # optional: also what is needed to email the report
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
| `--output FILE` | Report file name inside `reports/` (default `daily_summary.txt`, or `daily_summary.pdf` with `--format pdf`). |
| `--format txt\|pdf` | Report format (default `txt`). `pdf` needs the optional install described below. |
| `--email` | Email the report to the default recipient (`REPORT_EMAIL_TO` in `.env`). See "Email the report" below. |
| `--email-to ADDRESS` | Email the report to this address instead of the default (repeat for several). Implies `--email`. |
| `--top N` | How many tickets to list under "Needs attention first" and "Waiting longest" (default `5`, `1` or more). The totals and counts always cover every ticket. |
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
Total tickets: 12
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

Waiting longest
---------------
  demo-010 | HOOLI | Feature request: dark mode | LOW | waiting 6d 0h
  ...
```

1. **Header**: the date, the total number of tickets and how many records were rejected.
2. **Needs attention first**: HIGH priority tickets, oldest first. This is what a manager should act on first. It shows "No urgent tickets today" when there are none.
3. **Counts by priority**: HIGH, MEDIUM and LOW. An "Unclassified: N" line appears only if some tickets have no priority yet (for example if a run stopped before they were scored).
4. **Waiting longest**: the oldest tickets of any priority, so nothing is forgotten.

Each ticket row shows the id, customer, subject, priority and how long the ticket has been waiting (for example `2d 3h`, `5h 12m`). Sections are never left blank.

### PDF report (optional)

The same report can be written as a PDF. This needs the optional `reportlab` package, which the rest of the tool does not use:

```
pip install -e ".[pdf]"
ticket-triage --input sample_tickets.json --format pdf     # reports/daily_summary.pdf
```

Without the package, `--format pdf` stops with a message explaining how to install it. The PDF has the same sections and empty states as the text report, laid out as a designed one-page summary on US Letter: a title banner, four headline tiles (total tickets, rejected records, high priority, longest wait), two small charts (tickets by priority and by waiting time) and the three tables. Every page has a footer with the page number. Ticket ids are shortened to their first 8 characters in the tables, and characters outside Western European text are shown as `?`.

### Email the report (optional)

The report can be emailed to the support manager: the text report is the message body and the report file (the PDF with `--format pdf`) is attached. Sending uses Python's built-in mail support. The examples use a Gmail account; any mail provider that offers SMTP works the same way.

**1. Install the email extra**

```
pip install -e ".[email]"
```

It adds `python-dotenv` (reads the settings from `.env`) and `truststore` (checks the mail server's certificate against your computer's certificates; see Troubleshooting).

**2. Set up the Gmail account (once)**

Gmail does not accept your normal password from other programs. You need an *app password*: a 16-character password that only this tool uses.

1. Turn on 2-Step Verification: open your [Google Account](https://myaccount.google.com), go to Security, then "2-Step Verification", and turn it on. App passwords are only available with it on.
2. Create the app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords): enter a name such as `ticket-triage` and click Create. Copy the 16 characters Google shows (it will not show them again). You can delete the app password on the same page at any time.
3. If you do not see the app passwords page, you are probably on a work or school account whose administrator turned it off, or on an account with Advanced Protection or security-key-only 2-Step Verification. A personal `@gmail.com` account normally works.

See Google's help page, [Sign in with app passwords](https://support.google.com/accounts/answer/185833), for details.

**3. Add the settings to `.env`**

Copy `.env.example` to `.env` if you do not have one, and add these lines (one per line, no quotes, no spaces around `=`). `.env` is in `.gitignore`: never commit it or paste the password anywhere.

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=youraddress@gmail.com
SMTP_PASSWORD=<the 16 characters, without spaces>
SMTP_FROM=youraddress@gmail.com
REPORT_EMAIL_TO=manager@example.com
```

| Setting | Meaning |
|---|---|
| `SMTP_HOST`, `SMTP_PORT` | The mail server. Port 587 upgrades to TLS before logging in; port 465 uses SSL from the start. |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | Your full Gmail address and the app password. Both can be left out for a local test server. |
| `SMTP_FROM` | The sender address. Defaults to `SMTP_USERNAME` when that is an address. |
| `REPORT_EMAIL_TO` | The default recipient. Several addresses can be separated by commas. To send to yourself, use your own address. |

**4. Send the report**

```
ticket-triage --input sample_tickets.json --format pdf --email                       # to REPORT_EMAIL_TO
ticket-triage --input sample_tickets.json --format pdf --email-to boss@example.com    # to another address
```

The tool prints `Report emailed to <address>`. The message arrives with the subject "Daily Support Ticket Summary - <date>", and also appears in the sender's Sent folder.

**What happens when something is wrong**

Missing or invalid settings and addresses stop the run before any work is done, with a message saying what to fix. If the email cannot be sent, the report is still saved, the problem is printed, and the exit code is 2. The password is never printed, and it is never sent over a connection that cannot be encrypted.

**Troubleshooting**

| Message | Cause and fix |
|---|---|
| `SMTP_HOST is not set` | The settings are not being read. Run the command from the project folder and check that `.env` has the lines above (and that the `email` extra is installed). |
| `the server rejected the login` | The password is not an app password, was copied with a typo or spaces, or was revoked (Google revokes app passwords when you change your account password). Create a new one. Check that `SMTP_USERNAME` is your full address. |
| `CERTIFICATE_VERIFY_FAILED` | Python cannot verify the server's certificate. Run `pip install -e ".[email]"` (adds `truststore`). The other fix on a Mac with Python from python.org is to run "Install Certificates.command" from the Python folder in Applications. A company network that inspects HTTPS also needs `truststore`. |
| `no recipient` | Set `REPORT_EMAIL_TO` in `.env`, or pass `--email-to ADDRESS`. |
| Nothing in the Inbox | Check Spam, and check the sender's Sent folder to confirm Gmail accepted it. |

The report contains ticket subjects and customer names, so only send it to addresses that should see them. To try it without sending real mail, run a local test inbox such as [Mailpit](https://mailpit.axllent.org) and set `SMTP_HOST=localhost`, `SMTP_PORT=1025` and `SMTP_FROM=triage@example.com`.

### Demo data

To see a full report without any input file, create a demo database and run the tool on it:

```
python -m ticket_triage.demo                  # creates data/demo.db (reset each time)
python -m ticket_triage --db data/demo.db     # report from the demo database
```

The seeder resets the database it writes to, so it refuses to touch a database that holds other tickets unless you pass `--force`. The demo data has 12 tickets, with more HIGH tickets than the default `--top` and a tie on creation time, plus two rejected records. Its priorities are written directly, not scored by the rules, so it has no entries in `ticket_rule_results`.

## Priority Rules

Each valid ticket is scored from 0 to 100 by three rules (plus two optional model-based rules, below), and the scores are combined into a level: `HIGH`, `MEDIUM` or `LOW`. Each ticket is scored once, the first time the tool sees it: its level goes into the ticket's `priority_level` column, and every rule's score and reason goes into the `ticket_rule_results` table, so you can always see why a ticket got its level:

```
sqlite3 data/tickets.db "SELECT rule_name, score, reason FROM ticket_rule_results WHERE ticket_id = '<id>';"
```

| Rule | What it looks at |
|---|---|
| `keyword` | Urgent words in the subject or body. The highest-scoring match wins, and a keyword can set a minimum level (e.g. `outage` is always at least HIGH). |
| `repeat_contact` | How many tickets the same customer sent in the 7 days up to this one |
| `waiting_time` | Hours since `created_at`, reaching 100 at 72 hours |
| `llm_urgency` | Optional, off by default: Claude rates the urgency (see below) |
| `jev_urgency` | Optional, off by default: TypeSafe's Jev decision model places the ticket on an urgency rubric (see below) |

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

### Optional LLM rule

The `llm_urgency` rule asks Claude to rate each ticket's urgency from 0 to 100, with a one-sentence reason. It uses the class Azure sandbox.

1. Install the extra packages:

   ```
   pip install -e ".[llm]"
   ```

2. Copy `.env.example` to `.env` in the project folder and fill in the three values your instructor gave you. `.env` is in `.gitignore`, so Git never sees it. Put one setting on each line, with no quotes or spaces:

   ```
   AZURE_AI_KEY=<your key>
   AZURE_AI_ENDPOINT=<the endpoint>
   AZURE_AI_DEPLOYMENT=<the deployment name>
   ```

   The endpoint is the base address only, without `/openai/v1` or `/anthropic`; the tool adds `/anthropic/v1/messages` itself. The deployment name is used as the model. If no `AZURE_AI_*` settings are present, the rule uses `ANTHROPIC_API_KEY` with Anthropic's own API instead. Values already set in your shell win over `.env`.

3. Turn the rule on by setting `"enabled": true` under `llm_urgency` in `data/priority_rules.json`.

The tool still works without a key or the extra packages. The rule just gives no score, so the other rules decide, and its reason in `ticket_rule_results` says why (for example `incomplete Azure settings, missing AZURE_AI_ENDPOINT`). The rule makes one API call per new ticket, and the class account is shared, so keep it off for large ticket files.

### Optional Jev decision-model rule

The `jev_urgency` rule asks [TypeSafe's Jev](https://docs.typesafe.ai) where the ticket sits on a five-level urgency rubric, from "Can wait" to "Critical". Jev doesn't write text: it returns the ticket's position on the rubric (0 to 4, possibly in between) and how confident it is, and the rule turns that position into 0–100 (for example, 3.0 becomes 75). The rubric is in `URGENCY_LEVELS` in `priority/jev_rule.py`.

1. Add your key to `.env` (see `.env.example`):

   ```
   TYPESAFE_API_KEY=<your key>
   ```

2. Turn the rule on by setting `"enabled": true` under `jev_urgency` in `data/priority_rules.json`. Optional `params`: `min_confidence` (0–1; below it the rule gives no score and the other rules decide), `model` (default `jev-latest`) and `timeout_seconds`.

It needs no extra packages, but install `pip install -e ".[llm]"` anyway if you're on a Mac with Python from python.org: it adds `truststore`, which lets Python check HTTPS certificates using the Mac's keychain. Without it, calls fail with `CERTIFICATE_VERIFY_FAILED` (as does any Python HTTPS call) until you run "Install Certificates.command" from the Python folder in Applications. Like the Claude rule, it gives no score if the key is missing or a call fails, and its reason in `ticket_rule_results` says why. It can run alongside `llm_urgency`, each with its own weight.

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
