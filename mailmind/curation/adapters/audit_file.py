"""The audit trails, sweep_audit.jsonl and reconcile_audit.jsonl in the data directory: personal, gitignored,
one JSON object a line, and readable by their owner only."""
import json
import os
from datetime import datetime, timezone

from mailmind.kernel.when import now_iso
from mailmind.platform import private, settings


class AuditFile:
    def swept(self, account, rows):
        """Append a reviewable record of everything a sweep trashed."""
        stamp = datetime.now(timezone.utc).isoformat()
        with private.append(settings.current().sweep_audit) as f:
            for r in rows:
                f.write(json.dumps({"kind": "sweep", "ts": stamp, "account": account,
                                    "uid": r["uid"], "from": r["from"],
                                    "subject": r["subject"][:140],
                                    "bucket": r["bucket"], "reason": r["reason"]}) + "\n")

    def cleaned_up(self, from_addr, token, swept, status):
        try:
            with private.append(settings.current().sweep_audit) as f:
                f.write(json.dumps({"kind": "sender_cleanup", "ts": now_iso(), "from": from_addr,
                                    "token": token, "swept": swept, "status": status}) + "\n")
        except Exception:
            pass

    def read(self, limit=120):
        events = []
        for path in (settings.current().sweep_audit, settings.current().reconcile_audit):
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
