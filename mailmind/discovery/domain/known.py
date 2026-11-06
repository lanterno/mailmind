"""Who counts as known, for the composer's "new to you" marks. Known: non-junk mail from them, synced non-draft mail
you sent them, a delivered outbox envelope (Bcc included), or a keep_senders rule."""
import dataclasses
import json
from collections.abc import Callable, Container

from mailmind.kernel.row import Row

MIN_HISTORY = 50     # fewer non-junk messages than this → too little history to call anyone new


@dataclasses.dataclass(frozen=True)
class Facts:
    """What the stored mail says about the addresses asked after. Holds no subject and no date."""
    accounts: list[str]                 # the accounts that hold mail
    senders: list[tuple[str, str]]      # (address, folder): non-junk mail from one of them
    synced: list[tuple[str, str]]       # (account, folder): every folder synced
    draft_folders: set[str]             # where a draft is kept
    copies: list[Row]                   # rows of mail to one of them: addr, account, folder, is_junk, sender
    envelopes: list[str | None]         # what the outbox delivered, as JSON lists
    thin: bool                          # too little history to call anyone new


def vouched(facts: Facts, wanted: set[str], own: Container[str], role_by_name: Callable[[str], str]) -> set[str]:
    """Which of the addresses `wanted` the stored mail vouches for. `own` is your addresses."""
    # junk excluded: one phishing mail can't vouch, whether we or the provider filed it
    hit = {addr for addr, folder in facts.senders if role_by_name(folder) != "junk"}
    sent = {pair for pair in facts.synced if role_by_name(pair[1]) == "sent"}
    for r in facts.copies:
        if r["folder"] in facts.draft_folders:     # the autosaved copy of the message being written must not vouch for its own recipient
            continue
        yours = (r["sender"] in own and not r["is_junk"] and "draft" not in (r["folder"] or "").lower()
                 and role_by_name(r["folder"]) != "junk")   # spam forging your From: vouches for no one
        if yours or (r["account"], r["folder"]) in sent:    # a Sent folder: send-as identities that aren't accounts
            hit.add(r["addr"])
    for env in facts.envelopes:
        try:                               # queued rows may still be undone, or be the typo
            hit |= {str(x).lower() for x in json.loads(env or "[]")} & wanted
        except (ValueError, TypeError):
            continue
    return hit
