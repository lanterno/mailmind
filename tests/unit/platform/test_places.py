"""A place, as a row of `places`: what a batch keeps, what it refuses before writing anything, what opening one gives and marks,
and that only the newest stay."""
import json
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from mailmind.kernel.errors import Refused
from mailmind.platform.db import places
from mailmind.platform.db.uow import UnitOfWork

FOLDER = {"source": {"kind": "folder", "account": "ada@example.test", "folder": "Lists/Yarn & Wool"}, "opened": None}
SEARCH = {"source": {"kind": "search", "q": "from:bea “quilt”", "account": ""}, "opened": None}
A, B, C = "a" * 22, "B" * 16, "c_-9" * 8
T0 = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def rows():
    with UnitOfWork().read() as c:
        return {r["id"]: (json.loads(r["route"]), r["used_at"]) for r in c.execute("SELECT id, route, used_at FROM places")}


def test_a_batch_is_kept_and_each_place_opens_as_it_was_sent(data_dir):
    places.keep([(A, FOLDER), (B, SEARCH)])
    assert {k: route for k, (route, _) in rows().items()} == {A: FOLDER, B: SEARCH}
    assert json.loads(places.opened(A)) == FOLDER and json.loads(places.opened(B)) == SEARCH


def test_a_route_is_kept_compact_with_a_persons_words_as_they_are(data_dir):
    places.keep([(A, SEARCH)])
    assert places.opened(A) == '{"source":{"kind":"search","q":"from:bea “quilt”","account":""},"opened":null}'


def test_an_id_that_names_none_opens_nothing(data_dir):
    places.keep([(A, FOLDER)])
    assert places.opened(B) is None and places.opened("x") is None and places.opened("../" + A) is None


def test_sending_a_place_again_keeps_one_row_with_what_it_holds_now(data_dir):
    places.keep([(A, FOLDER)])
    places.keep([(A, SEARCH)])
    assert {k: route for k, (route, _) in rows().items()} == {A: SEARCH}


def test_opening_a_place_marks_it_used(data_dir):
    with time_machine.travel(T0, tick=False):
        places.keep([(A, FOLDER)])
    with time_machine.travel(T0 + timedelta(days=3), tick=False):
        places.opened(A)
    assert rows()[A][1] == (T0 + timedelta(days=3)).isoformat()


def test_only_the_newest_are_kept(data_dir, monkeypatch):
    monkeypatch.setattr(places, "KEPT", 3)
    ids = [f"place-{n:011d}" for n in range(5)]
    for n, place in enumerate(ids):
        with time_machine.travel(T0 + timedelta(minutes=n), tick=False):
            places.keep([(place, FOLDER)])
    assert set(rows()) == set(ids[2:])
    with time_machine.travel(T0 + timedelta(minutes=10), tick=False):
        places.opened(ids[2])                                   # used again, so the oldest is now the next one
        places.keep([("place-new-000000", SEARCH)])
    assert set(rows()) == {ids[2], ids[4], "place-new-000000"}


def test_a_batch_sent_at_once_beyond_what_is_kept_keeps_the_last_of_it(data_dir, monkeypatch):
    monkeypatch.setattr(places, "KEPT", 2)
    with time_machine.travel(T0, tick=False):
        places.keep([(A, FOLDER), (B, FOLDER), (C, SEARCH)])
    assert set(rows()) == {B, C}


@pytest.mark.parametrize("place", ["", "short", "a" * 15, "a" * 33, "a b" * 8, "a/" * 9, "ä" * 16, "a" * 21 + ".", None, 7],
                         ids=["empty", "short", "15", "33", "space", "slash", "not-ascii", "dot", "none", "number"])
def test_an_id_the_page_couldnt_have_made_is_refused(data_dir, place):
    with pytest.raises(Refused, match="16 to 32"):
        places.keep([(A, FOLDER), (place, FOLDER)])
    assert rows() == {}


@pytest.mark.parametrize("route", [[], "a route", 7, None, [FOLDER]], ids=["list", "text", "number", "null", "list-of-one"])
def test_a_route_that_isnt_an_object_is_refused(data_dir, route):
    with pytest.raises(Refused, match="JSON object"):
        places.keep([(A, FOLDER), (B, route)])
    assert rows() == {}


def test_a_route_past_its_size_is_refused_and_one_at_it_is_kept(data_dir):
    def of_size(n, word="q"):
        route = {"source": {"kind": "search", "q": "", "account": ""}}
        route["source"]["q"] = word * (n - len(places.text_of(route).encode()))
        return route
    at = of_size(places.MOST_BYTES)
    assert len(places.text_of(at).encode()) == places.MOST_BYTES
    places.keep([(A, at)])
    with pytest.raises(Refused, match=f"at most {places.MOST_BYTES} bytes"):
        places.keep([(B, of_size(places.MOST_BYTES + 1))])
    over_in_bytes = of_size(places.MOST_BYTES - 1000)
    over_in_bytes["source"]["q"] += "é" * 501                  # 501 letters, 1002 bytes
    with pytest.raises(Refused, match="bytes"):
        places.keep([(C, over_in_bytes)])
    assert set(rows()) == {A}


def test_a_batch_past_its_size_is_refused_whole(data_dir):
    batch = [(f"place-{n:011d}", FOLDER) for n in range(places.MOST_AT_ONCE + 1)]
    with pytest.raises(Refused, match=f"at most {places.MOST_AT_ONCE} places"):
        places.keep(batch)
    assert rows() == {}
    places.keep(batch[:-1])
    assert len(rows()) == places.MOST_AT_ONCE


def test_an_empty_batch_keeps_nothing(data_dir):
    places.keep([])
    assert rows() == {}
