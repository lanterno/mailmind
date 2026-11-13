//! MIME (mm-content), for the Python app.
//!
//! A raw message crosses as `bytes`, read in place with the interpreter released: a body can be megabytes, and a
//! sync parses while the app's other threads go on. What comes back is plain: a part's bytes as `bytes`, copied once
//! into Python and not decoded twice, its text as `str`, and an attachment as the tuple
//! `(part_id, filename, content_type, size, content_id, is_inline, travels)`.

use mm_content::mime;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyTuple};

/// An attachment as Python builds its `Attachment` from it.
type AttachmentTuple = (u32, String, String, usize, Option<String>, bool, bool);

/// A raw message read: `(text, html, attachments, to, cc, reply_to, date)`. `html` is not made safe yet.
#[pyfunction]
#[pyo3(signature = (raw))]
fn mime_parse<'py>(py: Python<'py>, raw: &[u8]) -> PyResult<Bound<'py, PyTuple>> {
    let parsed = py.detach(|| mime::parse(raw));
    let attachments: Vec<AttachmentTuple> = parsed
        .attachments
        .into_iter()
        .map(|a| (a.part_id, a.filename, a.content_type, a.size, a.content_id, a.is_inline, a.travels))
        .collect();
    (parsed.text, parsed.html, attachments, parsed.to, parsed.cc, parsed.reply_to, parsed.date).into_pyobject(py)
}

/// The leaf `part_id` names, as `mime_parse` numbered it: `(its bytes, its media type)`, or None.
#[pyfunction]
#[pyo3(signature = (raw, part_id))]
fn mime_extract_part<'py>(py: Python<'py>, raw: &[u8], part_id: u32) -> Option<(Bound<'py, PyBytes>, String)> {
    let (data, content_type) = py.detach(|| mime::extract_part(raw, part_id))?;
    Some((PyBytes::new(py, &data), content_type))
}

/// Every leaf with a Content-ID, as `(content_id, content_type, bytes)`; a Content-ID given twice is the later part's.
#[pyfunction]
#[pyo3(signature = (raw))]
fn mime_inline_parts<'py>(py: Python<'py>, raw: &[u8]) -> Vec<(String, String, Bound<'py, PyBytes>)> {
    let found = py.detach(|| mime::inline_parts(raw));
    found.into_iter().map(|inline| (inline.content_id, inline.content_type, PyBytes::new(py, &inline.data))).collect()
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(mime_parse, m)?)?;
    m.add_function(wrap_pyfunction!(mime_extract_part, m)?)?;
    m.add_function(wrap_pyfunction!(mime_inline_parts, m)?)?;
    Ok(())
}
