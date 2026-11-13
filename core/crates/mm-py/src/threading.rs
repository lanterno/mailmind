//! Threading (mm-mailbox), for the Python app.
//!
//! A copy crosses as the tuple `(pk, message_id, gmail_thread, in_reply_to, refs, thread_id, folder, ts)`, and a
//! conversation as `(root, copies, held)`. The work runs with the interpreter released: a sync threads every copy
//! of an account at once, and the app's other threads go on meanwhile.

use mm_mailbox::threading::{Conversation, Placement, StoredCopy, Threader};
use pyo3::prelude::*;

/// A copy as Python passes it.
type CopyTuple =
    (i64, Option<String>, Option<String>, Option<String>, Option<String>, Option<String>, String, Option<i64>);

/// A conversation as Python passes it back: its root, its copies and the ids it holds, in the set's order.
type ConversationTuple = (String, Vec<CopyTuple>, Vec<String>);

/// A placement as Python builds it: `given` and `merged`, as pairs in the dicts' order.
type PlacementTuple = (Vec<(i64, String)>, Vec<(String, String)>);

/// A conversation without its copies: its root and the ids it holds.
type Shape = (String, Vec<String>);

fn stored((pk, message_id, gmail_thread, in_reply_to, refs, thread_id, folder, ts): CopyTuple) -> StoredCopy {
    StoredCopy { pk, message_id, gmail_thread, in_reply_to, refs, thread_id, folder, ts }
}

fn copies(tuples: Vec<CopyTuple>) -> Vec<StoredCopy> {
    tuples.into_iter().map(stored).collect()
}

/// A copy holding only what its names are read from.
fn named(
    pk: i64,
    message_id: Option<String>,
    gmail_thread: Option<String>,
    in_reply_to: Option<String>,
    refs: Option<String>,
) -> StoredCopy {
    StoredCopy { pk, message_id, gmail_thread, in_reply_to, refs, ..StoredCopy::default() }
}

/// Where `m` stands in `arrivals`, which every copy a conversation holds is one of.
fn position(arrivals: &[StoredCopy], m: &StoredCopy) -> usize {
    (std::ptr::from_ref(m).addr() - arrivals.as_ptr().addr()) / size_of::<StoredCopy>()
}

fn plain(placement: Placement) -> PlacementTuple {
    (placement.given, placement.merged)
}

/// Gmail's thread id for a copy, stripped, or empty.
#[pyfunction]
#[pyo3(signature = (gmail_thread))]
fn thread_thrid(gmail_thread: Option<String>) -> String {
    named(0, None, gmail_thread, None, None).thrid().to_owned()
}

/// A copy's own id, or `pk:` and its pk when it has no usable one.
#[pyfunction]
#[pyo3(signature = (pk, message_id))]
fn thread_key(pk: i64, message_id: Option<String>) -> String {
    named(pk, message_id, None, None, None).key().into_owned()
}

/// The ids a copy says it follows: In-Reply-To's, then the last of References'.
#[pyfunction]
#[pyo3(signature = (in_reply_to, refs))]
fn thread_references(in_reply_to: Option<String>, refs: Option<String>) -> Vec<String> {
    named(0, None, None, in_reply_to, refs).references().into_iter().map(str::to_owned).collect()
}

/// Everything another copy can meet a copy by, sorted.
#[pyfunction]
#[pyo3(signature = (message_id, gmail_thread, in_reply_to, refs))]
fn thread_names(
    message_id: Option<String>,
    gmail_thread: Option<String>,
    in_reply_to: Option<String>,
    refs: Option<String>,
) -> Vec<String> {
    named(0, message_id, gmail_thread, in_reply_to, refs).names().into_iter().map(|name| name.into_owned()).collect()
}

/// The conversations among `arrivals`, each as its root, the positions of its arrivals and the ids it holds.
#[pyfunction]
#[pyo3(signature = (account, arrivals, touching))]
fn thread_conversations(
    py: Python<'_>,
    account: String,
    arrivals: Vec<CopyTuple>,
    touching: Vec<(String, Vec<String>)>,
) -> Vec<(String, Vec<usize>, Vec<String>)> {
    let arrivals = copies(arrivals);
    py.detach(|| {
        Threader::new(account)
            .conversations(&arrivals, &touching)
            .into_iter()
            .map(|c| {
                let at = c.arrivals.iter().map(|m| position(&arrivals, m)).collect();
                (c.root, at, c.held)
            })
            .collect()
    })
}

/// Conversations as Python passes them back: the copies each holds, and each, borrowing them.
fn rebuilt(tuples: Vec<ConversationTuple>) -> (Vec<Vec<StoredCopy>>, Vec<Shape>) {
    tuples.into_iter().map(|(root, arrivals, held)| (copies(arrivals), (root, held))).unzip()
}

fn conversations<'c>(arrivals: &'c [Vec<StoredCopy>], shapes: Vec<Shape>) -> Vec<Conversation<'c>> {
    shapes
        .into_iter()
        .zip(arrivals)
        .map(|((root, held), arrivals)| Conversation { root, arrivals: arrivals.iter().collect(), held })
        .collect()
}

/// The Gmail threads whose copies `thread_place` must see.
#[pyfunction]
#[pyo3(signature = (account, conversations))]
fn thread_contested(py: Python<'_>, account: String, conversations: Vec<ConversationTuple>) -> Vec<String> {
    let (arrivals, shapes) = rebuilt(conversations);
    py.detach(|| Threader::new(account).contested(&self::conversations(&arrivals, shapes)).into_iter().collect())
}

/// Where each conversation's arrivals go, and which ids that absorbs.
#[pyfunction]
#[pyo3(signature = (account, conversations, members))]
fn thread_place(
    py: Python<'_>,
    account: String,
    conversations: Vec<ConversationTuple>,
    members: Vec<CopyTuple>,
) -> PlacementTuple {
    let (arrivals, shapes) = rebuilt(conversations);
    let members = copies(members);
    py.detach(|| plain(Threader::new(account).place(&self::conversations(&arrivals, shapes), &members)))
}

/// Every copy an account holds, regrouped from what each names.
#[pyfunction]
#[pyo3(signature = (account, copies))]
fn thread_regroup(py: Python<'_>, account: String, copies: Vec<CopyTuple>) -> PlacementTuple {
    let copies = self::copies(copies);
    py.detach(|| plain(Threader::new(account).regroup(&copies)))
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("THREAD_REFERENCES_MAX", mm_mailbox::threading::REFERENCES_MAX)?;
    m.add_function(wrap_pyfunction!(thread_thrid, m)?)?;
    m.add_function(wrap_pyfunction!(thread_key, m)?)?;
    m.add_function(wrap_pyfunction!(thread_references, m)?)?;
    m.add_function(wrap_pyfunction!(thread_names, m)?)?;
    m.add_function(wrap_pyfunction!(thread_conversations, m)?)?;
    m.add_function(wrap_pyfunction!(thread_contested, m)?)?;
    m.add_function(wrap_pyfunction!(thread_place, m)?)?;
    m.add_function(wrap_pyfunction!(thread_regroup, m)?)?;
    Ok(())
}
