"""The Assistant's wiring: which language model the process talks to, and with which key.

Each front door binds its own. The new one, `python -m mailmind serve`, talks to the API through
the Anthropic SDK: front.build calls use("sdk"). Everything else, `python3 server.py`, the command
line scripts and the root test suites, gets the standard-library client, since the old door must
run with nothing installed. Either way, with no key the model is DisabledModel: AI is off and
nothing is sent.

The key is config_key() (mailmind.config.json's "anthropic_api_key", in any credential style,
read through the Accounts context), and only then ANTHROPIC_API_KEY. A config key that won't resolve is no key, never a quiet
switch to the environment's. Both are read at each call, so a new key takes effect at once, and the
model is made again only when the key, ANTHROPIC_BASE_URL or the transport changes.

Every call is counted in the usage ledger, the ai_usage table of the data directory's database,
and passes the cost governor first, whose daily limit is the config's "ai_daily_limit_usd" ($15
unless it names another, domain.limits), read at each call.
"""
import os
import threading

from mailmind.accounts.adapters import config_file, secrets
from mailmind.assistant.adapters import sqlite as answers
from mailmind.assistant.adapters.disabled import DisabledModel
from mailmind.assistant.adapters.sqlite_ledger import SqliteLedger
from mailmind.assistant.adapters.urllib_http import UrllibModel
from mailmind.assistant.application.ask import Asker
from mailmind.assistant.application.asking import Asking
from mailmind.assistant.application.brief import Briefs
from mailmind.assistant.application.bulk import BulkCommands
from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.draft_ahead import DraftAhead
from mailmind.assistant.application.gateway import Gateway
from mailmind.assistant.application.governor import CostGovernor, allowing_over
from mailmind.assistant.application.reply_draft import ReplyDrafts
from mailmind.assistant.application.review import Reviews
from mailmind.assistant.application.rewrite import Rewriter
from mailmind.assistant.application.thread_digest import ThreadDigest
from mailmind.assistant.application.triage import Triage
from mailmind.assistant.domain import limits
from typing import Any

__all__ = ["allowing_over", "answers", "asker", "asking", "briefs", "bulk", "calls", "config_key", "drafting", "gateway", "governor", "key", "limit_micros", "replies", "reviews", "rewriter", "threads", "transport", "triage", "use"]

CONFIG = "mailmind.config.json"
ENV = "ANTHROPIC_API_KEY"


def config_key() -> str | None:
    """The key mailmind.config.json's top-level "anthropic_api_key" names, in any credential style
    but proton_bridge, or None when it names none. Raises when it names one that can't be had."""
    cred = config_file.FILE.read().get("anthropic_api_key")
    if not cred:
        return None
    return secrets.resolve(cred, who="anthropic_api_key").strip()


def key() -> tuple[str, str]:
    """(the API key, where it came from), the key "" when there is none. The config file wins,
    so a MailMind-only key isn't shadowed by one the shell exports for other tools."""
    try:
        k = config_key()
    except Exception:
        return "", CONFIG
    if k is not None:
        return k, CONFIG
    return os.environ.get(ENV, "").strip(), ENV


def _sdk(api_key, base_url, **kw):
    from mailmind.assistant.adapters.anthropic_sdk import AnthropicModel   # the new door's only
    return AnthropicModel(api_key, base_url, **kw)


TRANSPORTS = {"urllib": UrllibModel, "sdk": _sdk}     # each (key, base_url, timeout=, retries=)
NAMES = {"urllib": "urllib", "sdk": "the Anthropic SDK"}


class _Bound:
    """The LanguageModel for the key, base URL and transport in force, made once for each."""

    def __init__(self, transport):
        self.transport = transport
        self._made: tuple[Any, Any] = None, None
        self._lock = threading.Lock()

    def __call__(self):
        api_key = key()[0]
        made_for = (self.transport, api_key, os.environ.get("ANTHROPIC_BASE_URL") or None)
        with self._lock:
            if self._made[0] != made_for:
                transport, api_key, base_url = made_for
                model = TRANSPORTS[transport](api_key, base_url) if api_key else DisabledModel()
                self._made = made_for, model
            return self._made[1]


def limit_micros() -> int:
    """Today's limit on AI spending, in micro-dollars. A config file that can't be read or parsed
    leaves it at the default."""
    try:
        value = config_file.FILE.read().get("ai_daily_limit_usd")
    except (OSError, ValueError):
        value = None
    return round(limits.daily_limit_usd(value) * 1_000_000)


_bound = _Bound("urllib")
LEDGER = SqliteLedger()                  # the ai_usage table in the data directory's database
GOVERNOR = CostGovernor(spent=lambda: GATEWAY.spent_today(), limit=limit_micros, reserved=lambda: LEDGER.reserved())
GATEWAY = Gateway(_bound, LEDGER, governor=GOVERNOR)


def gateway() -> Gateway:
    return GATEWAY


def governor() -> CostGovernor:
    return GOVERNOR


def use(transport: str) -> None:
    """Talk to the API through `transport`: "sdk" or "urllib"."""
    if transport not in TRANSPORTS:
        raise ValueError(f"no transport {transport!r}")
    _bound.transport = transport


def transport() -> str:
    """How this process reaches the API, in words for the startup line."""
    return NAMES[_bound.transport]


def _connect():
    from mailmind.bootstrap.mailbox import store
    return store.conn()


calls = Calls(lambda: gateway(), lambda: key(), lambda: transport(), lambda: governor(), lambda: triage.batch_status())
triage = Triage(calls, lambda: gateway(), answers, _connect)


class _Mail:
    """What the Assistant reads of the mail, from the contexts that hold it."""
    def fetch_body(self, pk):
        from mailmind.bootstrap.mailbox import ingest
        return ingest.fetch_body(pk)

    def pk_now(self, c, key, folder):
        from mailmind.bootstrap.mailbox import store
        return store.pk_now(c, key, folder)

    def accounts(self):
        from mailmind.bootstrap import accounts
        return accounts.accounts()

    def get_message(self, c, pk):
        from mailmind.bootstrap.mailbox import store
        return store.get_message(c, pk)

    def for_asking(self, match, accounts, limit):
        from mailmind.bootstrap import discovery
        return discovery.search.for_asking(match, accounts, limit)

    def brief_rows(self, c, accounts, n):
        from mailmind.bootstrap.mailbox import store
        return store.brief_rows(c, accounts, n)

    def delta_rows(self, c, accounts, since_ts, n):
        from mailmind.bootstrap.mailbox import store
        return store.delta_rows(c, accounts, since_ts, n)

    def sent_since(self, c, accounts, since_ts):
        from mailmind.bootstrap.mailbox import store
        return store.sent_since(c, accounts, since_ts)

    def received_since(self, c, accounts, since_ts):
        from mailmind.bootstrap.mailbox import store
        return store.received_since(c, accounts, since_ts)

    def received_by_bucket(self, c, accounts, since_ts):
        from mailmind.bootstrap.mailbox import store
        return store.received_by_bucket(c, accounts, since_ts)

    def needs_you_rows(self, c, accounts, n):
        from mailmind.bootstrap.mailbox import store
        return store.needs_you_rows(c, accounts, n)

    def awaited_threads(self, c, accounts, n):
        from mailmind.bootstrap import attention
        return attention.book.awaited_threads(c, accounts, n)

    def replylater_due(self, c, accounts):
        from mailmind.bootstrap import attention
        return attention.book.replylater_due(c, accounts)

    def senders_cleaned(self, c, since_iso, accounts):
        from mailmind.bootstrap import curation
        return curation.sqlite.senders_cleaned(c, since_iso, accounts)

    def draft_candidates(self, c, since_ts):
        from mailmind.bootstrap.mailbox import store
        return store.draft_candidates(c, since_ts)

    def thread_stamps(self, c, account, thread_id):
        from mailmind.bootstrap.mailbox import store
        return store.thread_stamps(c, account, thread_id)

    def message_stamp(self, c, pk):
        from mailmind.bootstrap.mailbox import store
        return store.message_stamp(c, pk)

    def thread_anchor_pk(self, c, account, thread_id):
        from mailmind.bootstrap.mailbox import store
        return store.thread_anchor_pk(c, account, thread_id)


class _Search:
    """What a bulk command asks of Discovery."""
    def parse(self, text):
        from mailmind.discovery.api import QuerySpec
        return QuerySpec.parse(text)

    def reach(self, spec, cap, apart, sample):
        from mailmind.bootstrap import discovery
        return discovery.search.reach(spec, cap, apart, sample)

    def sweeps_whole_mailbox(self, matched):
        from mailmind.bootstrap import discovery
        return discovery.search.sweeps_whole_mailbox(matched)


def _looks_human(local):
    from mailmind.bootstrap import curation
    return curation.looks_human(local)


_mail = _Mail()
threads = ThreadDigest(calls, answers, _connect, _mail)
briefs = Briefs(calls, answers, _connect, _mail)
reviews = Reviews(calls, answers, _connect, _mail)
replies = ReplyDrafts(calls, threads, _connect)
rewriter = Rewriter(calls)
asker = Asker(calls, _mail)
asking = Asking(threads, briefs, reviews, rewriter, asker, _connect, _mail)
drafting = DraftAhead(calls, lambda: gateway(), answers, _connect, _mail, replies, _looks_human)
bulk = BulkCommands(calls, _Search())
