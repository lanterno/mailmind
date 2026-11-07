"""How a property test runs. In the suite: a fixed number of cases from a fixed seed, so a failure repeats
and the suite stays fast. MAILMIND_MACHINE_RUNS asks for that many cases with fresh random seeds, as it does
of the queue's machine (tests/unit/mailbox/test_queue_state_machine.py)."""
import os

from hypothesis import HealthCheck, settings

RUNS = int(os.environ.get("MAILMIND_MACHINE_RUNS", "0"))


def runs(cases, **more):
    """Settings for a test that runs `cases` cases in the suite."""
    return settings(max_examples=RUNS or cases, deadline=None, database=None, derandomize=not RUNS,
                    suppress_health_check=list(HealthCheck), report_multiple_bugs=False, **more)
