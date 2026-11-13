"""The cases of ../test_mail_frame_scripts.py that take `wall`, run again with the sanitizer gone, as if it had missed
everything: the frame's sandbox has to stop all of it alone. Only the app in this process can be made to lose its
sanitizer, so they are collected here too, each the same test with this module's `wall`.
"""
import pytest

from mailmind.content import sanitize
from tests.e2e.browser.test_mail_frame_scripts import (  # noqa: F401  collected here, with the wall below
    asked_by_frames, test_nothing_in_a_message_runs_in_the_quote_of_a_reply,
    test_nothing_in_a_message_runs_or_asks_for_anything, test_plain_text_written_as_html_stays_text)

pytestmark = pytest.mark.browser


@pytest.fixture
def wall(monkeypatch):
    """The frame's sandbox alone: the sanitizer hands back what it was given."""
    monkeypatch.setattr(sanitize, "sanitize", lambda html, load_images=False: (html, {"blocked_images": 0, "blocked_css_urls": 0}))
    return "no sanitizer"
