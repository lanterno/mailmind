"""The reconcile trail: what a reconcile pruned, among the audit trails in the database (mailmind.platform.audit). It writes in
its caller's transaction."""
from collections.abc import Sequence

from mailmind.kernel.row import Connection
from mailmind.kernel.when import now_iso
from mailmind.mailbox.application.ports import AccountPruned
from mailmind.platform import audit


def pruned(c: Connection, results: Sequence[AccountPruned], auto: bool = False) -> None:
    """A record of each account's prune, all of one moment."""
    stamp = now_iso()
    audit.append(c, audit.RECONCILE, [{"kind": "reconcile", "ts": stamp, **({"auto": True} if auto else {}),
                                       "account": r["account"], "pruned": r["pruned"], "live": r["live"]} for r in results])
