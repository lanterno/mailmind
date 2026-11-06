"""The routes, over the contexts, on the one handler both front doors serve."""
from mailmind.bootstrap import accounts, assistant, attention, background, calendar, compose, curation, discovery, mailbox
from mailmind.http import handler, static
from mailmind.http.handler import AI_OVER_HEADER, H, post_body_cap
from mailmind.http.routers import accounts as accounts_routes
from mailmind.http.routers import assistant as assistant_routes
from mailmind.http.routers import attention as attention_routes
from mailmind.http.routers import calendar as calendar_routes
from mailmind.http.routers import compose as compose_routes
from mailmind.http.routers import curation as curation_routes
from mailmind.http.routers import debug, events, export
from mailmind.http.routers import discovery as discovery_routes
from mailmind.http.routers import mailbox as mailbox_routes
from mailmind.http.routing import Routes

__all__ = ["AI_OVER_HEADER", "H", "post_body_cap"]

handler.bind(Routes(static.routes(), events.routes(), export.routes(), debug.routes(mailbox, compose, background, assistant),
                    mailbox_routes.routes(mailbox), attention_routes.routes(attention), curation_routes.routes(curation),
                    discovery_routes.routes(discovery), calendar_routes.routes(calendar), compose_routes.routes(compose),
                    assistant_routes.routes(assistant, mailbox), accounts_routes.routes(accounts, mailbox)),
             lambda: assistant.governor().saw_someone())
