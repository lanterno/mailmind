"""Discovery's wiring: the one place that binds its ports, to its own adapters and, until those contexts move, to the
legacy modules behind them. The legacy modules and the command line reach Discovery through here.
"""
from mailmind.discovery.adapters import fts, views_file
from mailmind.discovery.adapters.fts import BM25, forget_message, index_message, to_sql, visible
from mailmind.discovery.adapters.sqlite import StoredMail
from mailmind.discovery.api import QuerySpec
from mailmind.discovery.application.contacts import Contacts, KnownContacts
from mailmind.discovery.application.documents import DocumentHub
from mailmind.discovery.application.search import Search
from mailmind.discovery.application.suggest import Suggesting
from mailmind.discovery.application.views import SavedViews
from mailmind.mailbox.domain import roles
from mailmind.bootstrap import mailbox

__all__ = ["BM25", "build", "contacts", "documents", "forget_message", "fts", "index_message", "known", "search", "suggesting",
           "views", "visible"]

def build(q):
    """A query as a safe (FTS MATCH, SQL WHERE, params) triple."""
    return to_sql(QuerySpec.parse(q))


class _Folders:
    def role(self, account, folder):
        return mailbox.folders.role(account, folder)

    def role_by_name(self, folder):
        return roles.role_by_name(folder)


class _Accounts:
    def emails(self):
        from mailmind.bootstrap import accounts
        return [a["email"] for a in accounts.accounts()]

    def aliases(self):
        from mailmind.bootstrap.accounts import config
        return config.alias_map()


class _KeptSenders:
    def kept(self):
        from mailmind.bootstrap import curation
        try:                                           # a domain rule isn't knowing a person
            return set(curation.rules.load().get("keep_senders", []))
        except (OSError, ValueError):
            return set()


_mail = StoredMail(_Folders())
views = SavedViews(views_file)
search = Search(_mail, _Accounts())
documents = DocumentHub(_mail, _Accounts())
contacts = Contacts(_mail)
suggesting = Suggesting(_mail)
known = KnownContacts(_mail, _Accounts(), _Folders(), _KeptSenders())
