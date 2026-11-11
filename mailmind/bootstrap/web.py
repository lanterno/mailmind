"""The routes, over the contexts, on the routers the app serves."""
from mailmind.bootstrap import accounts, assistant, attention, background, calendar, compose, curation, discovery, mailbox
from mailmind.http.routers import accounts as accounts_routes
from mailmind.http.routers import assistant as assistant_routes
from mailmind.http.routers import attention as attention_routes
from mailmind.http.routers import calendar as calendar_routes
from mailmind.http.routers import compose as compose_routes
from mailmind.http.routers import curation as curation_routes
from mailmind.http.routers import debug, events, export, places, static
from mailmind.http.routers import discovery as discovery_routes
from mailmind.http.routers import mailbox as mailbox_routes
from mailmind.http.routers import preferences as preferences_routes
from mailmind.http.shared import AI_OVER_HEADER, post_body_cap, tell_of_someone

__all__ = ["AI_OVER_HEADER", "ROUTERS", "post_body_cap"]

ROUTERS = [attention_routes.router(attention, mailbox),
           calendar_routes.router(calendar, mailbox),
           mailbox_routes.router(mailbox),
           accounts_routes.router(accounts, mailbox),
           preferences_routes.router(accounts, assistant),
           debug.router(mailbox, compose, background, assistant),
           curation_routes.router(curation, mailbox),
           assistant_routes.router(assistant, mailbox),
           compose_routes.router(compose, mailbox),
           discovery_routes.router(discovery),
           events.router(),
           export.router(),
           places.router(),
           static.router()]

tell_of_someone(lambda: assistant.governor().saw_someone())
