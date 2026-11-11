"""A filter at ingest (mailmind.curation.application.filters.Filters.on_arrival), on fakes behind Curation's ports: what the
filters a message just stored fits ask of it, together, and that one which can't be judged asks nothing, so no filter stops a
sync. That ingest sends it through the command path, and the server hears, is tests/integration/curation/test_filters_on_arrival.py."""
from mailmind.curation.application.filters import Filters
from mailmind.curation.domain import filters

STAR = filters.new("from:lin@example.test", True, False)
READ = filters.new("subject:lunch", False, True)
NEVER = filters.new("from:nobody@example.test", True, True)


class Book:
    """The rule book, holding these filters."""
    def __init__(self, *kept):
        self.kept = list(kept)

    def load(self):
        return {"filters": list(self.kept)}


class Fits:
    """Discovery's word on whether a stored message fits a query: it fits those named here, and each question is noted."""
    def __init__(self, *queries, raises=None):
        self.queries, self.raises, self.asked = set(queries), raises, []

    def holds(self, c, pk, q):
        self.asked.append((c, pk, q))
        if self.raises:
            raise self.raises
        return q in self.queries


def test_what_the_filters_a_message_fits_ask_comes_together_and_is_asked_on_the_connection_that_stored_it():
    fits = Fits(STAR["q"], READ["q"])
    assert Filters(Book(STAR, READ, NEVER), fits).on_arrival("the-connection", 7) == {"flagged": True, "seen": True}
    assert fits.asked == [("the-connection", 7, f["q"]) for f in (STAR, READ, NEVER)]


def test_a_message_no_filter_fits_is_asked_nothing_of():
    assert Filters(Book(STAR, READ), Fits()).on_arrival("the-connection", 7) == {}


def test_a_filter_that_cant_be_judged_asks_nothing_and_says_only_the_class_of_why(capsys):
    fits = Fits(STAR["q"], raises=RuntimeError("no index for ada@example.test"))
    assert Filters(Book(STAR), fits).on_arrival("the-connection", 7) == {}
    out = capsys.readouterr().out
    assert "[filters] a filter could not be judged: RuntimeError" in out and "example.test" not in out
