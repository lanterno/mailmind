//! The contract for Swift and Kotlin, through UniFFI.
//!
//! Only this crate knows UniFFI exists. Its types mirror `mm_app`'s one for one, so replacing the generator (with
//! BoltFFI, say) changes this crate and nothing else. What crosses is kept small on purpose: a view sends a batch
//! of rows at most once a page, never a row at a time.

use std::sync::Arc;

use zeroize::Zeroize;

uniffi::setup_scaffolding!();

/// Why the core did not do what it was asked.
#[derive(Debug, PartialEq, Eq, uniffi::Error)]
pub enum CoreRefusal {
    KeyDoesNotFit,
    NotFound { what: String },
    Invalid { detail: String },
    Storage { detail: String },
}

impl std::fmt::Display for CoreRefusal {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        std::fmt::Display::fmt(&self.to_core(), f)
    }
}

impl CoreRefusal {
    fn to_core(&self) -> mm_app::Refusal {
        match self {
            CoreRefusal::KeyDoesNotFit => mm_app::Refusal::KeyDoesNotFit,
            CoreRefusal::NotFound { what } => mm_app::Refusal::NotFound { what: what.clone() },
            CoreRefusal::Invalid { detail } => mm_app::Refusal::Invalid { detail: detail.clone() },
            CoreRefusal::Storage { detail } => mm_app::Refusal::Storage { detail: detail.clone() },
        }
    }
}

impl From<mm_app::Refusal> for CoreRefusal {
    fn from(refusal: mm_app::Refusal) -> CoreRefusal {
        match refusal {
            mm_app::Refusal::KeyDoesNotFit => CoreRefusal::KeyDoesNotFit,
            mm_app::Refusal::NotFound { what } => CoreRefusal::NotFound { what },
            mm_app::Refusal::Invalid { detail } => CoreRefusal::Invalid { detail },
            mm_app::Refusal::Storage { detail } => CoreRefusal::Storage { detail },
        }
    }
}

/// Which messages a list shows.
#[derive(Clone, Debug, uniffi::Record)]
pub struct ListQuery {
    pub accounts: Vec<String>,
    pub folder: String,
}

/// One row as a list shows it.
#[derive(Clone, Debug, PartialEq, Eq, uniffi::Record)]
pub struct MessageRow {
    pub key: i64,
    pub sender: String,
    pub subject: String,
    pub snippet: String,
    /// Seconds since 1970 UTC; 0 when the message has no date.
    pub date: i64,
    pub unread: bool,
    pub flagged: bool,
    pub attachment: bool,
}

/// Rows that start at one position in the list.
#[derive(Clone, Debug, PartialEq, Eq, uniffi::Record)]
pub struct RowRange {
    pub first: u32,
    pub rows: Vec<MessageRow>,
}

/// The rows a shell lacks, and which rows to keep.
#[derive(Clone, Debug, PartialEq, Eq, uniffi::Record)]
pub struct ViewBatch {
    pub total: u32,
    pub held_first: u32,
    pub held_count: u32,
    pub ranges: Vec<RowRange>,
}

/// What a shell gives a view to hear from it. Calls come on the core's thread.
#[uniffi::export(with_foreign)]
pub trait ViewObserver: Send + Sync {
    fn on_batch(&self, batch: ViewBatch);
    fn on_refusal(&self, refusal: CoreRefusal);
}

/// One open profile.
#[derive(Debug, uniffi::Object)]
pub struct Session {
    inner: mm_app::Session,
}

#[uniffi::export]
impl Session {
    /// Open the database at `database` with `key`, 32 bytes from the platform's key store. The bytes are wiped
    /// here once the core holds them.
    #[uniffi::constructor]
    pub fn open(database: String, mut key: Vec<u8>, workers: u8) -> Result<Arc<Session>, CoreRefusal> {
        let bytes: Result<[u8; 32], _> = key.as_slice().try_into();
        key.zeroize();
        let key = mm_app::DbKey::from_bytes(
            bytes.map_err(|_| CoreRefusal::Invalid { detail: "a database key is 32 bytes".into() })?,
        );
        let inner = mm_app::Session::open(mm_app::SessionConfig { database: database.into(), key, workers })?;
        Ok(Arc::new(Session { inner }))
    }

    /// Open a list. Nothing loads until `set_visible`.
    pub fn open_list(&self, query: ListQuery, observer: Arc<dyn ViewObserver>) -> Result<Arc<ListView>, CoreRefusal> {
        let query = mm_app::ListQuery { accounts: query.accounts, folder: query.folder };
        let inner = self.inner.open_list(query, Arc::new(Forward(observer)))?;
        Ok(Arc::new(ListView { inner }))
    }

    /// Wait until every window asked for so far has been served.
    pub fn settle(&self) {
        self.inner.settle();
    }
}

/// An open list. Releasing the last reference cancels what it was about to do.
#[derive(Debug, uniffi::Object)]
pub struct ListView {
    inner: Arc<mm_app::ListView>,
}

#[uniffi::export]
impl ListView {
    pub fn total(&self) -> u32 {
        self.inner.total()
    }

    pub fn set_visible(&self, first: u32, count: u32) {
        self.inner.set_visible(first, count);
    }
}

/// Make a fictional mailbox of `count` messages at `path`, for a shell's demo mode. Every address is at
/// example.test. It refuses a path where something already is.
#[uniffi::export]
pub fn build_fixture(path: String, count: u32) -> Result<(), CoreRefusal> {
    let key = mm_app::DbKey::from_bytes(mm_fixtures::FIXTURE_KEY);
    mm_fixtures::build(std::path::Path::new(&path), &key, count, mm_fixtures::SEED)?;
    Ok(())
}

/// The key a fixture is made with. It guards only fictional mail.
#[uniffi::export]
pub fn fixture_key() -> Vec<u8> {
    mm_fixtures::FIXTURE_KEY.to_vec()
}

/// The accounts and folder a fixture's mail is in, as the list query a demo opens.
#[uniffi::export]
pub fn fixture_inbox() -> ListQuery {
    ListQuery {
        accounts: mm_fixtures::ACCOUNTS.iter().map(|a| (*a).to_owned()).collect(),
        folder: mm_fixtures::FOLDER.to_owned(),
    }
}

/// A call that does nothing, to measure what crossing costs.
#[uniffi::export]
pub fn probe_noop() {}

/// `count` fictional rows, to measure what carrying a batch costs.
#[uniffi::export]
pub fn probe_rows(count: u32) -> Vec<MessageRow> {
    (0..count)
        .map(|i| MessageRow {
            key: i64::from(i),
            sender: "Ada Example".into(),
            subject: "Quarterly plan for Monday".into(),
            snippet: "I have attached the latest version with the changes we talked about. Could you take a look \
                      before the end of the week?"
                .into(),
            date: 1_793_491_200 - i64::from(i) * 600,
            unread: i % 7 == 0,
            flagged: false,
            attachment: i % 9 == 0,
        })
        .collect()
}

struct Forward(Arc<dyn ViewObserver>);

impl mm_app::ViewObserver for Forward {
    fn on_batch(&self, batch: mm_app::ViewBatch) {
        self.0.on_batch(ViewBatch {
            total: batch.total,
            held_first: batch.held_first,
            held_count: batch.held_count,
            ranges: batch
                .ranges
                .into_iter()
                .map(|r| RowRange { first: r.first, rows: r.rows.into_iter().map(row).collect() })
                .collect(),
        });
    }

    fn on_refusal(&self, refusal: mm_app::Refusal) {
        self.0.on_refusal(refusal.into());
    }
}

fn row(r: mm_app::MessageRow) -> MessageRow {
    MessageRow {
        key: r.key.0,
        sender: r.sender,
        subject: r.subject,
        snippet: r.snippet,
        date: r.date,
        unread: r.unread,
        flagged: r.flagged,
        attachment: r.attachment,
    }
}
