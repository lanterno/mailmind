<!-- 0.3.2's news. At the release after 0.3.2, replace this heading and the news under it with that release's. -->
## What's new in MailMind 0.3.2

- Going to the **Screener** or the **Paper Trail** with a message open now shows the page across the window, as it
  should, rather than squeezed into the list's column beside a message from somewhere else.

Everything 0.3.1 brought is in its [release notes](https://github.com/lanterno/mailmind/releases/tag/v0.3.1).

## Good to know if you are testing

- **The first time you open it**, macOS stops it: follow *The first time you open it* below, once.
- **Gmail** needs an App Password, not your Google password: Settings shows the steps and links the page that makes
  one. iCloud, Yahoo, AOL and Fastmail each need an app password of their own too.
- **Proton** needs Proton Mail Bridge installed and running on this Mac, which takes a paid Proton plan.
- **Outlook, Hotmail and Microsoft 365** aren't supported yet.
- **AI is off** until you turn it on in Settings › AI. It needs a key from the provider you choose, billed by them per
  use and separately from any ChatGPT or Claude subscription, or a model on this Mac.
- MailMind sends and checks mail only while it is open.

## Install

1. Download `MailMind-{{version}}.dmg` below and open it.
2. Drag **MailMind** onto **Applications**.
3. Open MailMind from Applications. The first time, macOS stops it: see below.

MailMind needs macOS 15 or later, on an Apple silicon or Intel Mac. Already have it? It updates itself: **MailMind ›
Check for Updates…**.

## The first time you open it

MailMind isn't signed with an Apple Developer ID yet, so macOS can't check who made it and won't open it until you say
so. You do this once:

1. Open MailMind. macOS says *"MailMind" Not Opened* (Apple could not verify it is free of malware). Click **Done**.
2. Open **System Settings › Privacy & Security**, scroll down to **Security**, and click **Open Anyway** beside
   *"MailMind" was blocked to protect your Mac*.
3. Click **Open Anyway** in the dialog that follows, and enter your login password or use Touch ID.

MailMind opens, and from then on it opens like any other app. If you prefer Terminal, this does the same:
`xattr -dr com.apple.quarantine /Applications/MailMind.app`

## If something goes wrong

Choose **Help › Copy Diagnostics** and paste what it copies into a message to whoever gave you MailMind, or use
**Help › Report a Problem** to open an issue on GitHub. MailMind's log is `mailmind.log` in
`~/Library/Application Support/io.github.lanterno.mailmind` (in Finder, **Go › Go to Folder…**), and it holds no
address, subject or password. If MailMind quits unexpectedly, macOS keeps a report in `~/Library/Logs/DiagnosticReports`
named MailMind and the date: send that too.

## Uninstall

Turn off **Open at login** in Settings › General if it is on, quit MailMind, and move it to the Trash. Then move
`~/Library/Application Support/io.github.lanterno.mailmind` to the Trash (your copy of the mail, its backups, settings
and log), and in **Keychain Access** delete each item named `mailmind` (your accounts' passwords, AI keys and the key
your mail was kept encrypted with). Your mail on its servers stays as it is. The
[README](https://github.com/lanterno/mailmind#uninstall-the-mac-app) has the steps in full.
