"""The first-run guide: offered once when there's mail to show, walked a step at a time. Ported from
tests/e2e/browser/test_guide.py."""
from mmtest import clients


# Each client's guide, in its own words: the Mac's rail holds the accounts, and its Screener is "At the gate".
STEPS = {"page": ["Today’s brief", "Folders, and focus reading", "⌘K opens every command", "The Screener"],
         "mac": ["Today’s brief", "Folders, and your accounts", "⌘K opens every command", "At the gate"]}


# The page only: opening the page again is a load, and its guide's Next has the keyboard. The Mac's guide keeps the
# keyboard for its own keys (Return is Next), and opening the app again is another launch.
@clients("page")
def test_the_guide_is_offered_once_and_opening_the_app_again_does_not_offer_it(world, app):
    world.first_run()
    app.inbox.open("ada", "INBOX")
    assert app.guide.offered
    assert app.guide.title == STEPS["page"][0]
    focus = app.shown().focus
    assert focus["region"].startswith("dialog") and focus["label"] == "Next", focus
    assert world.app.get("/api/preferences").json["preferences"]["guide_offered"] is True

    app.inbox.open("ada", "INBOX")
    assert not app.guide.offered
    assert app.shown().list is not None


@clients("page", "mac")
def test_the_guide_walks_every_step_by_keyboard_and_no_key_reaches_the_list(world, app):
    steps = STEPS[app.client.kind]
    world.first_run()
    app.inbox.open("ada", "INBOX")
    for title in steps[:-1]:
        assert app.guide.title == title
        app.press("j")                                    # a single key goes nowhere while the guide is up
        assert app.shown().open is None
        app.guide.next()
    assert app.guide.title == steps[-1]
    assert "Done" in app.guide.buttons
    app.guide.next()
    assert not app.guide.offered
