# HodHod in MailMind on macOS

In **Settings → Accounts → Add an account**, choose **Import HodHod profile…**. MailMind checks the profile with the hosted Worker and fills in the account address. Choose **Add Account** to save it in MailMind's account list. The account uses the standard inbox, folders, reader, search, and composer. A separate mailbox window is unnecessary.

## Profile

Use a private JSON file from the administrator of the mailbox.

- `origin` is the Worker's HTTPS origin. A loopback HTTP origin can be used for a fixture.
- `mailbox` is the server's mailbox ID.
- `token` is a lowercase hexadecimal device-session token.
- `privateKey` is the base64-encoded PKCS #1 DER RSA private key for that mailbox.

The existing MailMind account credential path stores the profile in the macOS login Keychain. The account configuration holds the credential reference. MailMind decrypts the message locally and uses its existing encrypted local store. While a HodHod account is configured, MailMind pauses remote AI calls across the workspace. Its local index and local AI remain available.

## Server and verification

The Worker must expose the v2 mailbox API with a stable numeric `uid` for each message's current folder membership. Its outbox creates the encrypted Sent copy upon delivery acceptance. The client builds a repeatable idempotency key for each queued send and uses the standard MailMind outbox to report errors.

The hosted transport supports reading, read-state changes, moving between available folders, and submitting mail. Its draft stays in MailMind's local draft store. Folder administration, starring, and permanent deletion require more hosted mailbox API support. Incoming refresh uses MailMind's periodic sync and manual refresh.

Use the standard [Apple build instructions](../shells/apple/justfile) and a synthetic profile for verification. Keep real profiles and credentials outside the repository.
