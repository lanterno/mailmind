"""What the page asks of Discovery: search, saved views, documents and who you know."""
from mailmind.http.routing import Routes


def routes(discovery):
    r = Routes()

    @r.get("/api/search")
    def search(req):
        return discovery.search.answer(req.query)

    @r.get("/api/views")
    def views(req):
        return {"ok": True, "views": discovery.views.merged()}

    @r.post("/api/views")
    def views_post(req):
        return discovery.views.post(req.body)

    @r.get("/api/documents")
    def documents(req):
        return discovery.documents.page(req.query)

    @r.get("/api/contacts")
    def contacts(req):
        return discovery.contacts.autocomplete(req.arg("q", ""))

    @r.get("/api/contact")
    def contact(req):
        return discovery.contacts.card(req.arg("addr", ""))

    @r.post("/api/contacts/known")   # read-only: which recipients you've exchanged mail with
    def known(req):
        try:
            return discovery.known.ask(req.body.get("addrs"))
        except Exception as ex:             # class only: the payload is a list of addresses
            print(f"[contacts] known error: {type(ex).__name__}", flush=True)
            return {"ok": False, "error": "lookup failed"}, 500

    return r
