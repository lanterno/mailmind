"""The usage ledger in the app's SQLite database: the ai_usage table (migration 21)."""
from mailmind.assistant.application.ports import Spend
from mailmind.platform import settings
from mailmind.platform.db import database

_ADD = """
INSERT INTO ai_usage(day, feature, model, served, outcome, calls, input_tokens, output_tokens,
                     cache_write_tokens, cache_read_tokens, cost_micros, last_ts)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(day, feature, model, served, outcome) DO UPDATE SET
  calls = calls + excluded.calls,
  input_tokens = input_tokens + excluded.input_tokens,
  output_tokens = output_tokens + excluded.output_tokens,
  cache_write_tokens = cache_write_tokens + excluded.cache_write_tokens,
  cache_read_tokens = cache_read_tokens + excluded.cache_read_tokens,
  cost_micros = cost_micros + excluded.cost_micros,
  last_ts = MAX(last_ts, excluded.last_ts)
"""


class SqliteLedger:
    def _connect(self):
        return database.connect(settings.current().db)

    def record(self, spend: Spend) -> None:
        u = spend.usage
        c = self._connect()
        try:
            c.execute(_ADD, (spend.day, spend.feature, spend.model, spend.served, spend.outcome, spend.calls,
                             u.input_tokens, u.output_tokens, u.cache_write_tokens, u.cache_read_tokens,
                             spend.cost_micros, spend.ts))
            c.commit()
        finally:
            c.close()

    def spent(self, day: str) -> int:
        c = self._connect()
        try:
            return int(c.execute("SELECT COALESCE(SUM(cost_micros), 0) FROM ai_usage WHERE day = ?",
                                 (day,)).fetchone()[0])
        finally:
            c.close()

    def reserved(self) -> int:
        c = self._connect()
        try:
            return int(c.execute("SELECT COALESCE(SUM(estimate_micros), 0) FROM ai_batches "
                                 "WHERE state IN ('submitting', 'running')").fetchone()[0])
        finally:
            c.close()

    def days(self, since: str) -> list[dict]:
        c = self._connect()
        try:
            rows = c.execute("""
                SELECT day, feature, SUM(calls), SUM(input_tokens), SUM(output_tokens),
                       SUM(cache_write_tokens), SUM(cache_read_tokens), SUM(cost_micros)
                FROM ai_usage WHERE day >= ? GROUP BY day, feature ORDER BY day DESC, feature
            """, (since,)).fetchall()
        finally:
            c.close()
        keys = ("day", "feature", "calls", "input_tokens", "output_tokens", "cache_write_tokens",
                "cache_read_tokens", "cost_micros")
        return [dict(zip(keys, r)) for r in rows]
