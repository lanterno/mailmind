# Private mailbox on macOS

Choose **Private mailbox…** in MailMind Settings under Accounts. It opens a dedicated Meadow window for a mailbox hosted by a compatible HodHod v2 server. The window reuses MailMind's type, colors, background, icons, and buttons. Its account data stays within the private-mailbox feature. Existing MailMind accounts and their shared Inbox, Search, and Assistant work through their usual connections.

## Connect a profile

Choose **Import mailbox profile…**. Ask the mailbox administrator for a protected JSON file with these fields.

- `origin` is the server's HTTPS origin without a path. HTTP with an explicit port at `127.0.0.1` is supported for local fixture testing.
- `mailbox` is the mailbox ID.
- `token` is a 64-character lowercase hexadecimal device-session token.
- `privateKey` is the base64-encoded PKCS #1 DER RSA private key for the mailbox.

Import a profile only when you trust its source and recognize the server that will receive the token. The private key stays on the Mac. Hosted profiles are saved in the login Keychain. Keep profile files private and outside Git. For a synthetic fixture, `HODHOD_ENCRYPTED_PROFILE` can point to a local profile when starting the app.

## Read and send

The feature unlocks cached message content on the Mac. It can sync read state, move messages between Inbox and Archive, and export attachments. Offline reading works after a successful sync. The reader displays plain text and does not load remote images.

The composer keeps one encrypted draft locally, supports one recipient, and submits replies and attachments through the mailbox's outbox. Queueing, relay acceptance, and rejection are reported separately. The server creates the encrypted Sent copy on acceptance. Ordinary inbound SMTP content is visible to the server during receipt and then encrypted for storage.

The Mac-specific feature is under `shells/apple/Sources/Mac/HostedMailbox`. The three host-only UniFFI functions delegate MIME parsing to MailMind's existing Rust parser. Use the standard [Apple build commands](../shells/apple/justfile) to regenerate the framework and build the app.
