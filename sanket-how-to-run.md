# How to run the tool and generate the report

Run every command from the project folder. The main flow uses the project's sample ticket file (`data/sample_tickets.json`), so tickets go through intake and the priority rules like real ones.

## 1. Set up (once)

| Command | Purpose |
|---|---|
| `source .venv/bin/activate` | Turn on the project's virtual environment. |
| `pip install -e ".[dev,pdf]"` | Install the tool, pytest and `reportlab` (needed for PDF output). |

## 2. Generate the report from the sample ticket file

| Command | Purpose |
|---|---|
| `python -m ticket_triage --input sample_tickets.json --db data/sample.db` | Load the sample tickets into `data/sample.db`, score them, and write the text report to `reports/daily_summary.txt`. |
| `cat reports/daily_summary.txt` | Show the text report in the terminal. |
| `python -m ticket_triage --input sample_tickets.json --db data/sample.db --format pdf` | Write the PDF report to `reports/daily_summary.pdf` (the tickets are already stored, so none are added again). |
| `open reports/daily_summary.pdf` | Open the PDF (macOS). |

## 3. Generate the report from your own tickets file

Put a JSON file in the `data/` folder: a list of tickets, each with `id` (unique), `subject`, `body`, `customer` and `created_at` (ISO 8601, e.g. `2026-10-19T08:30:00Z`). Use its file name (no folder) with `--input`.

| Command | Purpose |
|---|---|
| `python -m ticket_triage --input my_tickets.json --db data/mine.db --format pdf --output my_report.pdf` | Load `data/my_tickets.json` into its own database and write `reports/my_report.pdf`. Reuse the same `--db` to add more tickets later. |
| `python -m ticket_triage --db data/mine.db --format pdf --output my_report.pdf` | Rebuild the report from the tickets already stored, without loading a file. |

## 4. Useful options

| Command | Purpose |
|---|---|
| `python -m ticket_triage --input sample_tickets.json --db data/sample.db --format pdf --top 3` | Show only the top 3 tickets in each list. |
| `python -m ticket_triage --input sample_tickets.json --db data/sample.db --format pdf --output sample_report.pdf` | Save under a different file name inside `reports/`. |
| `python -m ticket_triage --help` | List all options. |

## 5. Run the tests

| Command | Purpose |
|---|---|
| `pytest` | Run all tests. |
| `pytest tests/test_report_pdf.py -v` | Run only the PDF tests. |

## 6. Optional: demo data with more tickets

| Command | Purpose |
|---|---|
| `python -m ticket_triage.demo` | Create `data/demo.db` with 12 dummy tickets (resets it each time). |
| `python -m ticket_triage --db data/demo.db --format pdf` | Write a PDF from the demo data, which has more HIGH tickets than the default list length. |

## 7. Start fresh

| Command | Purpose |
|---|---|
| `find data -name "*.db*" -delete` | Delete the generated databases in `data/` (`sample_tickets.json` and `priority_rules.json` stay). |
| `find reports -type f -delete` | Delete the generated reports (they are not tracked by Git). |
