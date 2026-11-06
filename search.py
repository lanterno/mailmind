"""Parse a MailMind search query into a safe (FTS MATCH, SQL WHERE, params) triple: what the legacy
callers ask of mailmind.discovery, which holds the grammar (domain.query) and the SQL (adapters.fts)."""
from mailmind.discovery.adapters.fts import BM25, to_sql, visible
from mailmind.discovery.domain.query import QuerySpec

__all__ = ["BM25", "build", "visible"]


def build(q):
    return to_sql(QuerySpec.parse(q))
