//! The page's content security policy.

/// The page's policy. Only script files from this server may run, it may reach only this server, and no one may
/// frame it. A mail body loads into a frame from a URL under a policy of its own, so nothing here allows a remote
/// image. The composer's quote is a srcdoc, which inherits this: that, and the page's two style attributes, are what
/// inline styles are for. The icon and the stylesheets' images are what `data:` is for, and no image comes from this
/// server.
pub fn page_policy() -> &'static str {
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src data:; font-src 'self'; \
     connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; \
     object-src 'none'"
}
