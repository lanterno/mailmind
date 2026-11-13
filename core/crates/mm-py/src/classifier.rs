//! The classifier (mm-curation), for the Python app.
//!
//! The person's keep and block lists cross once, when the rules change, into a `RuleSets` the core holds; each message
//! then crosses as three strings and a flag. Sending lists of thousands on every message would cost far more than
//! classifying it.

use std::borrow::Cow;

use mm_curation::classifier;
use mm_curation::rules;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyString};

/// The keep and block lists, held by the core: `RuleSets(keep_senders, block_senders, keep_domains, block_domains)`,
/// each an iterable of strings.
#[pyclass(frozen, module = "mailmind_leaves")]
struct RuleSets(rules::RuleSets);

#[pymethods]
impl RuleSets {
    #[new]
    fn new(
        keep_senders: &Bound<'_, PyAny>,
        block_senders: &Bound<'_, PyAny>,
        keep_domains: &Bound<'_, PyAny>,
        block_domains: &Bound<'_, PyAny>,
    ) -> PyResult<Self> {
        Ok(Self(rules::RuleSets::new(
            entries(keep_senders)?,
            entries(block_senders)?,
            entries(keep_domains)?,
            entries(block_domains)?,
        )))
    }
}

fn entries(list: &Bound<'_, PyAny>) -> PyResult<Vec<String>> {
    list.try_iter()?
        .map(|entry| {
            let entry = entry?;
            Ok(text(entry.cast::<PyString>()?)?.into_owned())
        })
        .collect()
}

/// A Python string as the classifier reads it. A lone surrogate, which no Rust string holds, reads as U+FFFD, one
/// for one, so what counts or classifies characters answers as it did in Python: neither is a letter, a digit, a
/// space or cased.
fn text<'a>(s: &'a Bound<'_, PyString>) -> PyResult<Cow<'a, str>> {
    if let Ok(s) = s.to_str() {
        return Ok(Cow::Borrowed(s));
    }
    let units = s.call_method1("encode", ("utf-32-le", "surrogatepass"))?;
    let units = units.cast::<PyBytes>()?.as_bytes();
    let (units, _) = units.as_chunks::<4>();
    Ok(Cow::Owned(
        units
            .iter()
            .map(|unit| char::from_u32(u32::from_le_bytes(*unit)).unwrap_or(char::REPLACEMENT_CHARACTER))
            .collect(),
    ))
}

fn text_or_none<'a>(s: Option<&'a Bound<'_, PyString>>) -> PyResult<Option<Cow<'a, str>>> {
    s.map(text).transpose()
}

/// `(bucket, confidence, reason)` for a message: its From address, subject and display name, and whether it carried a
/// List-Unsubscribe or List-ID header.
#[pyfunction]
#[pyo3(signature = (from_addr, subject, from_name, has_list_unsub, rules))]
fn classify(
    from_addr: &Bound<'_, PyString>,
    subject: &Bound<'_, PyString>,
    from_name: &Bound<'_, PyString>,
    has_list_unsub: bool,
    rules: &Bound<'_, RuleSets>,
) -> PyResult<(&'static str, &'static str, Cow<'static, str>)> {
    let verdict =
        classifier::classify(&text(from_addr)?, &text(subject)?, &text(from_name)?, has_list_unsub, &rules.get().0);
    Ok((verdict.bucket.name(), verdict.confidence.name(), verdict.reason))
}

/// Whether a local part looks like a person's, not a role's or a mailer's.
#[pyfunction]
#[pyo3(signature = (local))]
fn looks_human(local: Option<&Bound<'_, PyString>>) -> PyResult<bool> {
    Ok(classifier::looks_human(text_or_none(local)?.as_deref()))
}

/// Whether a reason `classify` gave says one of the person's rules decided.
#[pyfunction]
#[pyo3(signature = (reason))]
fn by_rule(reason: Option<&Bound<'_, PyString>>) -> PyResult<bool> {
    Ok(classifier::by_rule(text_or_none(reason)?.as_deref()))
}

/// Whether a bucket, by its name, is junk.
#[pyfunction]
#[pyo3(signature = (bucket))]
fn is_junk(bucket: Option<&Bound<'_, PyString>>) -> PyResult<bool> {
    Ok(classifier::is_junk(text_or_none(bucket)?.as_deref()))
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RuleSets>()?;
    m.add_function(wrap_pyfunction!(classify, m)?)?;
    m.add_function(wrap_pyfunction!(looks_human, m)?)?;
    m.add_function(wrap_pyfunction!(by_rule, m)?)?;
    m.add_function(wrap_pyfunction!(is_junk, m)?)?;
    Ok(())
}
