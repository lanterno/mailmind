"""The repository is public, so no real person's address may enter it (CLAUDE.md, "Nothing personal is committed"; the
supply chain row of test-strategy.md section 6). Every address in a tracked file is at a domain kept for examples and
tests (.test, .example, .invalid, .localhost, .local, example.com, .org and .net) or is one of the made-up addresses at a
real provider that the list below allows, which a test of a provider's presets or a classifier's rules needs. An address that is
neither fails here: use one at example.test, or, if a test needs that provider's domain, a made-up name added below."""
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]

ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
RESERVED = re.compile(r"(^|\.)(example\.(com|org|net)|[^.]+\.(test\w*|example|invalid|localhost|local))$")
NOT_A_DOMAIN = {"png", "jpg", "jpeg", "gif", "svg", "webp", "eml", "json", "txt"}    # a file named name@2x.png

# Made-up people at real providers, which a test of presets, sign-in words, link risk or the classifier's rules needs: the
# corpus's names and stock words at any domain, and a few made-up addresses more.
MADE_UP_NAMES = set("""
ada lin cy dee sam pia zed ann john a x me you hello news noreply no-reply
""".split())
MADE_UP = set("""
ceo@bank.co.uk lin.example@gmail.com udc80@gmail.com secure@company.com scammer@gmail.com prof@stanford.edu
newsletters@somesite.org mlmguy@gmail.com make.example@gmail.com fraudster@gmail.com desk@mail.bank.co.uk
desk@evil.co.uk deals@example.tk records@example.gov alice.smith@gmail.com ve@gmail.com ude00@gmail.com
udce9@gmail.com noreplyjo@gmail.com nick@nick.page lin2@gmail.com lin.mori@gmail.com li22@gmail.com
jo.newsom@gmail.com hana@kumo.studio abcdefghijklmnopqr@gmail.com abcdefghijklmnopq@gmail.com 0123456789ab@gmail.com
0123456789a@gmail.com
""".split())


def tracked_text():
    names = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout.split(b"\0")
    for name in filter(None, names):
        path = ROOT / name.decode()
        try:
            data = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            continue
        if b"\0" in data[:8192]:                       # an image, a font, a database: no text to read
            continue
        yield path, data.decode("utf-8", "replace")


def is_fine(address, domain):
    domain = domain.lower()
    return (domain.rsplit(".", 1)[-1] in NOT_A_DOMAIN or RESERVED.search(domain) is not None
            or address.lower() in MADE_UP or address.lower().split("@")[0] in MADE_UP_NAMES)


def test_every_address_in_the_repository_is_made_up():
    real = sorted({f"{path.relative_to(ROOT)}: an address at {m.group(1).lower()}"
                   for path, text in tracked_text() for m in ADDRESS.finditer(text)
                   if not is_fine(m.group(0), m.group(1))})
    # Says where and at which domain, never the address itself: a failure here is printed in CI's public log.
    assert real == []


def test_the_check_tells_a_real_address_from_a_made_up_one():
    assert is_fine("ada@example.test", "example.test")
    assert is_fine("lin@studio.example.test", "studio.example.test")
    assert is_fine("someone@example.com", "example.com")
    assert is_fine("icon@2x.png", "2x.png")
    assert is_fine("ada@gmail.com", "gmail.com")              # made up, and listed
    assert is_fine("hello@fastmail.com", "fastmail.com")      # a stock word, at any domain
    # Put together here, so this file holds no address the check above would refuse.
    at = "@"
    assert not is_fine(f"someone.real{at}gmail.com", "gmail.com")
    assert not is_fine(f"person{at}company.example.co", "company.example.co")
    assert sum(1 for _ in tracked_text()) > 1000
