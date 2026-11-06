"""Discovery's wiring: the one place that binds its ports, to its own adapters and, until those contexts move, to the
legacy modules behind them. The legacy modules and the command line reach Discovery through here.

A folder's role goes through the server module, which `attach` is handed rather than importing: `python3 server.py`
runs as __main__, and an import here would load a second copy with state of its own.
"""
from mailmind.discovery.adapters import fts, views_file
from mailmind.discovery.adapters.fts import BM25, forget_message, index_message, to_sql, visible
from mailmind.discovery.adapters.sqlite import StoredMail
from mailmind.discovery.api import QuerySpec
from mailmind.discovery.application.search import Search
from mailmind.discovery.application.views import SavedViews
from mailmind.mailbox.domain import roles

__all__ = ["BM25", "attach", "build", "forget_message", "fts", "index_message", "search", "views", "visible"]

_server = None


def attach(server):
    """Name the running server module, the one that remembers each account's last LIST."""
    global _server
    _server = server


def build(q):
    """A query as a safe (FTS MATCH, SQL WHERE, params) triple."""
    return to_sql(QuerySpec.parse(q))


class _Folders:
    def role(self, account, folder):
        if _server is None:         # no server, so no LIST to remember
            return roles.role_by_name(folder)
        return _server._folder_role(account, folder)

    def role_by_name(self, folder):
        return roles.role_by_name(folder)


class _Accounts:
    def emails(self):
        import accounts
        return [a["email"] for a in accounts.accounts()]

    def aliases(self):
        import config
        return config.alias_map()


_mail = StoredMail(_Folders())
views = SavedViews(views_file)
search = Search(_mail, _Accounts())
