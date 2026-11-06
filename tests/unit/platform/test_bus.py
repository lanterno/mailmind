"""mailmind.platform.bus: in-process publish and subscribe, an event's class its topic."""
from dataclasses import dataclass

import pytest

from mailmind.platform.bus import Bus


@dataclass(frozen=True)
class Sent:
    outbox_id: int


@dataclass(frozen=True)
class ResentLater(Sent):
    pass


@dataclass(frozen=True)
class Applied:
    mutation_id: int


def test_each_subscriber_hears_its_topic_in_the_order_they_subscribed():
    bus, heard = Bus(), []
    bus.subscribe(Sent, lambda e: heard.append(("stats", e.outbox_id)))
    bus.subscribe(Sent, lambda e: heard.append(("tabs", e.outbox_id)))
    bus.subscribe(Applied, lambda e: heard.append(("moves", e.mutation_id)))
    bus.publish(Sent(7))
    assert heard == [("stats", 7), ("tabs", 7)]


def test_a_topic_is_its_exact_class_not_a_subclass():
    bus, heard = Bus(), []
    bus.subscribe(Sent, heard.append)
    bus.publish(ResentLater(3))
    assert heard == []


def test_publishing_with_no_subscriber_does_nothing():
    Bus().publish(Sent(1))


def test_a_handler_that_raises_is_logged_and_the_rest_still_hear(capsys):
    bus, heard = Bus(), []

    def broken(e):
        raise RuntimeError("could not reach ada@example.test")
    bus.subscribe(Sent, broken)
    bus.subscribe(Sent, heard.append)
    bus.publish(Sent(2))
    assert heard == [Sent(2)]
    out = capsys.readouterr().out
    assert "[bus] test_a_handler_that_raises_is_logged_and_the_rest_still_hear.<locals>.broken failed on Sent: " \
           "RuntimeError" in out
    assert "example.test" not in out                     # the error's text may carry an address


def test_unsubscribing_stops_only_that_handler():
    bus, heard = Bus(), []
    stats = bus.subscribe(Sent, lambda e: heard.append("stats"))
    bus.subscribe(Sent, lambda e: heard.append("tabs"))
    stats()
    stats()                                              # twice is harmless
    bus.publish(Sent(4))
    assert heard == ["tabs"]
    assert len(bus.subscribers(Sent)) == 1


def test_a_handler_subscribing_while_an_event_is_told_hears_the_next_one():
    bus, heard = Bus(), []

    def first(e):
        heard.append(("first", e.outbox_id))
        if e.outbox_id == 1:
            bus.subscribe(Sent, lambda e: heard.append(("late", e.outbox_id)))
    bus.subscribe(Sent, first)
    bus.publish(Sent(1))
    bus.publish(Sent(2))
    assert heard == [("first", 1), ("first", 2), ("late", 2)]


@pytest.mark.parametrize("topic", [dict, int, "Sent"])
def test_only_a_frozen_dataclass_is_an_event(topic):
    @dataclass
    class Mutable:
        n: int
    for t in (topic, Mutable):
        with pytest.raises(TypeError, match="can't be an event"):
            Bus().subscribe(t, print)
    with pytest.raises(TypeError, match="can't be an event"):
        Bus().publish(Mutable(1))
    with pytest.raises(TypeError, match="can't be an event"):
        Bus().publish({"outbox_id": 1})
