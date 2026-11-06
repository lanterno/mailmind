"""The audit trails, sweep_audit.jsonl and reconcile_audit.jsonl in the data directory: personal, gitignored,
one JSON object a line."""
import json
import os
from datetime import datetime, timezone

from mailmind.kernel.when import now_iso
from mailmind.platform import settings


class AuditFile:
    def swept(self, account, rows):
        """Append a reviewable record of everything a sweep trashed."""
        stamp = datetime.now(timezone.utc).isoformat()
        with open(settings.current().sweep_audit, "a") as f:
            for r in rows:
                f.write(json.dumps({"kind": "sweep", "ts": stamp, "account": account,
                                    "uid": r["uid"], "from": r["from"],
                                    "subject": r["subject"][:140],
                                    "bucket": r["bucket"], "reason": r["reason"]}) + "\n")

    def cleaned_up(self, from_addr, token, swept, status):
        try:
            with open(settings.current().sweep_audit, "a") as f:
                f.write(json.dumps({"kind": "sender_cleanup", "ts": now_iso(), "from": from_addr,
                                    "token": token, "swept": swept, "status": status}) + "\n")
        except Exception:
            pass

    def read(self, kind="all", limit=120):
        events = []
        files = []
        if kind in ("all", "sweep"):
            files.append(settings.current().sweep_audit)
        if kind in ("all", "reconcile"):
            files.append(settings.current().reconcile_audit)
        for path in files:
            if not os.path.exists(path):
                continue
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        continue  # skip a torn final line
        events.sort(key=lambda e: e.get("ts", ""), reverse=True)
        return {"events": events[:limit], "has_more": len(events) > limit}
