//! The snippets: what makes one, what is read back of those saved, and a change's use case over a store in memory.

#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use mm_compose::ports::SnippetsStore;
use mm_compose::snippets::{Snippets, clean, saved, trigger_of, without};
use mm_kernel::errors::{Failure, Fault};
use serde_json::{Map, Value, json};

#[derive(Debug, Default)]
struct Kept {
    value: Mutex<Option<Value>>,
    writes: Mutex<usize>,
}

impl SnippetsStore for Kept {
    fn read(&self) -> Result<Option<Value>, Fault> {
        Ok(self.value.lock().unwrap().clone())
    }

    fn change(&self, change: &mut dyn FnMut(Option<Value>) -> Option<Value>) -> Result<(), Fault> {
        let mut value = self.value.lock().unwrap();
        if let Some(after) = change(value.clone()) {
            *value = Some(after);
            *self.writes.lock().unwrap() += 1;
        }
        Ok(())
    }
}

fn asked(value: Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap()
}

fn refusal(failure: Failure) -> String {
    match failure {
        Failure::Problem(p) => p.said,
        Failure::Fault(f) => f.class.to_owned(),
    }
}

#[test]
fn a_trigger_is_read_however_it_was_typed() {
    assert_eq!(trigger_of(Some(&json!(" ;Sig "))).as_deref(), Some("sig"));
    assert_eq!(trigger_of(Some(&json!("\u{212a}elvin"))).as_deref(), Some("kelvin"));
    assert_eq!(trigger_of(Some(&json!("-x"))), None);
    assert_eq!(trigger_of(Some(&json!("a".repeat(33)))), None);
    assert_eq!(trigger_of(Some(&json!(1))), None);
    assert_eq!(trigger_of(None), None);
}

#[test]
fn what_was_saved_is_read_back_whole_or_not_at_all() {
    let raw = json!([{"trigger": "b", "text": "two\r\n"}, {"trigger": "a", "text": "one"}, {"trigger": "B", "text": "dup"},
                     {"trigger": "c", "text": "  "}, {"trigger": "d"}, "e", {"trigger": "x".repeat(40), "text": "y"}]);
    let read = clean(Some(&raw));
    let read: Vec<(&str, &str)> = read.iter().map(|s| (s.trigger.as_str(), s.text.as_str())).collect();
    assert_eq!(read, [("a", "one"), ("b", "two\n")]);
    assert!(clean(Some(&json!({"a": 1}))).is_empty());
    assert!(clean(None).is_empty());
}

#[test]
fn a_save_keeps_someones_words_or_says_why_not() {
    let now = clean(Some(&json!([{"trigger": "sig", "text": "Best"}])));
    let after = saved(&now, Some(&json!("Sig")), Some("Cheers\r\n\n")).unwrap();
    assert_eq!(after.len(), 1);
    assert_eq!(after[0].text, "Cheers");
    let said = |r: Result<_, mm_kernel::errors::Problem>| r.err().map(|p| p.said);
    assert_eq!(
        said(saved(&now, Some(&json!("?")), Some("x"))).as_deref(),
        Some("a trigger is up to 32 letters, digits, - or _")
    );
    assert_eq!(said(saved(&now, Some(&json!("a")), None)).as_deref(), Some("a snippet needs some text"));
    assert_eq!(
        said(saved(&now, Some(&json!("a")), Some(&"x".repeat(20_001)))).as_deref(),
        Some("a snippet can be up to 20,000 characters")
    );
    assert_eq!(without(&now, Some(&json!("nope"))), None);
    assert_eq!(without(&now, Some(&json!(";SIG"))), Some(Vec::new()));
}

#[test]
fn a_change_writes_only_what_changes() {
    let kept = Arc::new(Kept::default());
    let snippets = Snippets::new(kept.clone());
    let answer = snippets.post(&asked(json!({"verb": "save", "trigger": "sig", "text": "Best"}))).unwrap();
    assert_eq!(answer, json!({"ok": true, "snippets": [{"trigger": "sig", "text": "Best"}]}));
    let answer = snippets.post(&asked(json!({"verb": "delete", "trigger": "other"}))).unwrap();
    assert_eq!(answer, json!({"ok": true, "snippets": [{"trigger": "sig", "text": "Best"}]}));
    assert_eq!(*kept.writes.lock().unwrap(), 1);
    let refused = snippets.post(&asked(json!({"verb": "save", "trigger": "sig", "text": " "}))).unwrap_err();
    assert_eq!(refusal(refused), "a snippet needs some text");
    let refused = snippets.post(&asked(json!({"trigger": "sig"}))).unwrap_err();
    assert_eq!(refusal(refused), "verb must be save|delete");
    assert_eq!(*kept.writes.lock().unwrap(), 1);
    assert_eq!(snippets.listed().unwrap(), json!({"ok": true, "snippets": [{"trigger": "sig", "text": "Best"}]}));
}
