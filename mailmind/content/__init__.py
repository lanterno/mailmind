"""What a message says: its MIME parts, headers and text, the quote in a reply, and what's safe to show.

Pure, on the standard library and the Rust core's leaves (MIME reading and the sanitizer run in `mm-content`, through
`mailmind_leaves`), so any context's domain may read mail through it. It knows nothing about mailboxes or accounts.
"""
