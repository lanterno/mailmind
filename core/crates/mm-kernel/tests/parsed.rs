//! A number as a file or a request gives it, read as Python's `int()` reads it (`mailmind.kernel.parsed.as_int`).

use mm_kernel::parsed::{as_int, text_as_int};
use serde_json::json;

#[test]
fn a_number_reads_as_int_reads_it() {
    for (given, read) in [
        (json!(993), Some(993)),
        (json!(-1), Some(-1)),
        (json!(true), Some(1)),
        (json!(false), Some(0)),
        (json!(7.9), Some(7)),
        (json!(-7.9), Some(-7)),
        (json!(" 143 "), Some(143)),
        (json!("+1_000"), Some(1000)),
        (json!(9_007_199_254_740_992_i64), Some(9_007_199_254_740_992)),
        (json!(-9_007_199_254_740_992_i64), Some(-9_007_199_254_740_992)),
    ] {
        assert_eq!(as_int(&given), read, "{given}");
    }
}

#[test]
fn what_isnt_a_number_or_is_past_two_to_the_53_reads_as_none() {
    for given in [
        json!(null),
        json!("nine"),
        json!(""),
        json!("7.0"),
        json!("1__0"),
        json!("_1"),
        json!("1_"),
        json!([993]),
        json!({"port": 993}),
        json!(9_007_199_254_740_993_i64),
        json!(18_446_744_073_709_551_615_u64),
        json!(1e300),
    ] {
        assert_eq!(as_int(&given), None, "{given}");
    }
}

#[test]
fn text_is_digits_with_a_sign_and_spaces_around() {
    assert_eq!(text_as_int("\t-42\n"), Some(-42));
    assert_eq!(text_as_int("- 42"), None);
    assert_eq!(text_as_int("99999999999999999999999"), None);
}
