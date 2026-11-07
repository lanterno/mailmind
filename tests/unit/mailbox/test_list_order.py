"""Rows of one time keep one order, so a list read a page at a time holds each of them once."""
import pytest

from fixtures import world as w
from mailmind.bootstrap.mailbox import store


def read_in_pages(app, query):
    rows, offset = [], 0
    while True:
        page = app.get(f"/api/messages?account={w.ADA}&folder=INBOX&limit=2&offset={offset}{query}").json
        rows += [i["pk"] for i in page["items"]]
        offset += 2
        if not page["has_more"]:
            return rows


@pytest.mark.parametrize("query", ["", "&group=thread"])
def test_rows_of_one_time_keep_one_order_from_page_to_page(app, query):
    c = store.conn()
    c.execute("UPDATE messages SET ts=1700000000 WHERE account=? AND folder='INBOX'", (w.ADA,))
    c.commit()
    c.close()
    rows = read_in_pages(app, query)
    assert len(rows) > 2
    assert rows == sorted(rows, reverse=True)
