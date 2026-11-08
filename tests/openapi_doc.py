"""`just gen-api`'s first step: the OpenAPI document of the app the routers make, which openapi-typescript turns into the page's types.

It lives in tests/, beside what holds the routes to their schemas, and not in the package: the served app has no OpenAPI page, and
the command line (`python -m mailmind`) must never load FastAPI. It builds the app as `front.build` does, over `web.ROUTERS`, but
picks no SDK and starts nothing. Nothing it loads reads the data directory, and to be sure of that it points the settings at an
empty one of its own. It sends nothing: an attempt to connect anywhere or run a program stops it.

    python tests/openapi_doc.py <out.json>
"""
import json
import os
import sys
import tempfile


def _refuse(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.exec", "os.posix_spawn", "os.system"):
        raise RuntimeError(f"writing the OpenAPI document tried {event}")


def document():
    """The document, as a dict. Request and answer models share their names, with no `-Input` and `-Output` pairs."""
    from fastapi.openapi.utils import get_openapi

    from mailmind.bootstrap import web
    from mailmind.http.app import create_app
    app = create_app(routers=web.ROUTERS, token=lambda: "", body_cap=lambda method, path: 0)
    return _problems(get_openapi(title="MailMind", version="1", routes=app.routes, separate_input_output_schemas=False))


def _problems(doc):
    """A failure answers a problem document, application/problem+json. FastAPI writes every answer a route declares in the route's
    own media type, so here each 4xx and 5xx says what it is."""
    from mailmind.http.shared import PROBLEM_JSON
    for operations in doc["paths"].values():
        for operation in operations.values():
            for status, answer in operation["responses"].items():
                content = answer.get("content", {})
                if status[:1] in ("4", "5") and "application/json" in content:
                    content[PROBLEM_JSON] = content.pop("application/json")
    return doc


def main(out):
    for name in ("MAILMIND_HOME", "ANTHROPIC_API_KEY"):
        os.environ.pop(name, None)
    sys.addaudithook(_refuse)
    with tempfile.TemporaryDirectory() as home:
        from mailmind.platform import settings
        settings.configure(settings.Settings(home=home))
        doc = document()
        if os.listdir(home):
            sys.exit(f"writing the OpenAPI document left {os.listdir(home)} in the data directory")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))     # the repo root, where the package is
    main(sys.argv[1])
