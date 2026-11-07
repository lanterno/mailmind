"""The content security policies: the page's, and a mail body's own."""


def page_policy():
    """The page's policy. Only script files from this server may run, it may reach only this server, and no one may frame it.
    A mail body loads into a frame from a URL under a policy of its own, so nothing here allows a remote image. The composer's
    quote is a srcdoc, which inherits this: that, and the page's two style attributes, are what inline styles are for, and
    the quote's own images, the icon and the stylesheets' are all that `data:` lets in. No image comes from this server."""
    return "; ".join([
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src data:",
        "font-src 'self'",
        "connect-src 'self'",
        "frame-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "object-src 'none'",
    ])


def mail_frame_policy(load_images):
    """The policy on a mail body's own document. A sandbox set here holds even if the frame's attribute goes, which a
    <meta> can't do. Allowing the same origin is what lets the parent size the frame and take its link clicks, and it is
    safe only because nothing here lets a script run. Remote images come only when asked for."""
    return "; ".join([
        "sandbox allow-same-origin",
        "default-src 'none'",
        "img-src data: https:" if load_images else "img-src data:",
        "style-src 'unsafe-inline'",
        "font-src data:",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'self'",
    ])
