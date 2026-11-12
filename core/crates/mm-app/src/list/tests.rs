use super::*;

#[test]
fn a_window_is_held_with_a_page_on_each_side() {
    assert_eq!(held_range(0, 20, 1000, 50), (0, 100));
    assert_eq!(held_range(120, 20, 1000, 50), (50, 200));
    assert_eq!(held_range(990, 20, 1000, 50), (900, 1000));
    assert_eq!(held_range(0, 20, 0, 50), (0, 0));
    assert_eq!(held_range(5000, 20, 1000, 50), (950, 1000));
}

#[test]
fn only_what_the_shell_lacks_is_sent() {
    assert_eq!(missing((0, 100), (0, 0)), vec![(0, 100)]);
    assert_eq!(missing((50, 200), (0, 100)), vec![(100, 200)]);
    assert_eq!(missing((0, 100), (50, 200)), vec![(0, 50)]);
    assert_eq!(missing((0, 300), (100, 200)), vec![(0, 100), (200, 300)]);
    assert_eq!(missing((500, 600), (0, 100)), vec![(500, 600)]);
    assert_eq!(missing((50, 100), (0, 200)), Vec::<(u32, u32)>::new());
}

#[test]
fn a_snippet_folds_whitespace_and_stops_at_its_length() {
    assert_eq!(snippet("  one\n\ttwo   three "), "one two three");
    let long = "é".repeat(400);
    assert_eq!(snippet(&long).chars().count(), SNIPPET_CHARS);
}
