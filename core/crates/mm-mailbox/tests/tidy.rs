//! The newest Tidy run a person can still take back, from the undo stack.

use mm_kernel::errors::Fault;
use mm_mailbox::tidy::{Entry, Filed, UndoEntries, is_run, latest};

#[derive(Debug)]
struct Stack(Vec<Entry>);

impl UndoEntries for Stack {
    fn entries(&self) -> Result<Vec<Entry>, Fault> {
        Ok(self.0.clone())
    }
}

fn filed(subject: &str) -> Filed {
    Filed { subject: Some(subject.to_owned()), who: Some("Lin".to_owned()) }
}

fn entry(group: Option<&str>, subjects: &[&str]) -> Entry {
    Entry { group: group.map(str::to_owned), children: subjects.iter().map(|s| filed(s)).collect() }
}

#[test]
fn a_run_is_a_group_named_for_its_day() {
    assert!(is_run(Some("tidy-2026-11-02")));
    assert!(!is_run(Some("bulk-1")) && !is_run(None));
}

#[test]
fn the_newest_run_with_what_it_filed_the_last_first() {
    let stack = Stack(vec![
        entry(Some("tidy-2026-11-01"), &["old"]),
        entry(Some("tidy-2026-11-02"), &["first", "second"]),
        entry(Some("bulk-1"), &["a person's"]),
        entry(None, &[]),
    ]);
    let run = latest(&stack).expect("read").expect("a run");
    assert_eq!(run.group, "tidy-2026-11-02");
    assert_eq!(run.filed, [filed("second"), filed("first")]);
}

#[test]
fn with_no_run_on_the_stack_there_is_none() {
    assert_eq!(latest(&Stack(vec![entry(Some("bulk-1"), &["x"])])).expect("read"), None);
}
