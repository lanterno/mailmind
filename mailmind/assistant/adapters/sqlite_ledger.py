"""The usage ledger in the app's SQLite database: the ai_usage table (migration 21).

Every count of tokens and cost it adds is held at the most a column holds (pricing.MOST, 2^63-1): a row's upsert in SQL,
and a day's totals as they are read, so an absurd usage stays an INTEGER that is counted rather than a REAL, or an
overflow, that is lost."""
from mailmind.assistant.application.ports import Spend
from mailmind.assistant.domain.pricing import MOST
from mailmind.platform.db.uow import UnitOfWork

_ADD = f"""
INSERT INTO ai_usage(day, feature, model, served, outcome, calls, input_tokens, output_tokens,
                     cache_write_tokens, cache_read_tokens, cost_micros, last_ts)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(day, feature, model, served, outcome) DO UPDATE SET
  calls = calls + excluded.calls,
  input_tokens = MIN(input_tokens + excluded.input_tokens, {MOST}),
  output_tokens = MIN(output_tokens + excluded.output_tokens, {MOST}),
  cache_write_tokens = MIN(cache_write_tokens + excluded.cache_write_tokens, {MOST}),
  cache_read_tokens = MIN(cache_read_tokens + excluded.cache_read_tokens, {MOST}),
  cost_micros = MIN(cost_micros + excluded.cost_micros, {MOST}),
  last_ts = MAX(last_ts, excluded.last_ts)
"""
_COLUMNS = ("calls", "input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens", "cost_micros")


def _total(counts) -> int:
    """`counts` added up, held at MOST."""
    return min(sum(int(n or 0) for n in counts), MOST)


_uow = UnitOfWork()


class SqliteLedger:
    def record(self, spend: Spend) -> None:
        u = spend.usage
        with _uow.write() as c:
            c.execute(_ADD, (spend.day, spend.feature, spend.model, spend.served, spend.outcome, spend.calls,
                             u.input_tokens, u.output_tokens, u.cache_write_tokens, u.cache_read_tokens,
                             spend.cost_micros, spend.ts))

    def spent(self, day: str) -> int:
        with _uow.read() as c:
            return _total(r[0] for r in c.execute("SELECT cost_micros FROM ai_usage WHERE day = ?", (day,)))

    def reserved(self) -> int:
        with _uow.read() as c:
            return _total(r[0] for r in c.execute("SELECT estimate_micros FROM ai_batches "
                                                  "WHERE state IN ('submitting', 'running')"))

    def days(self, since: str) -> list[dict]:
        with _uow.read() as c:
            rows = c.execute(f"SELECT day, feature, {', '.join(_COLUMNS)} FROM ai_usage WHERE day >= ? "
                             "ORDER BY day DESC, feature", (since,)).fetchall()
        out: dict[tuple[str, str], dict] = {}
        for day, feature, *counts in rows:
            row = out.setdefault((day, feature), {"day": day, "feature": feature, **dict.fromkeys(_COLUMNS, 0)})
            for name, n in zip(_COLUMNS, counts):
                row[name] = _total((row[name], n))
        return list(out.values())
