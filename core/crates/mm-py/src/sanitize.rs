//! The HTML sanitizer (mm-content), for the Python app.
//!
//! `sanitize_html` gives the sanitized HTML and what it blocked, as `(html, images, links, css_urls)`, which
//! `mailmind.content.sanitize` builds its own answer from. The CSS sanitizer's two entries give their CSS and how
//! many declarations a remote url() dropped, which the caller adds to its own count. Its caps cross as the `CSS_*`
//! constants, so the Python app keeps no copy of its own.

use std::borrow::Cow;

use mm_content::sanitize::{self, CssContext, Read, css};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyString};

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(sanitize_html, m)?)?;
    m.add_function(wrap_pyfunction!(without_fold, m)?)?;
    m.add_function(wrap_pyfunction!(sanitize_inline_style, m)?)?;
    m.add_function(wrap_pyfunction!(sanitize_css_stylesheet, m)?)?;
    m.add_function(wrap_pyfunction!(html_tokens, m)?)?;
    m.add("CSS_INLINE_MAX", css::INLINE_MAX)?;
    m.add("CSS_DECL_VALUE_MAX", css::DECL_VALUE_MAX)?;
    m.add("CSS_PROP_MAX", css::PROP_MAX)?;
    m.add("CSS_INLINE_DECLS", css::INLINE_DECLS)?;
    m.add("CSS_SHEET_MAX", css::SHEET_MAX)?;
    m.add("CSS_SHEET_RULES", css::SHEET_RULES)?;
    m.add("CSS_SHEET_DECLS", css::SHEET_DECLS)?;
    m.add("CSS_NEST_MAX", css::NEST_MAX)?;
    m.add("CSS_SELECTOR_MAX", css::SELECTOR_MAX)?;
    m.add("CSS_URL_ARG_MAX", css::URL_ARG_MAX)?;
    Ok(())
}

/// A Python string as the sanitizer reads it. A lone surrogate, which no Rust string holds, reads as U+FFFD, one for
/// one, so every cap counts the characters Python counted, and nothing the sanitizer asks of a character tells the
/// two apart. A browser could show neither.
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

/// `(html, blocked_images, blocked_links, blocked_css_urls)` for a message's HTML. `load_images` keeps remote images;
/// `fold` puts a quoted reply behind a toggle, for reading.
#[pyfunction]
#[pyo3(signature = (html, load_images, fold))]
fn sanitize_html(html: &Bound<'_, PyString>, load_images: bool, fold: bool) -> PyResult<(String, usize, usize, usize)> {
    let sanitized = sanitize::sanitize(&text(html)?, load_images, fold);
    let blocked = sanitized.blocked;
    Ok((sanitized.html, blocked.images, blocked.links, blocked.css_urls))
}

/// Sanitized HTML from before replies stopped carrying the fold, without it.
#[pyfunction]
#[pyo3(signature = (html))]
fn without_fold(html: &Bound<'_, PyString>) -> PyResult<String> {
    Ok(sanitize::without_fold(&text(html)?))
}

/// `(css, blocked_css_urls)` for a `style` attribute's value.
#[pyfunction]
#[pyo3(signature = (value, load_images))]
fn sanitize_inline_style(value: &Bound<'_, PyString>, load_images: bool) -> PyResult<(String, usize)> {
    let mut ctx = CssContext::new(load_images);
    let css = sanitize::sanitize_inline_style(&text(value)?, &mut ctx);
    Ok((css, ctx.blocked_urls))
}

/// `(css, blocked_css_urls)` for a `<style>` element's text.
#[pyfunction]
#[pyo3(signature = (css, load_images))]
fn sanitize_css_stylesheet(css: &Bound<'_, PyString>, load_images: bool) -> PyResult<(String, usize)> {
    let mut ctx = CssContext::new(load_images);
    let sheet = sanitize::sanitize_css_stylesheet(&text(css)?, &mut ctx);
    Ok((sheet, ctx.blocked_urls))
}

/// A token as `html_tokens` gives it: its kind, its name or text, and a start tag's attributes.
type Token = (&'static str, String, Vec<(String, String)>);

/// What a browser's tokenizer reads of `html`, as the sanitizer reads it: `("start", name, attrs)`, `("end", name,
/// [])`, `("text", text, [])` and `("comment", text, [])`, in order. For reading back what the sanitizer wrote.
#[pyfunction]
#[pyo3(signature = (html))]
fn html_tokens(html: &Bound<'_, PyString>) -> PyResult<Vec<Token>> {
    Ok(sanitize::read(&text(html)?)
        .into_iter()
        .map(|token| match token {
            Read::Start { name, attrs, .. } => ("start", name, attrs),
            Read::End { name } => ("end", name, Vec::new()),
            Read::Text(text) => ("text", text, Vec::new()),
            Read::Comment(text) => ("comment", text, Vec::new()),
        })
        .collect())
}
