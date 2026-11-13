//! A message before it is built, and once it is (`builder.MessageSpec`, `Built`, `attachments.Attachment`): what a
//! send, a save, an RSVP and an unsubscribe hand over to be built, and what building gives back.

use mm_kernel::seams::Moment;

/// What a message's files may come to, decoded (`limits.MAX_ATTACH_BYTES`).
pub const MAX_ATTACH_BYTES: usize = 25 << 20;
/// What a send's body may be: the files after base64, and the rest (`limits.MAX_SEND_POST`).
pub const MAX_SEND_POST: usize = 38 << 20;
/// The rest of a save beside its files: chips, subject, keys (`limits.RESUME_HEADROOM`).
pub const RESUME_HEADROOM: usize = 64 << 10;

/// A file a message carries.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Attachment {
    pub filename: String,
    pub content_type: String,
    pub data: Vec<u8>,
    /// Its own Content-Type parameters, in order, as an iMIP reply's `method` and `charset`.
    pub params: Vec<(String, String)>,
    /// The token a mark in the text names a picture by, for a picture a composer may put in the text.
    pub cid: Option<String>,
}

/// What to build. Recipients are as a person typed them: each a token, which may itself hold several addresses.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct MessageSpec {
    /// The sending account's address: From is only ever the account.
    pub account: String,
    /// The name From shows.
    pub account_name: String,
    pub to: Vec<String>,
    pub cc: Vec<String>,
    pub bcc: Vec<String>,
    pub subject: String,
    pub text: String,
    pub html: Option<String>,
    pub attachments: Vec<Attachment>,
    pub in_reply_to: Option<String>,
    pub references: Vec<String>,
}

/// A message built. `raw` holds no Bcc, which rides only in the envelope.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Built {
    /// The bytes, CRLF line ends.
    pub raw: Vec<u8>,
    /// Its Message-ID, angle brackets and all.
    pub message_id: String,
    /// Every recipient's addr-spec, each once, To then Cc then Bcc.
    pub envelope: Vec<String>,
    pub to_disp: Vec<String>,
    pub cc_disp: Vec<String>,
    pub bcc_disp: Vec<String>,
}

/// Random numbers, as Python's `random` draws them for a Message-ID and a MIME boundary. The program draws them from
/// the system; a test, as it likes.
pub trait Draws: Send + Sync + std::fmt::Debug {
    /// A number from 0 to 2^64 - 1.
    fn number(&self) -> u64;
}

/// What building a message takes from the world: the time its Date says and its Message-ID is minted at, this
/// process's id, which the Message-ID names as Python's does, and random numbers.
#[derive(Clone, Copy, Debug)]
pub struct Minting<'a> {
    pub now: Moment,
    pub pid: u32,
    pub draws: &'a dyn Draws,
}
