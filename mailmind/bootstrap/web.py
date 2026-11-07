"""The routes, over the contexts, on the routers and the one handler the app serves."""
from mailmind.bootstrap import accounts, assistant, attention, background, calendar, compose, curation, discovery, mailbox
from mailmind.http import handler, static
from mailmind.http.handler import H
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
from mailmind.http.shared import AI_OVER_HEADER, post_body_cap

__all__ = ["AI_OVER_HEADER", "H", "ROUTERS", "post_body_cap"]

# What has moved to FastAPI. The handler's table below holds the rest until each file has.
ROUTERS = [attention_routes.router(attention),
           calendar_routes.router(calendar),
           mailbox_routes.router(mailbox),
           accounts_routes.router(accounts, mailbox),
           debug.router(mailbox, compose, background, assistant),
           curation_routes.router(curation),
           assistant_routes.router(assistant, mailbox)]

handler.bind(Routes(static.routes(),
                    events.routes(),
                    export.routes(),
                    discovery_routes.routes(discovery),
                    compose_routes.routes(compose)),
             lambda: assistant.governor().saw_someone())
