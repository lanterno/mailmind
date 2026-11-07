"""What the page asks of Discovery: search, saved views, documents and who you know."""
from typing import Annotated

from fastapi import Query

from mailmind.http import route
from mailmind.http.schemas.common import answers
from mailmind.http.schemas.discovery import (
    BadQuery,
    ContactArgs,
    ContactCard,
    ContactsArgs,
    Documents,
    DocumentsArgs,
    Known,
    KnownBody,
    NothingAsked,
    SearchArgs,
    SearchPage,
    Suggestions,
    ViewChange,
    ViewRefused,
    Views,
)
from mailmind.kernel.errors import Problem
from mailmind.platform.logging import get_logger

log = get_logger("contacts")


def router(discovery):
    r = route.router()

    @r.get("/api/search", responses=answers(SearchPage | NothingAsked | BadQuery))
    def search(args: Annotated[SearchArgs, Query()]):
        return discovery.search.answer(args.as_query())

    @r.get("/api/views", responses=answers(Views))
    def views():
        return {"ok": True, "views": discovery.views.merged()}

    @r.post("/api/views", responses=answers(Views | ViewRefused))
    def views_post(body: ViewChange):
        return discovery.views.post(body.model_dump(exclude_unset=True))

    @r.get("/api/documents", responses=answers(Documents))
    def documents(args: Annotated[DocumentsArgs, Query()]):
        return discovery.documents.page(args.as_query())

    @r.get("/api/contacts", responses=answers(Suggestions))
    def contacts(args: Annotated[ContactsArgs, Query()]):
        return discovery.contacts.autocomplete(args.q)

    @r.get("/api/contact", responses=answers(ContactCard))
    def contact(args: Annotated[ContactArgs, Query()]):
        return discovery.contacts.card(args.addr)

    @r.post("/api/contacts/known", responses=answers(Known))   # read-only: which recipients you've exchanged mail with
    def known(body: KnownBody):
        try:
            return discovery.known.ask(body.addrs)
        except Problem:
            raise
        except Exception as ex:             # class only: the payload is a list of addresses
            log.error("known error", exc_info=ex)
            return {"ok": False, "error": "lookup failed"}, 500

    return r
