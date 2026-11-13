//! The search grammar (mm-discovery), for the Python app.
//!
//! `parse_query` gives a query's clauses as tuples, `(kind, value, field, negated, narrows)`, which
//! `mailmind.discovery.domain.query` builds its own clauses from. The grammar's words cross as the `QUERY_*`
//! constants, so the Python app keeps no copy of its own.

use std::collections::{HashMap, HashSet};

use mm_discovery::query::{self, QuerySpec, Value, Wall};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{IntoPyDict, PyBytes, PyString, PyTuple};

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = m.py();
    m.add_function(wrap_pyfunction!(parse_query, m)?)?;
    m.add("QUERY_MAX_CHARS", query::MAX_CHARS)?;
    m.add("QUERY_FIELDS", PyTuple::new(py, query::FIELDS)?)?;
    m.add("QUERY_FLAGS", PyTuple::new(py, query::FLAGS)?)?;
    m.add("QUERY_BUCKET_ALIAS", query::BUCKET_ALIAS.into_py_dict(py)?)?;
    m.add("QUERY_OPERATORS", PyTuple::new(py, query::OPERATORS)?)?;
    m.add("QUERY_IS_OFFERED", PyTuple::new(py, query::IS_OFFERED)?)?;
    m.add("QUERY_HAS_OFFERED", PyTuple::new(py, query::HAS_OFFERED)?)?;
    Ok(())
}

/// A query's clauses, each `(kind, value, field, negated, narrows)`. A date written with no zone is in this
/// process's own, placed by Python's `datetime` as the Python app placed it.
#[pyfunction]
#[pyo3(signature = (text))]
fn parse_query<'py>(py: Python<'py>, text: Option<&Bound<'py, PyString>>) -> PyResult<Vec<Bound<'py, PyTuple>>> {
    let zone = |wall: Wall| local_seconds(py, wall);
    let none = StandIns::default();
    let Some(text) = text else {
        return clauses(py, &QuerySpec::parse("", zone), &none);
    };
    if let Ok(text) = text.to_str() {
        return clauses(py, &QuerySpec::parse(text, zone), &none);
    }
    let (text, stand_ins) = StandIns::for_surrogates(text)?;
    let spec = QuerySpec::parse_with(&text, |written| query::seconds(&stand_ins.as_dated(written)?, zone));
    clauses(py, &spec, &stand_ins)
}

fn clauses<'py>(py: Python<'py>, spec: &QuerySpec, stand_ins: &StandIns) -> PyResult<Vec<Bound<'py, PyTuple>>> {
    spec.clauses
        .iter()
        .map(|c| {
            let value = match &c.value {
                None => py.None().into_bound(py),
                Some(Value::Text(text)) => stand_ins.restore(py, text)?,
                Some(Value::Seconds(seconds)) => seconds.into_pyobject(py)?.into_any(),
            };
            (c.kind.name(), value, c.field, c.negated, c.narrows).into_pyobject(py)
        })
        .collect()
}

/// The seconds since the epoch at a wall-clock time in this process's zone, as `datetime(...).timestamp()` gives
/// them, or None where that raises: for a time on 0001-01-01, say, which a zone can't look a day behind.
fn local_seconds(py: Python<'_>, wall: Wall) -> Option<i64> {
    let at = || -> PyResult<i64> {
        let datetime = py.import("datetime")?.getattr("datetime")?;
        let time = datetime.call1((wall.year, wall.month, wall.day, wall.hour, wall.minute, wall.second))?;
        // A whole number of seconds, which a float holds exactly.
        let seconds: f64 = time.call_method0("timestamp")?.extract()?;
        Ok(seconds as i64)
    };
    at().ok()
}

/// Lone surrogates, which a Python string may hold and a Rust one can't: an undecodable byte becomes one, and a
/// JSON body can send one. Each stands in the parse as a private-use character the query doesn't use, which the
/// grammar reads as Python read the surrogate, a character with no case that is no space. It goes back into the
/// values it reached.
#[derive(Default)]
struct StandIns {
    surrogate_of: HashMap<char, u32>,
}

impl StandIns {
    fn for_surrogates(text: &Bound<'_, PyString>) -> PyResult<(String, StandIns)> {
        let encoded = text.call_method1("encode", ("utf-32-le", "surrogatepass"))?.cast_into::<PyBytes>()?;
        let units: Vec<u32> =
            encoded.as_bytes().as_chunks::<4>().0.iter().map(|&unit| u32::from_le_bytes(unit)).collect();
        // Only the first MAX_CHARS characters after the leading space can reach a clause, so a stand-in need only
        // be missing from those, and Unicode's last private-use plane has room for every surrogate there is.
        let lead = units.iter().take_while(|&&unit| char::from_u32(unit).is_some_and(query::is_space)).count();
        let read: HashSet<u32> = units.iter().skip(lead).take(query::MAX_CHARS).copied().collect();
        let mut free = (0x10_0000..=0x10_fffd).filter(|unit| !read.contains(unit)).filter_map(char::from_u32);
        let mut stand_in = HashMap::new();
        let mut out = String::with_capacity(units.len());
        for unit in units {
            let c = match char::from_u32(unit) {
                Some(c) => c,
                None => match stand_in.get(&unit) {
                    Some(&c) => c,
                    None => {
                        let c = free.next().ok_or_else(|| PyValueError::new_err("no character left to stand in"))?;
                        stand_in.insert(unit, c);
                        c
                    }
                },
            };
            out.push(c);
        }
        Ok((out, StandIns { surrogate_of: stand_in.into_iter().map(|(unit, c)| (c, unit)).collect() }))
    }

    fn holds(&self, c: &char) -> bool {
        self.surrogate_of.contains_key(c)
    }

    /// What Python's `fromisoformat` read of a date holding a surrogate: one may be the character between the date
    /// and the time, the 7th, 8th or 10th, which it read as `T`, and any other made it no date at all.
    fn as_dated(&self, written: &str) -> Option<String> {
        let mut chars: Vec<char> = written.chars().collect();
        if !chars.iter().any(|c| self.holds(c)) {
            return Some(written.to_owned());
        }
        let at = [7, 8, 10]
            .into_iter()
            .take_while(|&at| at <= chars.len())
            .find(|&at| chars.get(at).is_some_and(|c| self.holds(c)))?;
        chars[at] = 'T';
        (!chars.iter().any(|c| self.holds(c))).then(|| chars.into_iter().collect())
    }

    /// `text` as a Python string, with its surrogates back.
    fn restore<'py>(&self, py: Python<'py>, text: &str) -> PyResult<Bound<'py, PyAny>> {
        if !text.chars().any(|c| self.holds(&c)) {
            return Ok(PyString::new(py, text).into_any());
        }
        let encoded: Vec<u8> = text
            .chars()
            .flat_map(|c| self.surrogate_of.get(&c).copied().unwrap_or(u32::from(c)).to_le_bytes())
            .collect();
        PyBytes::new(py, &encoded).call_method1("decode", ("utf-32-le", "surrogatepass"))
    }
}
