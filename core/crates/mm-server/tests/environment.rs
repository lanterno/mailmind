//! The environment as a program prints it: the names it holds, never a value, which may be a secret.

use mm_server::environment::Environment;

#[test]
fn printing_the_environment_names_its_variables_and_shows_no_value() {
    let key = "5e".repeat(32);
    let env = Environment::from_pairs([
        ("MAILMIND_DB_KEY", key.as_str()),
        ("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key"),
        ("MAILMIND_PROFILE", "full"),
    ]);
    let shown = format!("{env:?}");
    for name in ["MAILMIND_DB_KEY", "ANTHROPIC_API_KEY", "MAILMIND_PROFILE"] {
        assert!(shown.contains(name), "{shown}");
    }
    for value in [key.as_str(), "sk-ant-not-a-real-key", "full"] {
        assert!(!shown.contains(value), "{shown}");
    }
}
