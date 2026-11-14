//! What `mailmind-server` runs on (`real::seams`). The sanitizer there is the sanitizer itself: nothing in the
//! server can take it away, and only the test launcher (mm-served), which is never shipped, hands the app another.

use mm_content::sanitize::sanitize;
use mm_mailbox::ports::Sanitizer;
use mm_server::real::{self, Sanitizing};

const HOSTILE: &str = "<p>Hello</p><script>parent.document.body.dataset.pwned = 1</script>\
                       <img src=\"https://tracker.example.test/open.png\" onerror=\"parent.pwned = 1\">\
                       <a href=\"javascript:parent.pwned = 1\">click</a>";

#[test]
fn mailmind_servers_seams_sanitize() {
    let seams = real::seams();
    for load_images in [false, true] {
        for fold in [false, true] {
            let made = seams.sanitizer.sanitize(HOSTILE, load_images, fold);
            assert_eq!(made, sanitize(HOSTILE, load_images, fold));
            assert_eq!(made, Sanitizing.sanitize(HOSTILE, load_images, fold));
            assert!(made.html.contains("Hello"), "{}", made.html);
            for active in ["<script", "onerror", "javascript:"] {
                assert!(!made.html.contains(active), "{active} in {}", made.html);
            }
        }
    }
    assert_eq!(seams.sanitizer.sanitize(HOSTILE, false, true).blocked.images, 1, "the tracker is held back");
}
