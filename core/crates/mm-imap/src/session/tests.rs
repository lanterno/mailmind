//! What a server's answer to LOGIN comes to.

use super::*;

#[test]
fn a_no_or_bad_to_login_is_a_refusal_unless_the_server_says_it_cant_for_now() {
    for said in ["[AUTHENTICATIONFAILED] invalid credentials", "[CLIENTBUG] no", "Invalid username or password", ""] {
        assert_eq!(login_failed(said), Error::Unreachable("LoginRefused"), "{said:?}");
    }
    for said in ["[UNAVAILABLE] try later", "[unavailable] try later", "busy [UNAVAILABLE]"] {
        assert_eq!(login_failed(said), Error::Unreachable("LoginUnavailable"), "{said:?}");
    }
}

#[test]
fn each_of_gmails_refusals_is_a_class_of_its_own() {
    for (said, class) in [
        (
            "[ALERT] Application-specific password required: https://support.google.com/accounts/answer/185833 (Failure)",
            "LoginNeedsAppPassword",
        ),
        (
            "[ALERT] Please log in via your web browser: https://support.google.com/mail/accounts/answer/78754 (Failure)",
            "LoginNeedsAppPassword",
        ),
        (
            "[WEBALERT https://accounts.google.com/ContinueSignIn?sarp=1&scc=1] Web login required.",
            "LoginNeedsWebSignIn",
        ),
        ("[ALERT] IMAP access is disabled for your domain. Please contact your domain administrator.", "LoginImapOff"),
        ("[ALERT] Your account is not enabled for IMAP use. Please visit your Gmail settings page.", "LoginImapOff"),
        ("[ALERT] APPLICATION-SPECIFIC PASSWORD REQUIRED", "LoginNeedsAppPassword"),
        ("[AUTHENTICATIONFAILED] Invalid credentials (Failure)", "LoginRefused"),
    ] {
        assert_eq!(login_failed(said), Error::Unreachable(class), "{said:?}");
    }
}
