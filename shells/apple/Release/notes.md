<!-- 0.3.0's news. At the release after 0.3.0, replace this heading and the news under it with that release's. -->
## What's new in MailMind 0.3.0

MailMind now runs on **macOS 15 or later**, on Apple silicon and Intel Macs.

**Adding your accounts**

- Type your address and MailMind knows Gmail, iCloud, Yahoo, AOL, Fastmail and Proton: it fills in their servers and
  links the page where you make the app password each one needs. Any other address takes its incoming and outgoing
  servers, so you can send from every account, not only Gmail.
- **Test** checks sending as well as reading, and your first account is tested before it is saved. When a login fails,
  MailMind says what went wrong and what to do next, in plain words.
- Delete works on iCloud, and on Gmail in any language: MailMind finds your Trash rather than guessing its name.
- Removing an account can also delete its mail from this Mac. Your mail on the server stays as it is.

**Your first sync**

- A big Inbox shows its newest mail first, then reads the rest in the background, newest first, and says how far it
  has got ("Reading your inbox: 2,300 of 41,000"). Quit meanwhile and it carries on where it stopped. It reads
  only the start of each message, so a large attachment waits until you open it.
- Mail that was already there doesn't count as new: no notifications, no badge.
- One account's slow server no longer holds up the others, and you can open a message while the first sync runs.
- Each account keeps its connection to its server open, so opening messages and folders is quicker.
- After your Mac wakes, or your Wi-Fi changes, mail syncs at once, and an account shows as unable to connect only if
  the trouble lasts.

**AI, on your terms**

- AI is off until you turn it on in **Settings › AI**, which tells you first exactly what it would send, and to whom.
- Choose who runs it: Claude, OpenAI, Google Gemini, another compatible service, or a model on your own Mac, with steps
  to get Ollama and a model to start with.
- On its own, it summarizes only your Inbox's last 30 days, with the provider's smaller, cheaper model. Older mail is
  summarized when you ask.
- Drafting replies ahead of time is a switch, off until you turn it on.
- Settings shows what AI cost over the last 7 days and this month, and how many messages are waiting for a summary.
  Each provider's key says how it is billed, and an account with no credit left says so.

**Cleaning up**

- **Sweep** shows what would go, group by group, before you confirm. It never moves starred or unread mail, and one
  Undo puts it all back.
- A reply from a person in a mailing-list conversation is no longer sorted as marketing.

**Like a Mac mail app**

- Print a message (⌘P), search with ⌘F, find a word in a message (⌥⌘F), double-click to open a message in its own
  window, and drag messages onto a folder.
- Make a folder from **Move To**, and rename or delete your own folders from the sidebar.
- A notification names the sender and the subject, and a click opens the message.
- The Automatic light: Night when your Mac is in Dark Mode, otherwise by the time of day. It is the new default.
- **Open at login**, in Settings › General, and Settings also shows how much room your mail takes on this Mac.
- Quitting sends a message still in its Undo time first, and asks before leaving one scheduled for later.
- Closing the window and clicking MailMind in the Dock brings your mail back as it was.

**Safety, and when something goes wrong**

- **Read only**, in Settings › General: MailMind changes nothing on your mail servers. Anything that would is refused,
  and says so, while reading, rules, AI and saving drafts on this Mac still work.
- **Help › Copy Diagnostics** and **Help › Report a Problem** copy what the developer needs to see what went wrong,
  with no addresses, subjects or passwords in it.
- A sync that fails says why in Settings › Accounts, and what to do about it.
- If MailMind can't open your mail, its window says why, and offers to start fresh where that helps.

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
