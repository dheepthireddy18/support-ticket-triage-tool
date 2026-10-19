"""Allow running the tool with ``python -m ticket_triage``."""

from ticket_triage.ticket_triage_cli import main

raise SystemExit(main())
