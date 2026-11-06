"""The page's content security policy."""


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
