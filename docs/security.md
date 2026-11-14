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
secret (`op`, `env`, the Bridge password) is refused, since the request names the host. A draft's typed password is
`Typed`: held in memory for that one try, and kept nowhere.

A secret MailMind is given, a password typed into Settings, `accounts password`, `ai key` or what `accounts materialize`
moves, goes to the login Keychain (`platform.keychain`) before the file is written, and `mailmind.config.json` names the
item and a version, never the secret: `{"keychain": "account:<address>", "version": n}`, `anthropic-api-key` for the
Claude key. A new version tells IDLE to log in afresh and another process to read the item again. Removing an account,
or moving it to another style, removes its item. A config that holds a secret itself, `{"plain": …}` or a bare string,
is refused (`PlaintextSecret`): the app won't start, and `check` says which entry, never the secret. No credential may
name the database's key.

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
alone cannot execute script. Preserve that ordering when touching either. The sanitizer runs in the Rust core
(`mm_content::sanitize`): it reads a body with html5ever's tokenizer, as a browser with scripting off reads it, and
writes everything itself, every tag from the allowlist and every value escaped, so what it returns is safe however the
input was written. The CSS it writes reads in a browser as it was checked: no comment mark, line break or backslash,
and every string and bracket closed by its own match. One check over all it writes holds that (`written`, in
`sanitize/css.rs`), and writes nothing where it fails. The browser tests take the sanitizer away (`unsanitized on`, on
either server) to show the sandbox and the frame's own policy hold alone. On the Rust server that makes it a seam
(`Seams.sanitizer`), which only the test launcher fills with anything else: `mailmind-server`'s seams (`real::seams`)
have the sanitizer itself, and nothing in it reads a setting that could turn it off.

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
(service `mailmind`, account `database-key`). The first start that makes a database makes it. A connection takes it raw
(`PRAGMA key = "x'…'"`), so no passphrase is stretched: the key is random, and opening one costs nothing. Every
Keychain access goes through `platform.keychain` and `/usr/bin/security`: a secret goes in on standard input, in hex,
never on a command line another program can read, and each write is read back, since `security -i` doesn't say when one
failed. `add`, which the database's key takes, never replaces an item, since a database made with the one there could no
longer be read; `put`, for passwords and the API key, does. MailMind writes no secret to a file, log or terminal.
`MAILMIND_DB_KEY` (64 hex digits) stands in for the Keychain: for the tests, the demo, which makes a key for each run,
and a computer with no Keychain.

A plaintext database, one from before MailMind encrypted its data, is refused (`database.NotEncrypted`) and never opened
as it is. A key that doesn't fit opens nothing (`database.KeyDoesNotFit`), and a Keychain that won't answer stops the
start (`key.KeyProblem`), each with a line that says so and no traceback.

What was once in plaintext files beside the database is in it: the rule book, the saved views and the snippets as
`documents`, and the audit trails as `audit_events` (migration 48). A person's data goes in the database, never in a
new file beside it.

The browser keeps nothing of a person's either. It writes every URL a tab goes to into its history, in plaintext, and may
sync that to the account it is signed in to, so the page's URLs name no address, folder, search, question, topic or opened
message. A route that holds any of those is a place, `/p/<id>`: its id is random and made in the page (`app/route.ts`), and
the route it stands for is kept in `places` (migration 49, `platform.db.places`), sent once it is about to be used, gone to
or a link to it pointed at, never for a link only drawn (`POST /api/places`, which takes a batch of ids of the page's
making and routes of at most 4 KB, and keeps the 10,000 used last), and put back into the
page opened at `/p/<id>`, escaped, in its `mailmind-place` meta. An id that names none opens at home. Only `/`, `/all`, the
views over every account, `/documents` and the brief's own two lists are written as themselves, nothing goes into
`history.state`, which the browser's session file keeps too, and the readable forms still open when typed. The page's
storage holds a width, the rail's state, an Inbox tab, a tidy run's day and the last preferences it was given; the new-mail
pill keeps when each Inbox was looked at by the account's place in the list (`mm_looked`), and a browser's older copy kept
by address goes as it is first read. What a browser wrote into its history before is out of reach: clearing its history for
the app's address takes that away.

What it protects: the mail wherever the file goes without the Keychain, in a copy of the disk or the data directory, a
sync folder, a support bundle or a backup, from another user of the computer and from a disk taken out of it. What it
doesn't: code running as you, which can ask the Keychain as MailMind does, or a login keychain whose password is
known. The free blocks a plaintext file left on disk before it was encrypted or removed are out of its reach; FileVault
covers those, and is worth turning on. A copy of the data directory holds no password and no API key. What stays in
plaintext, readable by you only: `mailmind.config.json` (accounts, preferences, and where each secret is), the log,
which holds no address or subject, and the Bridge password file `~/.config/mailmind/proton_bridge_pass`, which Proton
support, kept as it is for the desktop app, still reads.
