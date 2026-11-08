"""A search read as a condition on one message, which is what a filter is: the same grammar and the same index as the search it
was made from, so a filter and that search can't disagree about what fits."""
from mailmind.discovery.application.ports import SearchIndex
from mailmind.discovery.domain import arrival
from mailmind.discovery.domain.query import QuerySpec


class Conditions:
    def __init__(self, index: SearchIndex):
        self._index = index

    def refusal(self, q: str) -> str | None:
        """Why this query can't be judged of mail as it arrives, in words for a person, or None."""
        return arrival.refusal(q)

    def holds(self, c: object, pk: int, q: str) -> bool:
        """Whether the stored message `pk` is one this query finds, on the connection that holds it."""
        return self._index.fits(c, pk, QuerySpec.parse(q))
