"""Each account's syncs in flight (mailbox.application.flights): a removal stops those in the air, and what is to run after
them runs once the last has landed."""
import pytest

from mailmind.mailbox.application.flights import AccountGone, Flights

ADA, BEA = "ada@example.test", "bea@example.test"


def test_a_removal_stops_each_flight_of_the_account_in_the_air_and_no_other():
    flights = Flights()
    with flights.fly(ADA) as ada, flights.fly("Ada@Example.test") as ada_again, flights.fly(BEA) as bea:
        flights.stop("ADA@example.test")
        assert ada.stopped and ada_again.stopped
        assert not bea.stopped
        with pytest.raises(AccountGone):
            ada.going_on()
        bea.going_on()


def test_a_removal_with_nothing_in_the_air_stops_no_later_flight():
    flights = Flights()
    flights.stop(ADA)
    with flights.fly(ADA) as ada:
        assert not ada.stopped


def test_a_flight_landed_is_forgotten_with_its_stop():
    flights = Flights()
    with flights.fly(ADA):
        flights.stop(ADA)
        assert flights.stopped(ADA)
    assert not flights.stopped(ADA)


def test_what_runs_after_the_flights_runs_once_the_last_lands():
    flights, ran = Flights(), []
    with flights.fly(ADA):
        with flights.fly(ADA):
            assert flights.after(ADA, lambda: ran.append(ADA))
        assert ran == []
    assert ran == [ADA]


def test_a_flight_that_ends_in_trouble_lands_too():
    flights, ran = Flights(), []
    with pytest.raises(OSError), flights.fly(ADA):
        flights.after(ADA, lambda: ran.append(ADA))
        raise OSError()
    assert ran == [ADA]


def test_with_none_in_the_air_nothing_waits_to_run():
    flights, ran = Flights(), []
    assert not flights.after(ADA, lambda: ran.append(ADA))
    with flights.fly(ADA):
        pass
    assert ran == []
