"""The Assistant's wiring: which language model the process talks to, and with which key.

`python -m mailmind serve` talks to the API through the Anthropic SDK: front.build calls use("sdk").
Everything else, the command line included, gets the standard-library client. Either way, with no
key the model is DisabledModel: AI is off and nothing is sent.

The key is config_key() (mailmind.config.json's "anthropic_api_key", in any credential style,
read through the Accounts context), and only then ANTHROPIC_API_KEY. A config key that won't resolve is no key, never a quiet
switch to the environment's. Both are read at each call, so a new key takes effect at once, and the
model is made again only when the key, ANTHROPIC_BASE_URL or the transport changes.

Every call is counted in the usage ledger, the ai_usage table of the data directory's database,
and passes the cost governor first, whose daily limit is the config's "ai_daily_limit_usd" ($15
unless it names another, domain.limits), read at each call.
"""
import threading

from mailmind.accounts.adapters import config_file, secrets
from mailmind.assistant.adapters import sqlite as answers
from mailmind.assistant.adapters.disabled import DisabledModel
from mailmind.assistant.adapters.sqlite_ledger import SqliteLedger
from mailmind.assistant.adapters.urllib_http import UrllibModel
from mailmind.assistant.application.ask import Asker
from mailmind.assistant.application.asking import Asking
from mailmind.assistant.application.assessment import Assessments
from mailmind.assistant.application.brief import Briefs
from mailmind.assistant.application.bulk import BulkCommands
from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.context import ContextAssembler
from mailmind.assistant.application.extract import Extractor
from mailmind.assistant.application.gateway import Gateway
from mailmind.assistant.application.governor import CostGovernor, allowing_over
from mailmind.assistant.application.prepared_replies import PreparedReplies
from mailmind.assistant.application.reply_draft import ReplyDrafts
from mailmind.assistant.application.review import Reviews
from mailmind.assistant.application.rationale import Rationales
from mailmind.assistant.application.rewrite import Rewriter
from mailmind.assistant.application.screening import Screening
from mailmind.assistant.application.sender_digest import SenderDigest
from mailmind.assistant.application.status import Status
from mailmind.assistant.application.thread_digest import ThreadDigest
from mailmind.assistant.domain import limits
from mailmind.bootstrap import accounts, attention, curation, discovery, mailbox
from mailmind.discovery.api import QuerySpec
from mailmind.platform import environment
from mailmind.platform.db.uow import UnitOfWork
from typing import Any

__all__ = ["allowing_over", "answers", "asker", "asking", "assessments", "briefs", "bulk", "calls", "config_key", "context", "drafting", "extractor", "gateway", "governor", "key", "limit_micros", "rationales", "replies", "reviews", "rewriter", "screening", "sender_digest", "status", "threads", "transport", "use"]

CONFIG = "mailmind.config.json"
ENV = environment.ANTHROPIC_API_KEY.name


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
    return (environment.ANTHROPIC_API_KEY.value() or "").strip(), ENV


def _sdk(api_key, base_url, **kw):
    from mailmind.assistant.adapters.anthropic_sdk import AnthropicModel   # front.build's only
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
        made_for = (self.transport, api_key, environment.ANTHROPIC_BASE_URL.value())
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


_uow = UnitOfWork()


class _Mail:
    """What the Assistant reads of the mail, from the contexts that hold it."""
    def fetch_body(self, pk):
        return mailbox.ingest.fetch_body(pk)

    def copy_now(self, c, key, folder):
        return mailbox.stored_mail.copy_now(c, key, folder)

    def accounts(self):
        return accounts.accounts()

    def get_message(self, c, pk):
        return mailbox.stored_mail.get_message(c, pk)

    def for_asking(self, match, accounts, limit):
        return discovery.search.for_asking(match, accounts, limit)

    def brief_rows(self, c, accounts, n):
        return mailbox.stored_mail.brief_rows(c, accounts, n)

    def delta_rows(self, c, accounts, since_ts, n):
        return mailbox.stored_mail.delta_rows(c, accounts, since_ts, n)

    def sent_since(self, c, accounts, since_ts):
        return mailbox.stored_mail.sent_since(c, accounts, since_ts)

    def received_since(self, c, accounts, since_ts):
        return mailbox.stored_mail.received_since(c, accounts, since_ts)

    def received_by_bucket(self, c, accounts, since_ts):
        return mailbox.stored_mail.received_by_bucket(c, accounts, since_ts)

    def needs_you_rows(self, c, accounts, n):
        return mailbox.stored_mail.needs_you_rows(c, accounts, n)

    def sender_recent(self, c, addr, n):
        return mailbox.stored_mail.sender_recent(c, addr, n)

    def sender_stats(self, c, addrs):
        return mailbox.stored_mail.sender_stats(c, addrs)

    def screener_items(self, account):
        return curation.screener.queue(account)["items"]

    def awaited_threads(self, c, accounts, n):
        return attention.book.awaited_threads(c, accounts, n)

    def replylater_due(self, c, accounts):
        return attention.book.replylater_due(c, accounts)

    def senders_cleaned(self, c, since_iso, accounts):
        return curation.sqlite.senders_cleaned(c, since_iso, accounts)

    def handled_since(self, c, accounts, since_iso):
        return mailbox.stored_mail.handled_since(c, accounts, since_iso)

    def events_from(self, when, limit):
        from mailmind.bootstrap import calendar                 # the reader of invites, which the command line never loads
        return calendar.agenda.upcoming(now=when, limit=limit)["items"]

    def draft_candidates(self, c, since_ts):
        return mailbox.stored_mail.draft_candidates(c, since_ts)

    def thread_stamps(self, c, account, thread_id):
        return mailbox.stored_mail.thread_stamps(c, account, thread_id)

    def message_stamp(self, c, pk):
        return mailbox.stored_mail.message_stamp(c, pk)

    def thread_anchor_pk(self, c, account, thread_id):
        return mailbox.stored_mail.thread_anchor_pk(c, account, thread_id)

    def conversation_rows(self, c, account, by, value):
        return mailbox.stored_mail.conversation_rows(c, account, by, value)

    def message_to_reply_to(self, c, pk):
        return mailbox.stored_mail.message_to_reply_to(c, pk)

    def any_copy(self, c, message_key):
        return mailbox.stored_mail.any_copy(c, message_key)


class _Search:
    """What a bulk command asks of Discovery."""
    def parse(self, text):
        return QuerySpec.parse(text)

    def reach(self, spec, cap, apart, sample):
        return discovery.search.reach(spec, cap, apart, sample)

    def sweeps_whole_mailbox(self, matched):
        return discovery.search.sweeps_whole_mailbox(matched)


_mail = _Mail()


def assemble():
    """The Assistant's objects, made afresh: the spend ledger, the cost governor, the gateway, which talks to the API
    through urllib until front.build picks the SDK, the status and every feature's use case. Importing this module
    assembles them once; wiring.assemble makes a new set of every context's."""
    global _bound, LEDGER, GOVERNOR, GATEWAY, status, calls, assessments, context, threads, briefs, reviews, replies, \
        rewriter, extractor, asker, asking, drafting, bulk, screening, rationales, sender_digest
    _bound = _Bound("urllib")
    LEDGER = SqliteLedger()                  # the ai_usage table in the data directory's database
    GOVERNOR = CostGovernor(spent=lambda: GATEWAY.spent_today(), limit=limit_micros, reserved=lambda: LEDGER.reserved())
    GATEWAY = Gateway(_bound, LEDGER, governor=GOVERNOR)
    status = Status(lambda: gateway(), lambda: key(), lambda: transport(), lambda: governor(),
                    lambda: assessments.batch_status(), lambda: calls.model_label())
    calls = Calls(lambda: gateway(), status)
    assessments = Assessments(calls, lambda: gateway(), answers, _uow, _mail)
    context = ContextAssembler(_mail)
    threads = ThreadDigest(calls, answers, _uow, context)
    briefs = Briefs(calls, answers, _uow, _mail)
    reviews = Reviews(calls, answers, _uow, _mail)
    replies = ReplyDrafts(calls, context, _uow, _mail)
    rewriter = Rewriter(calls)
    extractor = Extractor(calls, context, _uow, _mail)
    asker = Asker(calls, _mail)
    asking = Asking(threads, briefs, reviews, rewriter, asker, _uow, _mail)
    drafting = PreparedReplies(calls, lambda: gateway(), answers, _uow, _mail, replies, curation.looks_human)
    bulk = BulkCommands(calls, _Search())
    screening = Screening(calls, _uow, _mail)
    rationales = Rationales(calls, _uow, _mail)
    sender_digest = SenderDigest(calls, _uow, _mail)


assemble()
