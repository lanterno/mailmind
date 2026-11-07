"""Every test here reads its invites through each reader there is: the hand-rolled one, which a process starts on,
and the library one, which serving asks for. `tests/contract/test_ics_differential.py` says where the two differ."""
import os
import time

import pytest

from mailmind.bootstrap import calendar


@pytest.fixture(autouse=True, params=calendar.KINDS)
def reader(request):
    calendar.use(request.param)
    yield request.param
    calendar.reset()


@pytest.fixture(params=["UTC", "Asia/Tokyo", "America/New_York"])
def machine_zone(request):
    """This machine's zone for the test: the suite's own UTC can't tell UTC from local time."""
    os.environ["TZ"] = request.param
    time.tzset()
    yield request.param
    os.environ["TZ"] = "UTC"
    time.tzset()
