//! `errors::caught`: what a background round runs in, so one that panics ends as a fault of the class `Panic` and
//! whoever ran it goes on.

use mm_kernel::errors::{Fault, PANIC, caught};

#[test]
fn work_that_returns_is_its_answer_and_work_that_panics_is_a_panic() {
    assert_eq!(caught(|| 7), Ok(7));
    assert_eq!(caught(|| -> Result<(), Fault> { Err(Fault::new("Timeout")) }), Ok(Err(Fault::new("Timeout"))));
    let mut touched = false;
    let said: Result<(), Fault> = caught(|| {
        touched = true;
        panic!("a message that quotes mail");
    });
    assert_eq!(said, Err(PANIC));
    assert_eq!(PANIC.class, "Panic");
    assert!(touched);
}
