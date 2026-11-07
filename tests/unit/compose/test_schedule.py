"""The time a person asks a send to go at, judged with no clock but the one it's given."""
import pytest

from mailmind.compose.domain.errors import Refused
from mailmind.compose.domain.schedule import FURTHEST, send_after

NOW = 1_800_000_000


@pytest.mark.parametrize("asked", [None, 0, "", "0", False])
def test_no_time_is_now(asked):
    assert send_after(asked, NOW) == 0


@pytest.mark.parametrize("asked, at", [(NOW + 1, NOW + 1), (str(NOW + 60), NOW + 60), (NOW + FURTHEST - 1, NOW + FURTHEST - 1)])
def test_a_time_ahead_is_kept(asked, at):
    assert send_after(asked, NOW) == at


@pytest.mark.parametrize("asked, says", [
    (NOW, "that send time has passed"),
    (NOW - 1, "that send time has passed"),
    (1, "that send time has passed"),
    (NOW + FURTHEST, "pick a send time within a year"),
    (NOW + FURTHEST + 1, "pick a send time within a year"),
    ("soon", "bad send time"),
    ([NOW], "bad send time"),
    ({"at": NOW}, "bad send time"),
    (float("inf"), "bad send time"),
    (float("nan"), "bad send time"),
])
def test_a_time_it_cant_keep_is_refused_as_a_bad_request(asked, says):
    with pytest.raises(Refused) as refused:
        send_after(asked, NOW)
    assert (refused.value.code, refused.value.msg) == (400, says)


def test_the_furthest_is_a_year_and_a_day():
    assert send_after(NOW + 366 * 86400 - 1, NOW) == NOW + 366 * 86400 - 1
