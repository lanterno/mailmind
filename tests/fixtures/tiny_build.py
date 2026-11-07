"""A build of the app small enough to write for a test: what `just build-web` leaves in mailmind/http/dist, holding only what a
test of the server reads, so the suite needs no node. conftest makes every test's `webroot.DIST` this, and a child process
sets it itself. The browser tests serve the real build (`serve_the_build`)."""
import os

PAGE = ('<!doctype html><html lang="en"><head><meta name="csrf" content="__CSRF_TOKEN__">'
        '<meta name="mailmind-build" content="__BUILD__"></head><body><div id="root"></div></body></html>')
SCRIPT = "assets/app-abc123.js"
FONT = "assets/face-abc123.woff2"
STYLES = "assets/styles/fonts.css"          # the path the walks over every route ask for
FILES = {"index.html": PAGE, SCRIPT: "// the app\n", FONT: "not really a font", STYLES: "body { color: inherit }\n"}


def write(dist):
    """The build, in the folder `dist`, made if it has to be. Returns the folder's path."""
    for name, text in FILES.items():
        path = os.path.join(dist, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return str(dist)
