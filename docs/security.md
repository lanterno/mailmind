# Security model

Every request passes `mailmind.platform.perimeter` before any route: the `Perimeter` middleware
calls it, once. What runs next goes by the path it judged, as sent, and not as the
ASGI server decoded it.
It is standard library only. Callers ask the package by these names (`perimeter.refuse_host`); inside it
`host`, `origin`, `fetch_metadata`, `csrf` and `limits` hold the checks, `headers` and `csp` what an answer
carries, `errors` the `Refusal`, and `tokens` a confirm token, which a use case imports for itself.

- `refuse_host` — the Host must be this server's (127.0.0.1 or localhost on its port), and an
  Origin, when sent, its own. Blocks DNS rebinding. Every request, `/` included.
- `refuse_cross_site` — under `/api/` only `Sec-Fetch-Site: same-origin` passes, so another
  site's no-cors `<img>`, a typed URL and a client that says nothing are all refused. `/` also
  opens from a typed URL or another site's top-level link.
- `refuse_body` — a Content-Length over the route's cap (`shared.post_body_cap`:
  `MAX_SEND_POST` for send and draft, `MAX_POST` otherwise), judged before the body is read.
- `refuse_mutation` — every POST: the two checks above, then the `X-CSRF-Token` header
  compared with `secrets.compare_digest`, then an Origin, which must be this server's.

Every method but GET and HEAD goes through the POST checks, and a refusal comes before a body is read.

Every answer, a refusal included, carries `perimeter.answer_headers`: nosniff,
`Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, COOP and CORP `same-origin`, a
Permissions-Policy, and under `/api/` `Cache-Control: no-store`. The middleware adds each one
an answer didn't set itself. The page also carries
`Content-Security-Policy` from `perimeter.page_policy`: script files from this server and nothing else, connections to this server
only, framed by no one, and nothing in it for mail: no `https:` images and no `data:` fonts. The browser tests fail on any violation.

A failed GET or POST answers a fixed message and logs its path and the error's class. Every TLS connection, IMAP
and SMTP alike, STARTTLS or implicit, takes `accounts.tls_context(host)`: verified everywhere but
loopback, where Proton Bridge's certificate is self-signed. There `accounts.check_pin` stands in,
after the handshake and before LOGIN or AUTH: the first certificate an account meets at a loopback
`host:port` is pinned (`config.tls_pin`, the account's `tls_pins`, `sha256:` of its DER bytes), and
any other after it raises `TLSPinMismatch` (a `ConnectionError`, so a move queues) with nothing
sent. Health calls it `TLS_PIN`; `/api/accounts/trust-cert` forgets the account's pins and syncs it.
An unsaved draft pins nothing. A new TLS connection calls `check_pin` too.

A credential goes only to its own account's server. `/api/accounts/test` logs a saved account in
at its saved endpoint, and tries a draft (an account not yet saved) only with a password typed
into it, at the host typed with it (`domain.draft.throwaway_account`). A draft naming a stored
secret (`op`, `env`, the Bridge password) is refused, since the request names the host.

Folder names reach IMAP only through `accounts.mailbox_arg`. It wraps a name in the form LIST
gave it and refuses one that isn't a valid quoted-string body (CR, LF, NUL, a bare quote, a
trailing backslash) with `accounts.BadMailbox`, an `IMAP4.error`. Never build `f'"{folder}"'`.
Any other quoted value (a Message-ID, an expiry token) goes through `accounts.imap_quote`, which
refuses CR, LF and NUL with `BadValue`, an `IMAP4.error` too: a sender can fold the Message-ID
header so the ID stored holds a line break.

`perimeter.CSRF` is a per-process token. `web/index.html` ships a `__CSRF_TOKEN__` placeholder in a
`<meta>` tag that `webroot.page` substitutes at serve time; `request` in `lib/api/client.ts`
attaches it to every request, GETs included.

Mail bodies render in a sandboxed iframe with a deny-all CSP. `mailmind.content.sanitize` is
defense in depth — **the iframe sandbox is the actual boundary**, so a sanitizer miss
alone cannot execute script. Preserve that ordering when touching either.

The reader's frame loads `GET /api/message/body.html?key=<key>&folder=<folder>&uid=<uid>&images=0|1`
(`Bodies.copy_document`), the same sanitized HTML as `/api/message/body` or the plain text escaped, under
`perimeter.mail_frame_headers`: a policy of its own, with a `sandbox` that holds even if the frame's attribute goes (a `<meta>` can't
do that), and with an attachment's preview (`perimeter.attachment_preview_headers`, an image typed from its bytes, in a sandbox that allows nothing) the only answers this origin may frame. The folder and the uid name the copy, since one key spans a message's copies and
a sender who reuses a Message-ID puts two beside each other. The frame finds the copy as the reader's body route does
(`store.copy_pk`): no uid names the only copy there, and a uid that won't read as a number names none. The row found must still
hold that key and uid as its body is read (`Bodies.as_document`), so a renumber never shows another message, or another copy, under
the bar the page drew. A failure answers as a
document too, or the frame would show a browser's error page. The document holds no style: `MailFrame` gives it its look
(`mailDoc.lookOf`) as soon as it is parsed, folds a plain mail's quote and takes its link clicks through `contentDocument`, so a
reading setting fetches nothing. Load images is a new frame on `images=1`, since moving a loaded frame would add a history entry.
The composer's quote (`QuoteFrame`) is a srcdoc with an empty `sandbox`, which inherits the page's policy.

## At rest

The database is encrypted on disk. Its driver is SQLCipher's build of SQLite (the `sqlcipher3` package, which only
`mailmind.platform.db.driver` imports, as import-linter holds), and it encrypts every page of `mailmind.db` and of its
write-ahead log with AES-256, each page with an HMAC, so a page changed on disk fails to read rather than reading
wrong. Backups, the upgrade check's copy and a database export are encrypted with the same key. Nothing in the package
takes the standard library's `sqlite3` (`tests/unit/test_architecture.py`), which reads no encrypted file.

The key (`platform.db.key`) is 32 random bytes, one for the computer, kept in the login Keychain as a generic password
(service `mailmind`, account `database-key`). The first start that makes a database makes it, and so does `db encrypt`
for a database from before. A connection takes it raw (`PRAGMA key = "x'…'"`), so no passphrase is stretched: the key
is random, and opening one costs nothing. MailMind reaches the Keychain only through `/usr/bin/security`, hands it a new
key on standard input, never on a command line another program can read, and never replaces one the Keychain holds,
since a database made with that one could no longer be read. MailMind writes the key to no file, log or terminal.
`MAILMIND_DB_KEY` (64 hex digits) stands in for the Keychain: for the tests, the demo, which makes a key for each run,
and a computer with no Keychain.

A database still plaintext is refused (`database.NotEncrypted`), never opened as it is, with what to run: `db encrypt
--dry`, which tries it on a copy and changes nothing, then `db encrypt` with MailMind stopped, which checks each
encrypted copy (rows per table, integrity, foreign keys, the search index, the schema version) before it puts it in
place of the database or a backup. A key that doesn't fit opens nothing (`database.KeyDoesNotFit`), and a Keychain that
won't answer stops the start (`key.KeyProblem`), each with a line that says so and no traceback.

What it protects: the mail wherever the file goes without the Keychain, in a copy of the disk or the data directory, a
sync folder, a support bundle or a backup, from another user of the computer and from a disk taken out of it. What it
doesn't: code running as you, which can ask the Keychain as MailMind does, or a login keychain whose password is
known. The free blocks a plaintext database left on disk before `db encrypt` are out of its reach; FileVault covers
those, and is worth turning on. The other files in the data directory are still plaintext and readable by you only:
`mailmind.config.json`, whose passwords can live in 1Password or the environment instead, the rules, views and
snippets files, the audit trails, and the log, which holds no address or subject.
