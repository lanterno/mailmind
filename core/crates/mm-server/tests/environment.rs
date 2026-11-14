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

#[test]
fn the_bridges_password_and_its_1password_item_are_read_as_set_and_empty_is_unset() {
    let env = Environment::from_pairs([
        ("MAILMIND_PROTON_BRIDGE_PASS", " bridge-password "),
        ("MAILMIND_PROTON_OP_ITEM", "Proton Bridge"),
    ]);
    let given = env.bridge();
    assert_eq!(given.password.as_ref().map(mm_kernel::seams::Secret::expose), Some(" bridge-password "));
    assert_eq!(given.op_reference().as_deref(), Some("op://Private/Proton Bridge/password"));
    let given =
        Environment::from_pairs([("MAILMIND_PROTON_BRIDGE_PASS", ""), ("MAILMIND_PROTON_OP_ITEM", "")]).bridge();
    assert!(given.password.is_none() && given.op_item.is_none());
    assert!(Environment::default().bridge().op_reference().is_none());
}
