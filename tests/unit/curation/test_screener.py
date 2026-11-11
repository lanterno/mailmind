"""A person's verdict on a sender (mailmind.curation.application.screener.Screener.rule), kept in the rules file, with filing a
fake behind Curation's port: where the rule went, a removal that places nothing, a block that trashes the message it names now,
and a rule that won't do refused in its own words. The route is tests/integration/curation/test_curation_routes.py."""
import pytest

from mailmind.bootstrap.curation import rules
from mailmind.curation.application.screener import Screener
from mailmind.kernel.errors import Refused

LOOM = "news@loom.example.test"


class Filing:
    """The Mailbox, as the Screener asks it: trash a message now, and classify again what a rule covers."""
    def __init__(self, trash_raises=None):
        self.trash_raises = trash_raises
        self.said = []

    def trash_now(self, pk):
        self.said.append(("trash", pk))
        if self.trash_raises:
            raise self.trash_raises
        return True

    def reclassify(self, token):
        self.said.append(("reclassify", token))
        return 4

    def stats_changed(self):
        self.said.append(("stats",))


def rule(filing, **body):
    return Screener(rules, None, None, filing).rule(body)


def test_a_rule_says_where_it_went_and_one_removed_places_nothing(data_dir):
    filing = Filing()
    kept = rule(filing, token=" News@Loom.example.test ", verdict="keep")
    assert (kept["token"], kept["placed_in"], kept["reclassified"], kept["trashed"]) == (LOOM, "keep_senders", 4, False)
    assert kept["rules"]["keep_senders"] == [LOOM]
    removed = rule(filing, token=LOOM, verdict="remove")
    assert (removed["placed_in"], removed["rules"]["keep_senders"]) == (None, [])
    assert filing.said == [("reclassify", LOOM), ("stats",)] * 2


def test_a_block_naming_a_message_trashes_it_now(data_dir):
    filing = Filing()
    assert rule(filing, token=LOOM, verdict="block", pk="7")["trashed"] is True
    assert ("trash", 7) in filing.said and rules.load()["block_senders"] == [LOOM]


def test_a_block_whose_message_wont_go_still_blocks_and_says_so(data_dir):
    filing = Filing(trash_raises=RuntimeError("the server said no"))
    out = rule(filing, token=LOOM, verdict="block", pk=7)
    assert (out["trashed"], out["placed_in"]) == (False, "block_senders")
    assert ("reclassify", LOOM) in filing.said


def test_only_a_block_trashes(data_dir):
    filing = Filing()
    assert rule(filing, token=LOOM, verdict="keep", pk=7)["trashed"] is False
    assert ("trash", 7) not in filing.said


@pytest.mark.parametrize("body, said", [({"token": "nothing", "verdict": "keep"}, "invalid token (need a domain or an address)"),
                                        ({"verdict": "keep"}, "invalid token (need a domain or an address)"),
                                        ({"token": LOOM}, "verdict must be keep|block|remove"),
                                        ({"token": LOOM, "verdict": "maybe"}, "verdict must be keep|block|remove")])
def test_a_rule_that_wont_do_is_refused_in_its_own_words_and_changes_nothing(data_dir, body, said):
    filing = Filing()
    before = rules.load()
    with pytest.raises(Refused) as refused:
        rule(filing, **body)
    assert refused.value.said == said
    assert (rules.load(), filing.said) == (before, [])
