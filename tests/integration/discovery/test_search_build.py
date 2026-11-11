"""The search grammar (discovery.build): each operator's (FTS MATCH, WHERE fragments, params) triple,
the promise that no input raises and every MATCH it builds is one SQLite accepts, and two known
bugs in negation. The results these give over the world's mail are pinned in
tests/golden/http_get/search_*.json.
"""


import problems


def test_a_nul_in_the_query_is_a_bad_query_not_a_server_error(app):
    # FTS5 reads the bound MATCH string only up to the NUL, so it sees an unterminated quote
    r = app.get("/api/search?scope=all&q=lunch%00quilt")
    assert (r.status, r.json) == (400, problems.refused("bad query"))


def senders(app, q):
    r = app.get(f"/api/search?scope=all&q={q}").json
    return {i["from_addr"] for i in r["items"]}, len(r["items"])


def test_a_negation_alone_excludes_what_it_names(app, world):
    found, n = senders(app, "-from:lin")
    assert n > 0 and "lin@example.test" not in found


def test_excluding_a_category_keeps_mail_that_has_none(app, world):
    _, everything = senders(app, "in:INBOX")
    assert senders(app, "in:INBOX%20-category:info")[1] == everything     # nothing is summarized here
