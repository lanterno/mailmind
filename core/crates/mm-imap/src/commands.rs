//! The Mailbox's `MailServer` over a logged-in `Session` (`mailmind.mailbox.adapters.imap.imaplib_session`): the
//! commands a sync, a filing and an undo send, each as the Python app sends it, and their answers read by imap-proto.
//!
//! A folder name goes only through `mailbox_arg`, and any other value through `imap_quote`; one that can't be sent is
//! refused before it reaches the wire, as `BadMailbox` or `BadValue`. A server's NO or BAD is a refusal of the class
//! `error`, as `imaplib.IMAP4.error` is. An expunge names its UIDs, never the whole folder.

use std::collections::BTreeMap;

use mm_kernel::when::civil;
use mm_mailbox::session::{HEADER_BATCH, Headers, MailServer, Search, Selected, ServerError};

use crate::codec::{Code, Said};
use crate::list::mailbox_arg;
use crate::names::{gmail_labels, imap_quote};
use crate::session::{Answer, Error, Session};

/// What a server's NO or BAD to a command is, by its class: `imaplib.IMAP4.error`'s name.
const REFUSED: &str = "error";

fn uid_set(uids: &[u32]) -> String {
    uids.iter().map(u32::to_string).collect::<Vec<_>>().join(",")
}

fn folder(name: &str) -> Result<String, ServerError> {
    mailbox_arg(name).ok_or(ServerError::Refused("BadMailbox"))
}

fn quoted(value: &str) -> Result<String, ServerError> {
    imap_quote(value).ok_or(ServerError::Refused("BadValue"))
}

/// A flag (`\Seen`) or a header field's name (`MESSAGE-ID`): an atom, which nothing quotes, so nothing but an atom's
/// characters may be in one.
fn atom(value: &str) -> Result<&str, ServerError> {
    let fits = |c: char| c.is_ascii_graphic() && !matches!(c, '(' | ')' | '{' | '"' | '%' | '*' | ']');
    if value.is_empty() || !value.chars().all(fits) {
        return Err(ServerError::Refused("BadValue"));
    }
    Ok(value)
}

fn flag_list(flags: &[&str]) -> Result<String, ServerError> {
    let flags: Result<Vec<&str>, ServerError> = flags.iter().map(|f| atom(f)).collect();
    Ok(format!("({})", flags?.join(" ")))
}

/// `SINCE`'s day, as `strftime("%d-%b-%Y")` writes it.
fn imap_day(days: i64) -> String {
    const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    let (year, month, day) = civil(days);
    format!("{day:02}-{}-{year:04}", MONTHS[month as usize - 1])
}

/// A message as APPEND sends it: each line ending a CRLF, as imaplib makes it.
fn crlf(raw: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(raw.len() + raw.len() / 32);
    let mut i = 0;
    while i < raw.len() {
        match raw[i] {
            b'\r' => {
                out.extend_from_slice(b"\r\n");
                if raw.get(i + 1) == Some(&b'\n') {
                    i += 1;
                }
            }
            b'\n' => out.extend_from_slice(b"\r\n"),
            b => out.push(b),
        }
        i += 1;
    }
    out
}

impl Session {
    fn send(&mut self, command: &str) -> Result<Answer, ServerError> {
        Ok(self.connection.command(command.as_bytes())?.done(REFUSED)?)
    }

    fn fetched(&mut self, command: &str) -> Result<Vec<crate::codec::Fetched>, ServerError> {
        let answer = self.send(command)?;
        Ok(answer
            .said()
            .into_iter()
            .filter_map(|said| match said {
                Said::Fetch(fetched) => Some(fetched),
                _ => None,
            })
            .collect())
    }
}

impl MailServer for Session {
    fn capabilities(&self) -> Vec<String> {
        self.connection.capabilities.clone()
    }

    fn select(&mut self, name: &str, readonly: bool) -> Result<Selected, ServerError> {
        let verb = if readonly { "EXAMINE" } else { "SELECT" };
        let answer = self.send(&format!("{verb} {}", folder(name)?))?;
        let mut selected = Selected::default();
        for said in answer.said() {
            match said {
                Said::Exists(n) => selected.exists = n,
                Said::Code(Code::UidValidity(n)) => selected.uidvalidity = Some(n),
                Said::Code(Code::UidNext(n)) => selected.uidnext = Some(n),
                Said::Code(Code::HighestModSeq(n)) => selected.highest_modseq = Some(n),
                _ => {}
            }
        }
        Ok(selected)
    }

    fn search(&mut self, criteria: Search<'_>) -> Result<Vec<u32>, ServerError> {
        let criteria = match criteria {
            Search::All => "ALL".to_owned(),
            Search::Above(uid) => format!("UID {}:*", u64::from(uid) + 1),
            Search::Uid(uid) => format!("UID {uid}"),
            Search::Since(days) => format!("SINCE {}", imap_day(days)),
            Search::MessageId(id) => format!("HEADER Message-ID {}", quoted(id)?),
            Search::UndeletedMessageId(id) => format!("UNDELETED HEADER Message-ID {}", quoted(id)?),
            Search::ChangedSince(modseq) => format!("MODSEQ {}", modseq.saturating_add(1)),
        };
        let answer = self.send(&format!("UID SEARCH {criteria}"))?;
        let mut found: Vec<u32> = answer
            .said()
            .into_iter()
            .flat_map(|said| match said {
                Said::Search(uids) => uids,
                _ => Vec::new(),
            })
            .collect();
        found.sort_unstable();
        Ok(found)
    }

    fn flags(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, Vec<String>>, ServerError> {
        if uids.is_empty() {
            return Ok(BTreeMap::new());
        }
        let fetched = self.fetched(&format!("UID FETCH {} (FLAGS)", uid_set(uids)))?;
        Ok(fetched.into_iter().filter_map(|f| Some((f.uid?, f.flags?))).collect())
    }

    fn enable_condstore(&mut self) -> Result<bool, ServerError> {
        let offers = |name: &str| self.connection.capabilities.iter().any(|c| c == name);
        if !(offers("CONDSTORE") && offers("ENABLE")) {
            return Ok(false);
        }
        match self.connection.command(b"ENABLE CONDSTORE") {
            Ok(answer) => Ok(answer.status == "OK"),
            Err(Error::Unreachable(class)) => Err(ServerError::Unreachable(class)),
            Err(Error::Refused(_)) => Ok(false),
        }
    }

    fn headers(&mut self, uids: &[u32], fields: &[&str], gmail: bool) -> Result<Vec<Headers>, ServerError> {
        let fields: Result<Vec<&str>, ServerError> = fields.iter().map(|f| atom(f)).collect();
        let items = format!(
            "(UID FLAGS {}BODY.PEEK[HEADER.FIELDS ({})])",
            if gmail { "X-GM-THRID X-GM-LABELS " } else { "" },
            fields?.join(" ")
        );
        let mut out = Vec::with_capacity(uids.len());
        for batch in uids.chunks(HEADER_BATCH) {
            for fetched in self.fetched(&format!("UID FETCH {} {items}", uid_set(batch)))? {
                let (Some(uid), Some(raw)) = (fetched.uid, fetched.section) else { continue };
                out.push(Headers {
                    uid,
                    flags: fetched.flags.unwrap_or_default(),
                    raw,
                    gmail_thread: if gmail { fetched.gmail_thread.map(|t| t.to_string()) } else { None },
                    gmail_labels: if gmail { fetched.gmail_labels } else { None },
                });
            }
        }
        out.sort_by_key(|h| h.uid);
        Ok(out)
    }

    fn body(&mut self, uid: u32) -> Result<Option<Vec<u8>>, ServerError> {
        let fetched = self.fetched(&format!("UID FETCH {uid} (BODY.PEEK[])"))?;
        Ok(fetched.into_iter().find_map(|f| f.section))
    }

    fn store_flags(&mut self, uids: &[u32], add: bool, flags: &[&str]) -> Result<(), ServerError> {
        let sign = if add { '+' } else { '-' };
        self.send(&format!("UID STORE {} {sign}FLAGS {}", uid_set(uids), flag_list(flags)?))?;
        Ok(())
    }

    fn store_labels(&mut self, uids: &[u32], add: bool, labels: &[&str]) -> Result<(), ServerError> {
        let sign = if add { '+' } else { '-' };
        let labels = gmail_labels(labels).ok_or(ServerError::Refused("BadValue"))?;
        self.send(&format!("UID STORE {} {sign}X-GM-LABELS {labels}", uid_set(uids)))?;
        Ok(())
    }

    fn copy(&mut self, uids: &[u32], dest: &str) -> Result<(), ServerError> {
        self.send(&format!("UID COPY {} {}", uid_set(uids), folder(dest)?))?;
        Ok(())
    }

    fn uid_move(&mut self, uids: &[u32], dest: &str) -> Result<(), ServerError> {
        self.send(&format!("UID MOVE {} {}", uid_set(uids), folder(dest)?))?;
        Ok(())
    }

    fn expunge(&mut self, uids: &[u32]) -> Result<(), ServerError> {
        if uids.is_empty() {
            return Ok(()); // a UID EXPUNGE of nothing would be refused; a bare EXPUNGE is never sent
        }
        self.send(&format!("UID EXPUNGE {}", uid_set(uids)))?;
        Ok(())
    }

    fn append(
        &mut self,
        name: &str,
        raw: &[u8],
        flags: &[&str],
        when: Option<&str>,
    ) -> Result<Option<u32>, ServerError> {
        let mut command = format!("APPEND {}", folder(name)?);
        if !flags.is_empty() {
            command.push(' ');
            command.push_str(&flag_list(flags)?);
        }
        if let Some(when) = when {
            command.push(' ');
            command.push_str(&quoted(when)?);
        }
        let message = crlf(raw);
        let answer = self.connection.exchange(command.as_bytes(), Some(&message))?.done(REFUSED)?;
        Ok(answer.said().into_iter().find_map(|said| match said {
            Said::Code(Code::AppendUid(uid)) => Some(uid),
            _ => None,
        }))
    }

    fn shutdown(&mut self) {
        let _ = self.connection.wire.stream.shutdown();
    }
}
