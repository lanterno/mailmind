"""Every test here reads its invites through each reader there is: the hand-rolled one, which the old front door runs on,
and the library one, which the new door runs on. `tests/contract/test_ics_differential.py` says where the two differ."""
import pytest

from mailmind.bootstrap import calendar


@pytest.fixture(autouse=True, params=calendar.KINDS)
def reader(request):
    calendar.use(request.param)
    yield request.param
    calendar.reset()
