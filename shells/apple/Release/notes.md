## What's new in MailMind {{version}}

- Write what changed here, a sentence each, before you publish.

## Install

1. Download `MailMind-{{version}}.dmg` below and open it.
2. Drag **MailMind** onto **Applications**.
3. Open MailMind from Applications. The first time, macOS stops it: see below.

MailMind needs macOS 26 or later, on an Apple silicon or Intel Mac. Already have it? It updates itself: **MailMind ›
Check for Updates…**.

## The first time you open it

MailMind isn't signed with an Apple Developer ID yet, so macOS can't check who made it and won't open it until you say
so. You do this once. On macOS 15 and later:

1. Open MailMind. macOS says *"MailMind" Not Opened* (Apple could not verify it is free of malware). Click **Done**.
2. Open **System Settings › Privacy & Security**, scroll down to **Security**, and click **Open Anyway** beside
   *"MailMind" was blocked to protect your Mac*.
3. Click **Open Anyway** in the dialog that follows, and enter your login password or use Touch ID.

MailMind opens, and from then on it opens like any other app. If you prefer Terminal, this does the same:
`xattr -dr com.apple.quarantine /Applications/MailMind.app`
