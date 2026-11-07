"""What the rule book holds to, with no file, no database and no I/O."""
import pytest

from mailmind.curation.domain import rulebook

EMPTY = rulebook.as_sets(rulebook.empty())


def sets(**lists):
    return {**EMPTY, **{k: frozenset(v) for k, v in lists.items()}}


# ── one matcher ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("domain, entry, covered", [
    ("mail.example.test", "mail.example.test", True),
    ("news.mail.example.test", "mail.example.test", True),
    ("hotmail.example.test", "mail.example.test", False),   # a lookalike
    ("example.test", "mail.example.test", False),           # a parent isn't covered by its child
    ("", "mail.example.test", False),
])
def test_a_domain_entry_covers_itself_and_its_sub_domains(domain, entry, covered):
    assert rulebook.domain_covers(domain, entry) is covered
    assert rulebook.domain_listed(domain, [entry]) is covered
    assert (rulebook.expiry_hours(f"a@{domain}", {entry: 72}) == 72) is covered
    assert rulebook.decided(f"a@{domain}", domain, sets(keep_domains=[entry])) is covered


# ── a token ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw, token", [("  Ada@Example.TEST ", "ada@example.test"), ("Example.Test", "example.test"), ("  ", "")])
def test_a_rule_keeps_its_token_trimmed_and_lower_cased(raw, token):
    assert rulebook.token_of(raw) == token


@pytest.mark.parametrize("token, ok", [("ada@example.test", True), ("example.test", True), ("ada@local", True), ("example", False), ("", False)])
def test_a_rule_names_an_address_or_a_domain(token, ok):
    assert rulebook.is_token(token) is ok


# ── a token is never in both ──────────────────────────────────────────────────
@pytest.mark.parametrize("token, kept, blocked", [("ada@example.test", "keep_senders", "block_senders"),
                                                  ("example.test", "keep_domains", "block_domains")])
def test_a_verdict_takes_the_token_out_of_the_opposite_list(token, kept, blocked):
    rules = rulebook.empty()
    assert rulebook.add(rules, token, "keep") == kept
    assert rulebook.add(rules, token, "block") == blocked
    assert (rules[kept], rules[blocked]) == ([], [token])
    assert rulebook.add(rules, token, "keep") == kept
    assert (rules[kept], rules[blocked]) == ([token], [])


def test_removing_leaves_shelf_life_alone():
    rules = rulebook.empty()
    rulebook.add(rules, "example.test", "keep")
    rulebook.set_expiry(rules, "example.test", 72)
    rulebook.remove(rules, "example.test")
    assert rules["keep_domains"] == [] and rules[rulebook.EXPIRE] == {"example.test": 72}


def test_a_shelf_life_of_nothing_clears_it():
    rules = rulebook.empty()
    rulebook.set_expiry(rules, "example.test", 72)
    rulebook.set_expiry(rules, "example.test", 0)
    assert rules[rulebook.EXPIRE] == {}


# ── who is left to decide on ──────────────────────────────────────────────────
def senders(*addrs):
    return [{"addr": a} for a in addrs]


def test_only_senders_no_rule_covers_are_left():
    rows = senders("kept@example.test", "blocked@example.test", "a@kept.example.test", "a@sub.blocked.example.test", "new@other.test")
    rules = sets(keep_senders=["kept@example.test"], block_senders=["blocked@example.test"], keep_domains=["kept.example.test"],
                 block_domains=["blocked.example.test"])
    assert [r["addr"] for r in rulebook.undecided(rows, rules)] == ["new@other.test"]


def test_with_no_rules_everyone_is_left():
    assert len(list(rulebook.undecided(senders("a@example.test", "b@example.test"), EMPTY))) == 2


# ── a keep rule, over whatever else ───────────────────────────────────────────
@pytest.mark.parametrize("addr, kept", [
    ("Kept@Example.test", True), ("a@sub.kept.example.test", True), ("a@kept.example.test", True),
    ("other@example.test", False), ("", False), (None, False),
])
def test_a_keep_rule_covers_its_sender_and_domain_whatever_the_case(addr, kept):
    assert rulebook.keep_ruled(addr, sets(keep_senders=["kept@example.test"], keep_domains=["kept.example.test"])) is kept


def test_a_block_rule_is_no_keep_rule():
    assert not rulebook.keep_ruled("a@example.test", sets(block_senders=["a@example.test"], block_domains=["example.test"]))


# ── shelf life ────────────────────────────────────────────────────────────────
def test_of_several_domains_the_shortest_shelf_life_wins():
    assert rulebook.expiry_hours("a@sub.example.test", {"example.test": 72, "sub.example.test": 24}) == 24


def test_a_bare_word_is_no_domain_entry():
    assert rulebook.expiry_hours("a@sub.example", {"example": 72}) is None
