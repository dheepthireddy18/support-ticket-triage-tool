-- SQLite Schema for Support Ticket Triage Tool
-- Tables: tickets (valid, actionable), rejected_tickets (audit/DLQ),
-- and ticket_rule_results (why each ticket got its priority).
-- Priority rule settings live in data/priority_rules.json, not here.

CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    content_hash TEXT UNIQUE NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    customer TEXT NOT NULL,
    created_at TEXT NOT NULL,            -- Source timestamp from input file (ISO-8601 UTC)
    inserted_at TEXT NOT NULL,           -- Timestamp when record was ingested into DB
    status TEXT NOT NULL DEFAULT 'PENDING',  -- 'PENDING', 'PROCESSING', 'PROCESSED', 'RESOLVED'
    priority_level TEXT,                 -- 'HIGH', 'MEDIUM', 'LOW'
    classified_at TEXT,                  -- Timestamp when priority was assigned
    metadata TEXT,                       -- JSON string of extra unmapped columns
    priority_score REAL,                 -- Weighted average of rule scores (0-100)
    escalation_count INTEGER NOT NULL DEFAULT 0,  -- Times the escalation worker raised the level
    last_escalated_at TEXT,              -- When the escalation worker last raised the level
    resolved_at TEXT                     -- When the ticket was marked RESOLVED
);

CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON tickets(created_at);
CREATE INDEX IF NOT EXISTS idx_tickets_inserted_at ON tickets(inserted_at);
CREATE INDEX IF NOT EXISTS idx_tickets_customer ON tickets(customer, created_at);

CREATE TABLE IF NOT EXISTS rejected_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_record TEXT NOT NULL,            -- JSON string of raw payload
    rejection_reason TEXT NOT NULL,      -- Explicit failure reason
    rejected_at TEXT NOT NULL            -- Timestamp when rejection occurred
);

CREATE INDEX IF NOT EXISTS idx_rejected_tickets_rejected_at ON rejected_tickets(rejected_at);

-- Priority rules: per-rule scores and reasons, so every priority can be explained
CREATE TABLE IF NOT EXISTS ticket_rule_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    rule_name TEXT NOT NULL,             -- Rule name, or 'escalation' for worker bumps
    score REAL,                          -- NULL when the rule abstained
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ticket_rule_results_ticket_id ON ticket_rule_results(ticket_id);
