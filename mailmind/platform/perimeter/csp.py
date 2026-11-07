"""The content security policies: the page's, the React app's, and a mail body's own."""


def next_page_policy():
    """The React app's policy. Only script files from this server may run, it may reach only this server, and no one may
    frame it. A mail body loads into a frame from a URL under a policy of its own, so nothing here has to allow mail."""
    return "; ".join([
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "frame-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "object-src 'none'",
    ])


def page_policy():
    """The content security policy for the page. Only script files from this server may run, so a
    script or event handler that gets into the page some other way can't. It may reach only this
    server and be framed by no one. Images may come over https: a mail body's frame, a srcdoc
    document, inherits this policy on top of its own, and shows remote images once asked to."""
    return "; ".join([
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: https:",
        "font-src 'self' data:",
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
