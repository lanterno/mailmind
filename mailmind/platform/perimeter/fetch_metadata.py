"""The cross-site check, on what the browser says of a request in Sec-Fetch-Site."""
from mailmind.platform.perimeter.errors import Refusal


def is_api(path):
    return path == "/api" or path.startswith("/api/")


def refuse_cross_site(headers, path):
    """A request the page didn't make. Another site's no-cors GET, such as an <img>, carries this
    server's Host and no Origin, so the host check passes it, yet it could still make the server
    sync a folder, fetch bodies or run an AI briefing. Browsers mark it Sec-Fetch-Site cross-site,
    or same-site when it comes from another port on this machine.

    Under /api/ only same-origin passes: the page's own fetches, its event stream and its
    downloads. A request that says nothing (a browser too old to send the header, or not a
    browser) or comes from no page (a typed URL) is refused as well. The app itself, `/`, also
    opens from a typed or bookmarked URL, or from another site's link: a top-level visit does
    nothing, and its contents can't be read across origins."""
    site = (headers.get("Sec-Fetch-Site") or "").lower()
    if is_api(path):
        if site == "same-origin":
            return None
        return Refusal(403, "cross-site request" if site in ("cross-site", "same-site")
                       else "same-origin request required")
    if site in ("", "same-origin", "none"):
        return None
    if path == "/" and headers.get("Sec-Fetch-Mode") == "navigate" and headers.get("Sec-Fetch-Dest") == "document":
        return None
    return Refusal(403, "cross-site request")
