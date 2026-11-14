//! What a demo or test home runs on: the Keychain as a file, a way out that reaches only this machine, and the event
//! stream's text read back into events.

use mm_host::stand_in::{FileKeychain, LoopbackOnly};
use mm_host::stream::{Event, Frames};
use mm_kernel::seams::{Keychain, Outside, Secret};

#[test]
fn only_this_machine_is_reached() {
    for host in ["127.0.0.1", "localhost", "LocalHost", "::1", "[::1]", "127.8.9.10"] {
        assert!(LoopbackOnly.reach(host).is_ok(), "{host} is this machine");
    }
    for host in ["api.anthropic.com", "imap.example.test", "10.0.0.1", "0.0.0.0", "", "localhost.example.test"] {
        assert!(LoopbackOnly.reach(host).is_err(), "{host} is not this machine");
    }
    assert!(LoopbackOnly.run("/usr/bin/security").is_err(), "no program runs, the login Keychain's least");
    assert!(LoopbackOnly.run("op").is_err(), "nor 1Password's");
}

#[test]
fn the_file_keeps_what_is_put_and_forgets_what_is_deleted() {
    let folder = tempfile::tempdir().expect("a temporary folder");
    let file = folder.path().join("keychain.json");
    let keychain = FileKeychain::new(file.clone());
    let found = |keychain: &FileKeychain| keychain.find("account:ada@example.test").ok().flatten();
    assert!(found(&keychain).is_none(), "no file, nothing kept");
    keychain.put("account:ada@example.test", &Secret::new("fictional".into())).expect("kept");
    assert_eq!(found(&keychain).as_ref().map(Secret::expose), Some("fictional"));
    assert_eq!(found(&FileKeychain::new(file.clone())).as_ref().map(Secret::expose), Some("fictional"), "in the file");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(&file).expect("written").permissions().mode();
        assert_eq!(mode & 0o777, 0o600, "readable by its owner alone");
    }
    assert_eq!(keychain.delete("account:ada@example.test"), Ok(true));
    assert_eq!(keychain.delete("account:ada@example.test"), Ok(false));
    assert!(keychain.put("a name with spaces", &Secret::new("x".into())).is_err(), "a name a Keychain can't take");
}

#[test]
fn the_file_is_what_the_tests_fake_keychain_writes() {
    let folder = tempfile::tempdir().expect("a temporary folder");
    let file = folder.path().join("keychain.json");
    std::fs::write(&file, r#"{"account:bea@example.test": "fictional-too"}"#)
        .expect("written as fake_keychain.py does");
    let found = FileKeychain::new(file).find("account:bea@example.test").ok().flatten();
    assert_eq!(found.as_ref().map(Secret::expose), Some("fictional-too"));
}

fn event(id: &str, name: &str, data: &str) -> Event {
    Event { id: id.into(), name: name.into(), data: data.into() }
}

#[test]
fn the_stream_reads_back_into_events() {
    let mut frames = Frames::default();
    let opening = "retry: 3000\nid: 0a1b2c3d-0\n: connected\n\nevent: hello\ndata: {\"build\": \"b-1\"}\n\n";
    assert_eq!(frames.take(opening.as_bytes()), vec![event("", "hello", "{\"build\": \"b-1\"}")]);
    assert_eq!(frames.take(b": keepalive\n\n"), vec![], "a comment is no event");
    // An event cut across chunks, mid-character too, comes whole once its blank line does.
    let mute = "id: 0a1b2c3d-1\nevent: mute\ndata: {\"account\": \"z\u{00e9}\"}\n\n".as_bytes();
    let cut = mute.iter().position(|&b| b == 0xc3).expect("a two-byte character") + 1;
    assert_eq!(frames.take(&mute[..cut]), vec![]);
    assert_eq!(frames.take(&mute[cut..]), vec![event("0a1b2c3d-1", "mute", "{\"account\": \"z\u{00e9}\"}")]);
    let two = b"id: 0a1b2c3d-2\nevent: outbox\ndata: {}\n\nid: 0a1b2c3d-3\nevent: resync\ndata: {}\n\n";
    assert_eq!(frames.take(two), vec![event("0a1b2c3d-2", "outbox", "{}"), event("0a1b2c3d-3", "resync", "{}")]);
}
