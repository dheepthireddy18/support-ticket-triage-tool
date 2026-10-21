"""
Background escalation worker.

Runs in a daemon thread started by the CLI. Every `check_interval` seconds it
raises open tickets one level if they have stayed at their level for
`escalate_after` (see repository.escalate_overdue). It opens its own SQLite
connection for each check, since a connection can't be shared across threads;
WAL mode and the busy timeout in db.py let it write alongside the main thread.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from ticket_triage.db import get_db_connection
from ticket_triage.priority.base import utc_now
from ticket_triage.priority.repository import escalate_overdue

logger = logging.getLogger(__name__)


class EscalationWorker:
    def __init__(
        self,
        db_path: Path | str,
        check_interval: float,
        escalate_after: timedelta,
        clock: Callable[[], datetime] = utc_now,
        on_escalate: Callable[[list[str]], None] | None = None,
    ) -> None:
        if check_interval <= 0:
            raise ValueError(f"check_interval must be more than 0, got {check_interval}")
        if escalate_after <= timedelta(0):
            raise ValueError(f"escalate_after must be more than 0, got {escalate_after}")
        if str(db_path) == ":memory:":
            raise ValueError("the worker needs a database file; ':memory:' is per-connection")
        self.db_path = db_path
        self.check_interval = float(check_interval)
        self.escalate_after = escalate_after
        self.clock = clock
        self.on_escalate = on_escalate
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_once(self) -> list[str]:
        """Do one check. Returns the ids of tickets escalated."""
        conn = get_db_connection(self.db_path)
        try:
            escalated = escalate_overdue(conn, self.clock(), self.escalate_after)
        finally:
            conn.close()
        if escalated and self.on_escalate:
            self.on_escalate(escalated)
        return escalated

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:  # Log and keep going; a dead worker would fail silently
                logger.exception("escalation check failed")
            self._stop.wait(self.check_interval)

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="escalation-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
