"""Snooze: the most one request may name, and the presets at the week's edges."""
from datetime import datetime

import pytest

from fixtures import world as w
from stored_rows import q
from world_helpers import RECEIPT, UTC


def test_hwf_snooze_refuses_a_bulk_list_past_its_cap_server_side(app, world):
    """It once snoozed the first 500 and said nothing of the rest, which the page had already taken off the list."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:                                                # 600 copies that are all there, more than one request may snooze
        for i in range(600):
            store.upsert_message(c, {"account": w.ADA, "kind": "imap", "folder": "INBOX", "uid": 10_000 + i,
                                     "message_id": f"<bulk-{i}@example.test>", "gmail_thread": None,
                                     "from_addr": "lin@example.test", "from_name": "Lin Mori", "subject": "Bulk",
                                     "date_iso": None, "ts": 0, "snippet": "", "bucket": "other/personal-ish",
                                     "is_junk": 0})
        c.commit()
    finally:
        c.close()
    copies = q("SELECT message_key AS key, folder FROM messages WHERE subject='Bulk'")
    assert len(copies) == 600
    assert app.post("/api/snooze", {"copies": copies, "preset": "tomorrow"}).status == 400
    assert q("SELECT COUNT(*) AS n FROM snoozes")[0]["n"] == 0
    r = app.post("/api/snooze", {"copies": copies[:500], "preset": "tomorrow"})
    assert r.status == 200 and r.json["count"] == 500


@pytest.mark.parametrize("now, preset, wake", [
    (datetime(2026, 11, 7, 10, 0, tzinfo=UTC), "weekend", datetime(2026, 11, 14, 8, 0, tzinfo=UTC)),   # Saturday
    (datetime(2026, 11, 8, 10, 0, tzinfo=UTC), "weekend", datetime(2026, 11, 14, 8, 0, tzinfo=UTC)),   # Sunday
    (datetime(2026, 11, 8, 10, 0, tzinfo=UTC), "next_week", datetime(2026, 11, 9, 8, 0, tzinfo=UTC)),  # Sunday
])
def test_hwf_weekend_and_next_week_from_the_weekend(app, world, now, preset, wake):
    app.clock.move_to(now)
    r = app.post("/api/snooze", {**world.copy(RECEIPT), "preset": preset}).json
    assert datetime.fromtimestamp(r["until"], UTC) == wake
