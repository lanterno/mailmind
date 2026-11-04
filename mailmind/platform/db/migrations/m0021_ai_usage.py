"""Migration 21 (2026-11-04): the AI usage ledger.

Every call to the Claude API adds to one row: its local day, the feature that made it, the model
asked for, the model that served it ('' when none did) and the outcome. The row counts the calls
and adds up their tokens and cost, so the table stays small however many calls a day makes, and the
day's spending, which the daily limit is checked against, is one indexed sum. Cost is in
micro-dollars (US dollars × 1,000,000), an integer, so totals never drift.
"""


def migrate(c):
    c.executescript("""
      CREATE TABLE IF NOT EXISTS ai_usage(
        day TEXT NOT NULL,
        feature TEXT NOT NULL,
        model TEXT NOT NULL,
        served TEXT NOT NULL DEFAULT '',
        outcome TEXT NOT NULL,
        calls INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        cost_micros INTEGER NOT NULL DEFAULT 0,
        last_ts INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, feature, model, served, outcome));
    """)
