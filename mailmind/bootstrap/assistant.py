"""The Assistant's wiring: the language model the process talks to, with which key, and whether AI is on at all.

The engine in force is read from what a person chose (#49): AI on or off, its provider, model and URL, which are preferences
in mailmind.config.json, and the key kept for that provider. Claude, the default, goes through the Anthropic SDK; every other
provider through the one adapter that speaks OpenAI's Chat Completions. AI off, turned off or with no key, URL or model to
ask, the model is DisabledModel, and nothing is sent. MAILMIND_MODEL names the Claude model: it wins over the model a person
chose while Claude is the provider, for every feature, background summaries too, and with any other it is ignored. Background
summaries otherwise ask the provider's small model (providers.background_model).

A provider's key is config_key() (mailmind.config.json's "anthropic_api_key", "openai_api_key" and the like, in any
credential style but the Proton Bridge's password, read through the Accounts context), and for Claude only then
ANTHROPIC_API_KEY. A config key that won't resolve is no key, never a quiet switch to the environment's. Each is read at
each call, so a change in Settings takes effect at once, and the model is made again only when what it was made for
changes: the provider, model, URL or key. With AI mode off no key is read at all: not the Keychain, nor 1Password.

Every call is counted in the usage ledger, the ai_usage table of the data directory's database,
and passes the cost governor first, whose daily limit is the config's "ai_daily_limit_usd" ($15
unless it names another, domain.limits), read at each call.
"""
import threading

from mailmind.accounts.adapters import config_file, secrets
from mailmind.accounts.api import PREFERENCE_DEFAULTS, CredentialRef, api_key_who
from mailmind.assistant.adapters import anthropic_sdk, local_servers, openai_chat
from mailmind.assistant.adapters import sqlite as answers
from mailmind.assistant.adapters.anthropic_sdk import AnthropicModel
from mailmind.assistant.adapters.disabled import WHY, DisabledModel
from mailmind.assistant.adapters.openai_chat import ChatModel
from mailmind.assistant.adapters.sqlite_ledger import SqliteLedger
from mailmind.assistant.application.ask import Asker
from mailmind.assistant.application.asking import Asking
from mailmind.assistant.application.assessment import Assessments
from mailmind.assistant.application.brief import Briefs
from mailmind.assistant.application.bulk import BulkCommands
from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.catalog import Catalog
from mailmind.assistant.application.context import ContextAssembler
from mailmind.assistant.application.extract import Extractor
from mailmind.assistant.application.gateway import Gateway
from mailmind.assistant.application.governor import CostGovernor, allowing_over
from mailmind.assistant.application.ports import LanguageModel
from mailmind.assistant.application.prepared_replies import PreparedReplies
from mailmind.assistant.application.reply_draft import ReplyDrafts
from mailmind.assistant.application.review import Reviews
from mailmind.assistant.application.rationale import Rationales
from mailmind.assistant.application.rewrite import Rewriter
from mailmind.assistant.application.screening import Screening
from mailmind.assistant.application.sender_digest import SenderDigest
from mailmind.assistant.application.status import Status
from mailmind.assistant.application.thread_digest import ThreadDigest
from mailmind.assistant.domain import limits, providers
from mailmind.assistant.domain.providers import PROVIDERS, Engine, Wire
from mailmind.bootstrap import accounts, attention, curation, discovery, mailbox
from mailmind.discovery.api import QuerySpec
from mailmind.kernel.ai_provider import AiProvider
from mailmind.platform import environment
from mailmind.platform.db.uow import UnitOfWork
from typing import Any

__all__ = ["WHY", "allowing_over", "answers", "asker", "asking", "assessments", "briefs", "bulk", "calls", "catalog", "config_key", "context", "drafting", "engine", "extractor", "gateway", "governor", "key", "limit_micros", "rationales", "replies", "reviews", "rewriter", "screening", "sender_digest", "status", "threads"]

CONFIG = providers.CONFIG
ENV = environment.ANTHROPIC_API_KEY.name


def config_key(provider: AiProvider = "anthropic") -> str | None:
    """The key mailmind.config.json's top-level "<provider>_api_key" names, in any credential style but proton_bridge, or None
    when it names none. Raises when it names one that can't be had, the Bridge's password among them: it is never a key."""
    who = api_key_who(provider)
    cred = config_file.FILE.read().get(who)
    if not cred:
        return None
    if CredentialRef.parse(cred).style == "proton_bridge":
        raise RuntimeError(f"{who} names the Proton Bridge's password, which is no API key")
    return secrets.resolve(cred, who=who).strip()


def key(provider: AiProvider = "anthropic") -> tuple[str, str]:
    """(`provider`'s API key, where it was looked for), the key "" when there is none. The config file wins, so a
    MailMind-only key isn't shadowed by one the shell exports for other tools; only Claude's has the environment after it."""
    try:
        k = config_key(provider)
    except Exception:
        return "", CONFIG
    if k is not None:
        return k, CONFIG
    if provider != "anthropic":
        return "", ""
    return (environment.ANTHROPIC_API_KEY.value() or "").strip(), ENV


def _preferences() -> dict[str, Any]:
    """The preferences as they stand; their defaults when the file can't be read."""
    try:
        return accounts.config.preferences()
    except (OSError, ValueError):
        return dict(PREFERENCE_DEFAULTS)


def _base_url(provider: AiProvider, chosen: str) -> str:
    """Where `provider` is reached: the URL a person chose for it, else for Claude ANTHROPIC_BASE_URL, else "" for its own."""
    if chosen or provider != "anthropic":
        return chosen
    return environment.ANTHROPIC_BASE_URL.value() or ""


def engine() -> Engine:
    """The AI in force now, from the preferences and the key kept for the provider chosen: none is looked up while AI mode
    is off."""
    prefs = _preferences()
    provider: AiProvider = prefs["ai_provider"]
    api_key, source = key(provider) if prefs["ai_enabled"] else ("", "")
    return providers.in_force(enabled=prefs["ai_enabled"], provider=provider, model=prefs["ai_model"],
                              base_url=_base_url(provider, prefs["ai_base_url"]), key=api_key, key_from=source,
                              every=_every(provider))


def _every(provider: AiProvider) -> str | None:
    """The model every feature asks, in place of the one chosen: MAILMIND_MODEL's, while Claude is the provider."""
    return calls.MODEL if provider == "anthropic" else None


def background_model() -> str:
    """The model background summaries ask now, read with no key looked up: the provider's small model, MAILMIND_MODEL's for
    Claude where it names one, or the one chosen for a model on this Mac or a compatible service."""
    prefs = _preferences()
    provider: AiProvider = prefs["ai_provider"]
    return providers.background_model(provider, prefs["ai_model"], _every(provider))


def _model_for(e: Engine) -> LanguageModel:
    if e.off:
        return DisabledModel(e.off, e.model)
    if PROVIDERS[e.provider].wire == "anthropic":
        return AnthropicModel(e.key, e.base_url, timeout=e.timeout, model=e.model, local=e.local)
    return ChatModel(e.provider, e.base_url, e.key, e.model, timeout=e.timeout, local=e.local)


class _Bound:
    """The LanguageModel for the engine in force, made once for each provider, model, URL, key and reason AI is off."""

    def __init__(self):
        self._made: tuple[Any, Any] = None, None
        self._lock = threading.Lock()

    def __call__(self):
        e = engine()
        made_for = (e.provider, e.model, e.base_url, e.key, e.off)
        with self._lock:
            if self._made[0] != made_for:
                self._made = made_for, _model_for(e)
            return self._made[1]


def _listing_base(provider: AiProvider) -> str:
    """Where a provider's models are listed: the URL a person chose, while it is the provider chosen, else its own."""
    prefs = _preferences()
    return _base_url(provider, prefs["ai_base_url"] if prefs["ai_provider"] == provider else "")


def _list_models(wire: Wire, provider: AiProvider, base_url: str, key: str) -> list[str]:
    if wire == "anthropic":
        return anthropic_sdk.list_models(key, base_url)
    return openai_chat.list_models(provider, base_url, key)


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


_uow = UnitOfWork()


class _Mail:
    """What the Assistant reads of the mail, from the contexts that hold it."""
    def fetch_body(self, pk):
        return mailbox.ingest.fetch_body(pk)

    def copy_now(self, c, key, folder):
        return mailbox.stored_mail.copy_now(c, key, folder)

    def accounts(self):
        return accounts.accounts()

    def weekly_review(self):
        return accounts.config.preferences()["weekly_review"]

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

    def needs_you_count(self, c, accounts):
        return mailbox.stored_mail.needs_you_count(c, accounts)

    def sender_recent(self, c, addr, n):
        return mailbox.stored_mail.sender_recent(c, addr, n)

    def sender_stats(self, c, addrs):
        return mailbox.stored_mail.sender_stats(c, addrs)

    def screener_items(self, account):
        return curation.screener.queue(account)["items"]

    def awaited_threads(self, c, accounts, n):
        return attention.book.awaited_threads(c, accounts, n)

    def awaited_count(self, c, accounts):
        return attention.book.awaited_count(c, accounts)

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
    """The Assistant's objects, made afresh: the spend ledger, the cost governor, the gateway, the status and every
    feature's use case. Importing this module assembles them once; wiring.assemble makes a new set of every context's."""
    global _bound, LEDGER, GOVERNOR, GATEWAY, status, calls, assessments, context, threads, briefs, reviews, replies, \
        rewriter, extractor, asker, asking, drafting, bulk, screening, rationales, sender_digest, catalog
    _bound = _Bound()
    LEDGER = SqliteLedger()                  # the ai_usage table in the data directory's database
    GOVERNOR = CostGovernor(spent=lambda: GATEWAY.spent_today(), limit=limit_micros, reserved=lambda: LEDGER.reserved())
    GATEWAY = Gateway(_bound, LEDGER, governor=GOVERNOR)
    status = Status(lambda: gateway(), engine, lambda: governor(), lambda: assessments.batch_status(),
                    lambda: assessments.summaries_waiting())
    calls = Calls(lambda: gateway(), status, background_model)
    assessments = Assessments(calls, lambda: gateway(), answers, _uow, _mail, lambda: _preferences()["ai_provider"])
    context = ContextAssembler(_mail)
    threads = ThreadDigest(calls, answers, _uow, context)
    briefs = Briefs(calls, answers, _uow, _mail, lambda: assessments.summaries_waiting())
    reviews = Reviews(calls, answers, _uow, _mail)
    replies = ReplyDrafts(calls, context, _uow, _mail)
    rewriter = Rewriter(calls)
    extractor = Extractor(calls, context, _uow, _mail)
    asker = Asker(calls, _mail)
    asking = Asking(threads, briefs, reviews, rewriter, asker, _uow, _mail)
    drafting = PreparedReplies(calls, lambda: gateway(), answers, _uow, _mail, replies, curation.looks_human,
                               lambda: _preferences()["draft_ahead"])
    bulk = BulkCommands(calls, _Search())
    screening = Screening(calls, _uow, _mail)
    rationales = Rationales(calls, _uow, _mail)
    sender_digest = SenderDigest(calls, _uow, _mail)
    catalog = Catalog(lambda provider: key(provider)[0], _listing_base, _list_models, local_servers)


assemble()
