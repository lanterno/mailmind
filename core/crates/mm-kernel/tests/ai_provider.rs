//! The AI providers' closed set, and the base URL a person may give one at (#49).

use mm_kernel::ai_provider::{AiProvider, BASE_URL_MOST, base_url, on_this_machine};

#[test]
fn the_set_is_named_in_its_order_and_claude_is_the_default() {
    let ids: Vec<&str> = AiProvider::ALL.iter().map(|p| p.id()).collect();
    assert_eq!(ids, AiProvider::IDS);
    assert_eq!(ids, ["anthropic", "openai", "gemini", "local", "compatible"]);
    assert_eq!(AiProvider::DEFAULT, AiProvider::Anthropic);
    for provider in AiProvider::ALL {
        assert_eq!(AiProvider::parse(provider.id()), Some(provider));
    }
    for none in ["", "Anthropic", "claude", "open-ai", " openai", "ollama"] {
        assert_eq!(AiProvider::parse(none), None, "{none:?}");
    }
    let labels: Vec<&str> = AiProvider::ALL.iter().map(|p| p.label()).collect();
    assert_eq!(
        labels,
        ["Claude (Anthropic)", "OpenAI", "Google Gemini", "A model on this Mac", "Another OpenAI-compatible service"]
    );
}

#[test]
fn a_model_on_this_mac_and_a_compatible_service_do_without_a_key() {
    let needs: Vec<bool> = AiProvider::ALL.iter().map(|p| p.needs_key()).collect();
    assert_eq!(needs, [true, true, true, false, false]);
}

#[test]
fn a_base_url_is_https_or_plain_http_to_this_machine_and_keeps_no_trailing_slash() {
    for (given, kept) in [
        ("https://api.example.test/v1", "https://api.example.test/v1"),
        ("https://api.example.test/v1/", "https://api.example.test/v1"),
        ("https://api.example.test:8443/", "https://api.example.test:8443"),
        ("HTTPS://api.example.test", "HTTPS://api.example.test"),
        ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1"),
        ("http://localhost:1234/v1/", "http://localhost:1234/v1"),
        ("http://LOCALHOST/v1", "http://LOCALHOST/v1"),
        ("http://[::1]:8080/v1", "http://[::1]:8080/v1"),
        ("http://[::1]/v1", "http://[::1]/v1"),
        ("http://127.0.0.1:1/v1", "http://127.0.0.1:1/v1"),
        ("https://api.example.test:65535/v1", "https://api.example.test:65535/v1"),
        ("https://10.0.0.5/v1", "https://10.0.0.5/v1"),
    ] {
        assert_eq!(base_url(given).as_deref(), Some(kept), "{given:?}");
    }
}

#[test]
fn a_base_url_with_a_login_a_query_a_fragment_or_plain_http_off_this_machine_is_none() {
    for given in [
        "",
        "api.example.test",
        "ftp://api.example.test",
        "http://api.example.test/v1",
        "http://10.0.0.5/v1",
        "http://127.0.0.2/v1",
        // This machine only by the names the Python app takes: 127.0.0.1, ::1 and localhost.
        "http://[0:0:0:0:0:0:0:1]/v1",
        "http://[::ffff:127.0.0.1]/v1",
        "http://localhost.example.test/v1",
        "https://user:secret@api.example.test/v1",
        "https://user@api.example.test",
        "https://api.example.test/v1?key=1",
        "https://api.example.test/v1#top",
        "https://",
        "https:///v1",
        "https://:443/v1",
        "https://api.example.test:99999/v1",
        "https://api.example.test:65536/v1",
        "https://api.example.test:port/v1",
        // A port the HTTP client couldn't read: none after the `:`, or nought.
        "https://api.example.test:/v1",
        "http://127.0.0.1:/v1",
        "http://localhost:",
        "http://[::1]:/v1",
        "https://api.example.test:0/v1",
        "http://127.0.0.1:00/v1",
        "https://[not-an-address]/v1",
        "https://api.example.test/v 1",
        "https://api.example.test/v1\r\nX-Other: 1",
        " https://api.example.test",
    ] {
        assert_eq!(base_url(given), None, "{given:?}");
    }
}

#[test]
fn a_base_url_runs_to_five_hundred_characters() {
    let head = "https://api.example.test/";
    let longest = format!("{head}{}", "a".repeat(BASE_URL_MOST - head.len()));
    assert_eq!(base_url(&longest).as_deref(), Some(longest.as_str()));
    assert_eq!(base_url(&format!("{longest}a")), None);
}

#[test]
fn a_url_is_on_this_machine_when_its_host_is_loopback() {
    for (url, here) in [
        ("http://127.0.0.1:11434/v1", true),
        ("https://localhost/v1", true),
        ("http://[::1]:1/", true),
        ("https://api.example.test/v1", false),
        ("http://10.0.0.5/v1", false),
        ("not a url", false),
    ] {
        assert_eq!(on_this_machine(url), here, "{url}");
    }
}
