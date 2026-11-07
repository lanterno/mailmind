import os
import time

import pytest


@pytest.fixture(params=["UTC", "Asia/Tokyo", "America/New_York"])
def machine_zone(request):
    """This machine's zone for the test: the suite's own UTC can't tell UTC from local time."""
    os.environ["TZ"] = request.param
    time.tzset()
    yield request.param
    os.environ["TZ"] = "UTC"
    time.tzset()
