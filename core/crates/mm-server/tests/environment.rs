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

#[test]
fn mailmind_model_is_read_without_the_spaces_python_strips_and_blank_is_unset() {
    let model = |value: &str| Environment::from_pairs([("MAILMIND_MODEL", value)]).model();
    assert_eq!(model("claude-sonnet-5-5"), Some("claude-sonnet-5-5".to_owned()));
    // A space, a line break, and the separators `str.strip` also takes off (U+001C to U+001F).
    assert_eq!(model(" \tclaude-sonnet-5-5\n"), Some("claude-sonnet-5-5".to_owned()));
    assert_eq!(model("\u{1c}claude-sonnet-5-5\u{1f}\u{a0}"), Some("claude-sonnet-5-5".to_owned()));
    for blank in ["", " ", "\n\t", "\u{1d}\u{3000}"] {
        assert_eq!(model(blank), None, "{blank:?}");
    }
    assert_eq!(Environment::default().model(), None);
}

#[test]
fn each_account_has_a_gate_of_its_own_unless_mailmind_workers_says_one() {
    let per_account = |value: &str| Environment::from_pairs([("MAILMIND_WORKERS", value)]).gate_per_account();
    assert!(Environment::default().gate_per_account(), "unset, a gate for each account");
    for each in ["account", "", "acount"] {
        assert!(per_account(each), "{each:?}");
    }
    for one in ["one", " One ", "global", "GLOBAL"] {
        assert!(!per_account(one), "{one:?}");
    }
}
