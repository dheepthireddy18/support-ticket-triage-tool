-- SQLite Schema for Support Ticket Triage Tool
-- Tables: tickets (valid, actionable) and rejected_tickets (audit/DLQ)

CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    content_hash TEXT UNIQUE NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    customer TEXT NOT NULL,
    created_at TEXT NOT NULL,            -- Source timestamp from input file (ISO-8601 UTC)
    inserted_at TEXT NOT NULL,           -- Timestamp when record was ingested into DB
    status TEXT NOT NULL DEFAULT 'PENDING',  -- 'PENDING', 'PROCESSING', 'PROCESSED'
    priority_level TEXT,                 -- 'HIGH', 'MEDIUM', 'LOW'
    classified_at TEXT,                  -- Timestamp when priority was assigned
    metadata TEXT                        -- JSON string of extra unmapped columns
);

CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON tickets(created_at);
CREATE INDEX IF NOT EXISTS idx_tickets_inserted_at ON tickets(inserted_at);

CREATE TABLE IF NOT EXISTS rejected_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_record TEXT NOT NULL,            -- JSON string of raw payload
    rejection_reason TEXT NOT NULL,      -- Explicit failure reason
    rejected_at TEXT NOT NULL            -- Timestamp when rejection occurred
);

CREATE INDEX IF NOT EXISTS idx_rejected_tickets_rejected_at ON rejected_tickets(rejected_at);

-- Priority rules: each rule's score and reason per ticket, so every priority can be explained
CREATE TABLE IF NOT EXISTS ticket_rule_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    rule_name TEXT NOT NULL,             -- e.g. 'keyword', 'repeat_contact', 'waiting_time'
    score REAL,                          -- 0-100, or NULL when the rule had no opinion
    reason TEXT NOT NULL,                -- Human-readable explanation
    created_at TEXT NOT NULL             -- When the ticket was scored (ISO-8601 UTC)
);

CREATE INDEX IF NOT EXISTS idx_ticket_rule_results_ticket_id ON ticket_rule_results(ticket_id);
