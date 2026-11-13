//! The login Keychain through a program that speaks as /usr/bin/security does: a secret goes in on its standard input
//! and never on its command line, an item that isn't there is none, a keep is read back, and a program the tripwire
//! refuses never starts. No test reaches the real Keychain.

#![cfg(unix)]
#![allow(clippy::unwrap_used)]

use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use mm_kernel::seams::{Keychain, Outside, Secret, Tripped};
use mm_server::login_keychain::LoginKeychain;

/// Each item a file in items/ beside the program, holding its secret as hex; each command line it was given, in
/// argv beside it.
const SECURITY: &str = r#"#!/bin/sh
here=$(dirname "$0")
echo "$@" >> "$here/argv"
case "$1" in
find-generic-password)
    [ -f "$here/items/$5" ] || exit 44
    awk 'BEGIN { h = "0123456789abcdef" }
         { for (i = 1; i < length($0); i += 2)
               printf "%c", (index(h, substr($0, i, 1)) - 1) * 16 + index(h, substr($0, i + 1, 1)) - 1 }
         END { print "" }' "$here/items/$5"
    ;;
-i)
    read -r line
    set -- $line
    while [ $# -gt 0 ]; do
        case "$1" in -a) name=$2; shift ;; -X) hex=$2; shift ;; esac
        shift
    done
    printf '%s\n' "$hex" > "$here/items/$name"
    ;;
delete-generic-password)
    [ -f "$here/items/$5" ] || exit 44
    rm "$here/items/$5"
    ;;
*)
    exit 1
    ;;
esac
"#;

/// Ends well and keeps nothing, as `security -i` can.
const FORGETFUL: &str = "#!/bin/sh\n[ \"$1\" = find-generic-password ] && exit 44\nexit 0\n";
/// An unlock prompt nobody answers.
const WAITING: &str = "#!/bin/sh\nexec sleep 30\n";
/// The Keychain locked: every question refused.
const LOCKED: &str = "#!/bin/sh\nexit 36\n";

/// The programs, written once before any test starts one, so none is still open for writing when one starts.
fn programs() -> &'static Path {
    static FOLDER: OnceLock<tempfile::TempDir> = OnceLock::new();
    FOLDER
        .get_or_init(|| {
            let folder = tempfile::tempdir().unwrap();
            std::fs::create_dir(folder.path().join("items")).unwrap();
            for (name, text) in
                [("security", SECURITY), ("forgetful", FORGETFUL), ("waiting", WAITING), ("locked", LOCKED)]
            {
                let path = folder.path().join(name);
                std::fs::write(&path, text).unwrap();
                std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
            }
            folder
        })
        .path()
}

#[derive(Debug)]
struct Open;

impl Outside for Open {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, _host: &str) -> Result<(), Tripped> {
        Ok(())
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

/// Refuses every program, as the tests' tripwire refuses `security`.
#[derive(Debug)]
struct NoPrograms;

impl Outside for NoPrograms {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, _host: &str) -> Result<(), Tripped> {
        Ok(())
    }

    fn run(&self, program: &str) -> Result<(), Tripped> {
        Err(Tripped(program.to_owned()))
    }
}

fn through(program: &str, outside: Arc<dyn Outside>) -> LoginKeychain {
    LoginKeychain::through(programs().join(program), outside, Duration::from_secs(20))
}

fn said() -> String {
    std::fs::read_to_string(programs().join("argv")).unwrap_or_default()
}

fn hex(text: &str) -> String {
    text.bytes().map(|b| format!("{b:02x}")).collect()
}

#[test]
fn a_secret_kept_is_read_back_and_never_goes_on_a_command_line() {
    let keychain = through("security", Arc::new(Open));
    let secret = "a pass \"word\" with 'quotes' $HOME; and spaces";
    keychain.put("account:ada@example.test", &Secret::new(secret.to_owned())).unwrap();
    assert_eq!(keychain.find("account:ada@example.test").unwrap().unwrap().expose(), secret);
    let lines = said();
    assert!(lines.contains("find-generic-password -s mailmind -a account:ada@example.test -w"), "{lines}");
    assert!(!lines.contains(secret) && !lines.contains(&hex(secret)), "the secret went on a command line");
}

#[test]
fn a_secret_kept_again_takes_the_place_of_the_last() {
    let keychain = through("security", Arc::new(Open));
    keychain.put("account:bea@example.test", &Secret::new("first-password".into())).unwrap();
    keychain.put("account:bea@example.test", &Secret::new("second-password".into())).unwrap();
    assert_eq!(keychain.find("account:bea@example.test").unwrap().unwrap().expose(), "second-password");
}

#[test]
fn an_item_that_isnt_there_is_none_and_taking_it_out_says_so() {
    let keychain = through("security", Arc::new(Open));
    assert!(keychain.find("account:nobody@example.test").unwrap().is_none());
    assert!(!keychain.delete("account:nobody@example.test").unwrap());
    keychain.put("account:cy@example.test", &Secret::new("cy-password".into())).unwrap();
    assert!(keychain.delete("account:cy@example.test").unwrap());
    assert!(keychain.find("account:cy@example.test").unwrap().is_none());
}

#[test]
fn a_keep_the_keychain_didnt_hold_is_a_problem() {
    let keychain = through("forgetful", Arc::new(Open));
    assert!(keychain.put("account:dee@example.test", &Secret::new("dee-password".into())).is_err());
}

#[test]
fn a_keychain_that_refuses_or_doesnt_answer_is_a_problem() {
    let locked = through("locked", Arc::new(Open));
    assert!(locked.find("account:ada@example.test").is_err());
    assert!(locked.delete("account:ada@example.test").is_err());
    let waiting = LoginKeychain::through(programs().join("waiting"), Arc::new(Open), Duration::from_millis(200));
    assert!(waiting.find("account:ada@example.test").is_err(), "stopped once past its time");
    let missing =
        LoginKeychain::through(PathBuf::from("/nonexistent/security"), Arc::new(Open), Duration::from_secs(5));
    assert!(missing.find("account:ada@example.test").is_err());
}

#[test]
fn a_name_or_a_secret_the_keychain_cant_keep_is_never_sent() {
    let keychain = through("security", Arc::new(Open));
    for name in ["", "-a", "account:two words@example.test", "account:a\nb", "account:ü@example.test"] {
        assert!(keychain.find(name).is_err(), "{name:?}");
        assert!(keychain.delete(name).is_err(), "{name:?}");
        assert!(keychain.put(name, &Secret::new("password".into())).is_err(), "{name:?}");
    }
    for secret in ["", "two\nlines", "caf\u{e9}", "tab\there"] {
        assert!(keychain.put("account:eve@example.test", &Secret::new(secret.to_owned())).is_err(), "{secret:?}");
    }
    assert!(!said().contains("account:eve@example.test"), "nothing was started for them");
}

#[test]
fn a_program_the_tripwire_refuses_never_starts() {
    let keychain = through("security", Arc::new(NoPrograms));
    assert!(keychain.find("account:fay@example.test").is_err());
    assert!(keychain.put("account:fay@example.test", &Secret::new("fay-password".into())).is_err());
    assert!(!said().contains("account:fay@example.test"));
}
