"""The MailSession port over IMAP: imaplib_session, what every account runs on unless it asks for another, and
imapclient_session, the only module that imports IMAPClient. So this file imports neither: a process not offering it never
loads it."""
