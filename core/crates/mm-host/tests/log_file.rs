//! The Mac app's log: the host keeps it in `mailmind.log` in its data folder, readable by its owner only, since an app
//! opened from the Finder has nowhere for standard error to go. A test binary of its own, since the log is the
//! process's. The home is a temporary folder with fictional data, its Keychain a file.

#![allow(clippy::unwrap_used)]

use mm_host::{Home, Host};
use serde_json::Value;

const KEY: &str = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0";

#[test]
fn opening_a_home_keeps_the_log_in_its_folder() {
    let folder = tempfile::tempdir().unwrap();
    let host = Host::open(Home {
        folder: folder.path().to_path_buf(),
        variables: vec![("MAILMIND_DB_KEY".into(), KEY.into())],
        stand_in: Some(folder.path().join("keychain.json")),
    })
    .unwrap();
    host.close();
    let path = folder.path().join("mailmind.log");
    let written: Vec<Value> =
        std::fs::read_to_string(&path).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    // The workers say which AI is in force as they start.
    assert!(written.iter().any(|l| l["logger"] == "ai" && l["engine"].is_string()), "{written:?}");
    assert!(written.iter().all(|l| l["ts"].as_str().is_some_and(|ts| ts.ends_with("+00:00"))));
    #[cfg(unix)]
    assert_eq!(
        std::os::unix::fs::PermissionsExt::mode(&std::fs::metadata(&path).unwrap().permissions()) & 0o777,
        0o600
    );
}
