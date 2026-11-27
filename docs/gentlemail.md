# GentleMail in MailMind for Mac

Open Settings → Accounts, add an account, and choose **GentleMail** in the Type row. Enter your mailbox address, your GentleMail HTTPS hostname, and an API key scoped to that mailbox. Give the key permission to read messages and attachments and to send mail. MailMind keeps the credential in the macOS Keychain.

Use the ordinary inbox, message reader, and composer. MailMind downloads mail from the existing GentleMail deployment and sends replies through its API. Connecting the account does not require DNS changes or a mailbox migration.

This integration supports personal mailboxes with fewer than 100 messages in each synced folder. The API used here returns at most 100 per folder without a continuation token. At the limit, MailMind reports a sync error to avoid treating a partial result as the whole mailbox. Server-side filing and deletion are outside this integration.
