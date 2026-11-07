"""The perimeter: what a request must pass before any route runs.

Both front doors apply these checks, so the two refuse the same requests with the same answers.
Each check reads only the request's headers (any mapping whose .get ignores case: the old
server's message headers and Starlette's alike) and the values passed in, and returns a Refusal,
or None when the request may go on. Standard library only: python3 server.py runs it.

Every request reaches 127.0.0.1, so the danger is another page in the browser:
- a page on a rebound name, which reaches this server with its own Host (`refuse_host`);
- another site's no-cors GET, an <img>, which carries this server's Host and no Origin
  (`refuse_cross_site`);
- another site's POST, which can't carry the page's CSRF token or its Origin (`refuse_mutation`).
The API answers only the page's own requests, which browsers mark Sec-Fetch-Site: same-origin.
Every answer, a refusal included, carries the headers `answer_headers` names, the page the policy
`page_policy` writes, and a mail body's document the ones `mail_frame_headers` names.

Callers import this package and ask it by these names; which module holds each is its own affair.
"""
from mailmind.platform.perimeter.csp import mail_frame_policy, page_policy
from mailmind.platform.perimeter.csrf import refuse_mutation, token_matches
from mailmind.platform.perimeter.errors import Refusal
from mailmind.platform.perimeter.fetch_metadata import refuse_cross_site
from mailmind.platform.perimeter.headers import answer_headers, mail_frame_headers
from mailmind.platform.perimeter.host import refuse_host
from mailmind.platform.perimeter.limits import refuse_body

__all__ = ["Refusal", "answer_headers", "mail_frame_headers", "mail_frame_policy", "page_policy",
           "refuse_body", "refuse_cross_site", "refuse_get", "refuse_host", "refuse_mutation", "refuse_post",
           "token_matches"]


def refuse_get(path, headers, *, port):
    return refuse_host(headers, port) or refuse_cross_site(headers, path)


def refuse_post(path, headers, *, port, token, cap):
    return refuse_body(headers, cap) or refuse_mutation(path, headers, port=port, token=token)
