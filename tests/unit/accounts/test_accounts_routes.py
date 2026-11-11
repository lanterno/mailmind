"""Accounts' routes on their router: what the schemas refuse, what they leave to the use case's own words, what never gets out."""


import schema_contract
from mailmind.accounts.domain.credential import Typed
from mailmind.accounts.domain.draft import throwaway_account
from mailmind.http.schemas.accounts import AccountDraft
SECRET = "hunter2-the-typed-password"
TYPED = {"style": "keychain", "value": SECRET}
NEW = "dee@example.test"


def test_a_draft_reads_nothing_its_model_doesnt_name(monkeypatch):
    """The contract watches what a use case reads of a request, and a draft is one level in."""
    schema_contract.watch_reads(monkeypatch)
    draft = AccountDraft(email=NEW, type="imap", host="mail.example.test", port=993, starttls=True, strip_spaces=False, credential=TYPED)
    assert throwaway_account(draft.model_dump(exclude_unset=True))["credential"] == Typed(SECRET)
    schema_contract.verify()
