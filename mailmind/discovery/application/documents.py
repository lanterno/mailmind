"""The Document Hub: one findable place for every real attachment (PDF/image/doc/…) across all accounts. Pure local SQL
over the attachments table (metadata only: filename/type/size, no body/IMAP fetch). Downloads reuse the existing
/api/message/<pk>/attachment/<part_id> route."""
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from mailmind.discovery.application.ports import AccountNames, StoredDocuments
from mailmind.discovery.domain import documents
from mailmind.kernel.when import rel_time


class DocumentHub:
    def __init__(self, mail: StoredDocuments, accounts: AccountNames):
        self._mail = mail
        self._accounts = accounts

    def page(self, qs: Mapping[str, list[str]]) -> dict[str, Any]:
        """Real attachments across accounts, newest first: the `limit` from `offset` of those that match
        `kind` and `q` (`matched` counts them), with per-kind facet counts of them all. Read-only SQL."""
        try:
            limit = max(1, min(1000, int((qs.get("limit") or ["400"])[0])))
        except Exception:
            limit = 400
        try:
            offset = max(0, int((qs.get("offset") or ["0"])[0]))
        except Exception:
            offset = 0
        want_kind = (qs.get("kind") or [""])[0]
        needle = (qs.get("q") or [""])[0].strip().lower()
        enabled = set(self._accounts.emails())      # match every other corpus-wide endpoint
        if not enabled:
            return {"ok": True, "total": 0, "matched": 0, "shown": 0, "facets": {}, "docs": []}
        docs: list[dict[str, Any]] = []
        facets: dict[str, int] = {}
        seen: set[tuple[Any, ...]] = set()
        matched = 0
        for r in self._mail.attachments(enabled):
            if not documents.is_document(r["content_type"]):
                continue
            kind = documents.kind_of(r["content_type"], r["filename"])
            # dedup the SAME physical file — the identical email synced to two accounts/folders gets a
            # different pk, so key on message_id + size + name (fall back to pk when message_id is absent,
            # so unrelated same-name files aren't wrongly merged).
            fn = (r["filename"] or "").lower()
            key = (r["message_id"], r["size"], fn) if r["message_id"] else (r["pk"], fn)
            if key in seen:
                continue
            seen.add(key)
            facets[kind] = facets.get(kind, 0) + 1
            if want_kind and kind != want_kind:
                continue
            if needle and needle not in " ".join(r[k] or "" for k in ("filename", "from_name", "from_addr", "subject")).lower():
                continue
            matched += 1
            if matched <= offset or len(docs) >= limit:
                continue
            docs.append({"pk": r["pk"], "part_id": r["part_id"], "filename": r["filename"], "kind": kind,
                         "size": r["size"] or 0, "account": r["account"],
                         "from": r["from_name"] or r["from_addr"] or "", "subject": r["subject"] or "(no subject)",
                         "when": rel_time(r["date_iso"]),
                         "url": f"/api/message/{r['pk']}/attachment/{quote(str(r['part_id']))}"})
        return {"ok": True, "total": sum(facets.values()), "matched": matched, "shown": len(docs), "facets": facets,
                "docs": docs}
