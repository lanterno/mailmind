"""What the app says of its own running. Ids and counts, and of a job's error its class's name. The page reads none of this."""
from typing import Literal

from mailmind.http.schemas.common import Answer, Number, QueryArgs


class DaysArgs(QueryArgs):
    days: Number = 30               # the route holds it to 1 to 366


class Job(Answer):
    """`Scheduler.snapshot`. Times are wall-clock seconds, and `next_at` is None while a round runs or no lane does."""
    name: str
    every: float
    runs: int
    failures: int
    running: bool
    last_started: float | None
    last_took: float | None
    last_error: str | None          # the exception's class name
    next_at: float | None


class FeatureCost(Answer):
    calls: int
    cost_micros: int


class DayCost(FeatureCost):
    """`Gateway.usage`: a local day's calls and what they cost, in all and by feature."""
    day: str
    features: dict[str, FeatureCost]


class Batch(Answer):
    """`Assessments.batch_status`."""
    waiting: int
    state: str | None
    estimate_micros: int


class Governor(Answer):
    """`CostGovernor.snapshot`. A spending the ledger can't say is None."""
    spent_today_micros: int | None
    limit_micros: int
    reserved_micros: int | None
    interactive_running: int
    since_interactive: float | None
    since_seen: float
    breaker: list[str]


class Idle(Answer):
    accounts: int
    running: int


class Queues(Answer):
    outbox: dict[str, int]          # rows in each state
    commands: dict[str, int]
    drafts_waiting: int


class AiToday(Answer):
    today: DayCost
    limit_micros: int
    batch: Batch


class AtAGlance(Answer):
    ok: Literal[True]
    profile: str
    generated: str
    jobs: list[Job]
    idle: Idle
    queues: Queues
    breakers: list[str]
    ai: AiToday


class PoolCounters(Answer):
    created: int
    reused: int
    validate_fail: int
    discard_dirty: int


class PoolSlot(Answer):
    open: bool
    uses: int
    age: float | None


class PoolStats(Answer):
    """`Pool.stats`, in counts and with no address: `target` says whether it serves every account (`all`), a list of them or
    those that ask for it (`listed`) or none, and `slots` holds each connection kept, with no account named."""
    ok: Literal[True]
    enabled: bool
    target: Literal["all", "listed", ""]
    counters: PoolCounters
    slots: list[PoolSlot]


class Jobs(Answer):
    ok: Literal[True]
    profile: str
    jobs: list[Job]
    generated: str


class AiCosts(Answer):
    ok: Literal[True]
    days: list[DayCost]
    governor: Governor
    batch: Batch
    generated: str
