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
