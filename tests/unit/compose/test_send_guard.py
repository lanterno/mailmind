"""The composer's send-time checks (web/src/features/compose/sendGuard.ts, as pure.js has it: the block between
/* send-guard:pure:start */ and /* send-guard:pure:end */): attachment wording in eight languages, what counts as typed
text, empty subjects, reply-all size, speed on long unbroken text, acknowledgements, and the exact wording of every line.
The block is pure JavaScript with no DOM, so this runs it once in node, like `just check-frontend` does. Its word lists
are tests/fixtures/send_guard_words.json, which sendGuard.test.ts reads too. All addresses are fictional."""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
START, END = "/* send-guard:pure:start */", "/* send-guard:pure:end */"
_WORDS = json.loads((ROOT / "tests" / "fixtures" / "send_guard_words.json").read_text(encoding="utf-8"))

# A default snapshot: a new message to one person, nothing waved through yet.
HARNESS = r"""
const S=o=>Object.assign({mode:"new",subject:"Plans",subject0:"",body:"",sigBlock:"",nAttach:0,fwdAttach:0,
  reading:[],sendAfter:0,now:1e9,to:["bo@example.test"],cc:[],mine:["ada@example.test"],autoCc:null,fresh:[],
  ack:{subject:false,attach:false,fanout:0}},o);
const keys=w=>w.map(x=>x.key);
const W=(t,weak)=>guardAttachWord(t,weak);
const ppl=n=>Array.from({length:n},(_,i)=>`p${i}@example.test`);
const R={};
for(const [name,fn] of Object.entries(CASES)){ try{ R[name]=fn(); }catch(e){ R[name]="THROW "+e; } if(STOP(R[name]))break; }
process.stdout.write(JSON.stringify(R));
"""

# Strong wording presents a file of your own, so it warns in every mode.
STRONG = _WORDS["strong"]
# Weak wording names a file without presenting it: in a reply it's usually the other person's.
WEAK = _WORDS["weak"]
NEVER = _WORDS["never"]
# Replies about the file they sent: weak at most, so a reply never warns.
REPLY_NEVER = _WORDS["reply_never"]
# Long runs with no spaces: a pasted base64 blob, minified JSON, a line of dashes. They run in a
# node process of their own, which stops at the first that takes SLOW_MS or more.
SLOW_MS = 1000     # about 120 ms on a quiet machine, 320 ms seen on a busy one, and it was 2-5 s when it was wrong
SLOW = ["'QUJD'.repeat(12500)", "'-'.repeat(50000)", "'_'.repeat(50000)", "'a-'.repeat(25000)", "'.'.repeat(50000)",
        "'@'.repeat(50000)", "'ال'.repeat(25000)", "', '.repeat(25000)", "'a'+' '.repeat(49998)+'b'",
        "JSON.stringify(Array.from({length:4000},(_,i)=>({k:i,v:'x'+i}))).slice(0,50000)"]

# Every line a warning can show: (what it says of itself, its text, the fix it offers).
LINES = [
    ("{key:'reading',name:'a.pdf',more:0}", "Still adding a.pdf. Send again once it’s added.", None),
    ("{key:'reading',name:'a.pdf',more:2}", "Still adding a.pdf and 2 more. Send again once they’re added.", None),
    ("{key:'late',whenText:'today 6:00 PM'}", "The time you picked, today 6:00 PM, has passed.",
     {"kind": "time", "label": "Pick a new time"}),
    ("{key:'subject'}", "There’s no subject.", {"kind": "subject", "label": "Add a subject"}),
    ("{key:'attach',word:'pièce jointe'}", "You wrote “pièce jointe”, but nothing is attached.",
     {"kind": "attach", "label": "Attach a file"}),
    ("{key:'fanout',n:7,removable:6}", "This reply goes to 7 people.",
     {"kind": "trim", "label": "Remove the 6 copied from the original"}),
    ("{key:'fanout',n:7,removable:0}", "This reply goes to 7 people.", {"kind": "review", "label": "Review recipients"}),
    ("{key:'new',addrs:['a@example.test','b@example.test']}", "New to you: a@example.test and b@example.test.", None),
    ("{key:'unchecked'}", "The checks before sending couldn’t run.", None),
    ("{key:'norcpt'}", "Add at least one recipient.", {"kind": "to", "label": "Add one"}),
    ("{key:'bad',v:'bo@',more:0}", "“bo@” isn’t a valid address.", {"kind": "bad", "label": "Edit it"}),
    ("{key:'bad',v:'bo@',more:1}", "“bo@” isn’t a valid address. 1 more needs fixing too.", {"kind": "bad", "label": "Edit it"}),
    ("{key:'bad',v:'bo@',more:2}", "“bo@” isn’t a valid address. 2 more need fixing too.", {"kind": "bad", "label": "Edit it"}),
]


def _cases():
    c = {}
    for i, t in enumerate(STRONG):
        c[f"strong{i}"] = f"()=>[W({json.dumps(t)},false), W({json.dumps(t)},true)]"
    for i, t in enumerate(WEAK):
        c[f"weak{i}"] = f"()=>[W({json.dumps(t)},false), W({json.dumps(t)},true)]"
    for i, t in enumerate(NEVER):
        c[f"never{i}"] = f"()=>W({json.dumps(t)},true)"
    for i, t in enumerate(REPLY_NEVER):
        c[f"reply_never{i}"] = (f"()=>[W({json.dumps(t)},false), keys(guardCheck(S({{mode:'reply',subject:'Re: x',"
                                f"subject0:'Re: x',body:{json.dumps(t)}}})))]")
    for i, (line, _, _) in enumerate(LINES):
        c[f"line{i}"] = f"()=>guardLine({line})"
    c.update({
        "echo": "()=>W('Voici le devis en pièce jointe.',false)",
        "typed_sig": r"""()=>guardTypedText('see attached\n\n-- \nAda','\n\n-- \nAda')""",
        "typed_sig_edited": r"""()=>guardTypedText('Hi there\n\n-- \nAda, attachments policy','\n\n-- \nZed')""",
        "typed_no_sigblock": r"""()=>guardTypedText('Hi there\n-- \nAda\nAttachments may contain viruses.','')""",
        "typed_attribution": r"""()=>guardTypedText('Thanks\nOn Mon, 3 Feb 2026, Dana <dana@example.test> wrote:\n> see attached\nsee attached','')""",
        "typed_forward": r"""()=>guardTypedText('FYI\n\n---------- Forwarded message ----------\nFrom: Dana\nsee attached','')""",
        "typed_outlook": r"""()=>guardTypedText('Noted\n-----Original Message-----\nsee attached','')""",
        "typed_quoted": r"""()=>guardTypedText('> see attached\nok, thanks','')""",
        "typed_keeps_above_sig": r"""()=>guardTypedText('see attached\n\n-- \nAda','\n\n-- \nAda')""",
        "typed_ps": r"""()=>guardTypedText('Hi Bo,\nnotes below.\n\n-- \nAda Lovelace\n\nP.S. I attached the deck.','\n\n-- \nAda Lovelace')""",
        "typed_below_sig": r"""()=>guardTypedText('\n\n-- \nAda Lovelace\nHi Bo, the deck is attached.','\n\n-- \nAda Lovelace')""",
        "typed_sig_mid_line": r"""()=>guardTypedText('See x-- \nAda Lovelace','\n\n-- \nAda Lovelace')""",
        "ps_check": r"""()=>keys(guardCheck(S({mode:'reply',subject:'Re: x',subject0:'Re: x',sigBlock:'\n\n-- \nAda',
            body:'Thanks.\n\n-- \nAda\n\nP.S. The deck is attached.'})))""",
        "disclaimer": r"""()=>keys(guardCheck(S({body:'Hello\n-- \nAda\nAttachments may contain viruses.'})))""",
        "subj_empty_reply": "()=>guardSubjectEmpty('','reply')",
        "subj_blank_new": "()=>guardSubjectEmpty('  ','new')",
        "subj_re_reply": "()=>guardSubjectEmpty('Re:','reply')",
        "subj_fwd_forward": "()=>guardSubjectEmpty('Fwd:','forward')",
        "subj_re_new": "()=>guardSubjectEmpty('Re:','new')",
        "subj_inherited": "()=>keys(guardCheck(S({mode:'reply',subject:'Re: Contract attached',subject0:'Re: Contract attached'})))",
        "subj_edited": "()=>keys(guardCheck(S({mode:'reply',subject:'Re: Contract attached, v2',subject0:'Re: Contract attached'})))",
        "subj_new": "()=>keys(guardCheck(S({subject:'Contract attached'})))",
        "subj_ack": "()=>keys(guardCheck(S({subject:'',ack:{subject:true,attach:false,fanout:0}})))",
        "reading": "()=>guardCheck(S({subject:'',body:'see attached',reading:['a.pdf','b.pdf']}))",
        "late_soon": "()=>keys(guardCheck(S({sendAfter:1e9+3})))",
        "late_hour": "()=>keys(guardCheck(S({sendAfter:1e9+3600})))",
        "late_is_hard": "()=>guardHard(guardCheck(S({sendAfter:1e9-60})))",
        "fwd_files": "()=>keys(guardCheck(S({mode:'forward',body:'see attached',fwdAttach:2})))",
        "fwd_no_files": "()=>keys(guardCheck(S({mode:'forward',body:'see attached',fwdAttach:0})))",
        "has_attach": "()=>keys(guardCheck(S({body:'see attached',nAttach:1})))",
        "attach_ack": "()=>keys(guardCheck(S({body:'see attached',ack:{subject:false,attach:true,fanout:0}})))",
        "reply_weak_word": "()=>keys(guardCheck(S({mode:'reply',subject:'Re: x',subject0:'Re: x',body:'the attachment you sent'})))",
        "fan6": "()=>guardCheck(S({mode:'replyall',to:ppl(1),cc:ppl(6).slice(1)})).find(x=>x.key==='fanout')",
        "fan5": "()=>keys(guardCheck(S({mode:'replyall',to:ppl(1),cc:ppl(5).slice(1)})))",
        "fan_own_excluded": "()=>keys(guardCheck(S({mode:'replyall',to:ppl(1),cc:[...ppl(5).slice(1),'ada@example.test']})))",
        "fan_dupes_once": "()=>keys(guardCheck(S({mode:'replyall',to:ppl(3),cc:ppl(5)})))",
        "fan_reply_mode": "()=>keys(guardCheck(S({mode:'reply',to:ppl(4),cc:ppl(10).slice(4)})))",
        "fan_ack_8_n7": "()=>keys(guardCheck(S({mode:'replyall',to:ppl(1),cc:ppl(7).slice(1),ack:{subject:false,attach:false,fanout:8}})))",
        "fan_ack_8_n9": "()=>keys(guardCheck(S({mode:'replyall',to:ppl(1),cc:ppl(9).slice(1),ack:{subject:false,attach:false,fanout:8}})))",
        "fan_removable": "()=>guardCheck(S({mode:'replyall',to:['mira@example.test'],"
                         "cc:[...ppl(4),'own1@example.test','own2@example.test'],"
                         "autoCc:[...ppl(6),'ada@example.test']})).find(x=>x.key==='fanout').removable",
        "fan_none_removable": "()=>guardCheck(S({mode:'replyall',to:ppl(1),cc:ppl(7).slice(1)})).find(x=>x.key==='fanout').removable",
        "soft_alone": "()=>{ const w=guardCheck(S({fresh:['new@example.test']})); return [keys(w), guardBlocks(w)]; }",
        "soft_with_subject": "()=>guardBlocks(guardCheck(S({subject:'',fresh:['new@example.test']})))",
        "list_0": "()=>guardList([])",
        "list_1": "()=>guardList(['a'])",
        "list_2": "()=>guardList(['a','b'])",
        "list_3": "()=>guardList(['a','b','c'])",
        "list_4": "()=>guardList(['a','b','c','d'])",
    })
    return c


def _slow_cases():
    return {f"slow{i}": (f"()=>{{ const b={t}, t0=performance.now(); for(const mode of ['new','reply']){{ "
                         f"guardCheck(S({{mode,body:b}})); guardCheck(S({{mode,subject:b}})); }} "
                         f"return performance.now()-t0; }}") for i, t in enumerate(SLOW)}


def _run(tmp_path_factory, cases, stop):
    """Run `cases` against the block, in one node process of their own."""
    assert shutil.which("node"), "needs node, like just check-frontend"
    page = (ROOT / "web" / "assets" / "legacy" / "pure.js").read_text(encoding="utf-8")
    block = page[page.index(START) + len(START):page.index(END)]
    table = "const CASES={" + ",".join(f"{json.dumps(k)}:{v}" for k, v in cases.items()) + "};"
    path = tmp_path_factory.mktemp("send-guard") / "guard_test.js"
    path.write_text(block + "\n" + table + "\nconst STOP=" + stop + ";\n" + HARNESS, encoding="utf-8")
    out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def r(tmp_path_factory):
    """What each case answered, from one run of the block in node."""
    results = _run(tmp_path_factory, _cases(), "()=>false")

    def answer(name):
        v = results[name]
        assert not (isinstance(v, str) and v.startswith("THROW")), (name, v)
        return v
    return answer


@pytest.fixture(scope="module")
def slow(tmp_path_factory):
    return _run(tmp_path_factory, _slow_cases(), f"ms=>ms>={SLOW_MS}")


@pytest.mark.parametrize("i", range(len(STRONG)), ids=STRONG)
def test_strong_words_fire_in_every_mode(i, r):
    strict, weak = r(f"strong{i}")
    assert strict and weak, (strict, weak)


@pytest.mark.parametrize("i", range(len(WEAK)), ids=WEAK)
def test_weak_words_fire_only_where_allowed(i, r):
    strict, weak = r(f"weak{i}")
    assert strict == "" and weak, (strict, weak)


@pytest.mark.parametrize("i", range(len(NEVER)), ids=NEVER)
def test_lookalikes_links_and_addresses_never_fire(i, r):
    assert r(f"never{i}") == ""


@pytest.mark.parametrize("i", range(len(REPLY_NEVER)), ids=REPLY_NEVER)
def test_a_reply_about_their_file_never_warns(i, r):
    word, keys = r(f"reply_never{i}")
    assert word == "" and keys == [], (word, keys)


@pytest.mark.parametrize("i", range(len(SLOW)), ids=SLOW)
def test_long_unbroken_text_checks_fast(i, slow):
    ms = slow.get(f"slow{i}")           # none: the run stopped at an earlier one that was too slow
    assert isinstance(ms, (int, float)) and ms < SLOW_MS, ms   # was 2-5 s at the 50k cap


def test_the_word_is_echoed_as_typed(r):
    assert r("echo") == "en pièce jointe"


@pytest.mark.parametrize("case, want", [
    ("typed_sig", "see attached"),
    ("typed_keeps_above_sig", "see attached"),
    ("typed_no_sigblock", "Hi there"),
    ("typed_attribution", "Thanks"),
    ("typed_outlook", "Noted"),
    ("typed_quoted", "ok, thanks"),
    ("disclaimer", []),
])
def test_typed_text_leaves_out_signature_and_quote(case, want, r):
    assert r(case) == want


@pytest.mark.parametrize("case, want", [("typed_sig_edited", "Hi there"), ("typed_forward", "FYI")])
def test_typed_text_leaves_out_signature_and_quote_and_their_blank_lines(case, want, r):
    assert r(case).strip() == want


@pytest.mark.parametrize("case, want", [
    ("typed_ps", "Hi Bo,\nnotes below.\n\n\n\nP.S. I attached the deck."),
    ("typed_sig_mid_line", "See x-- \nAda Lovelace"),
    ("ps_check", ["attach"]),
])
def test_text_on_either_side_of_your_signature_is_yours(case, want, r):
    assert r(case) == want


def test_text_below_your_signature_is_yours(r):
    assert r("typed_below_sig").strip() == "Hi Bo, the deck is attached."


@pytest.mark.parametrize("case, want", [
    ("subj_empty_reply", True),
    ("subj_blank_new", True),
    ("subj_re_reply", False),
    ("subj_fwd_forward", False),
    ("subj_re_new", True),
    ("subj_ack", []),
])
def test_empty_subject(case, want, r):
    assert r(case) == want and type(r(case)) is type(want)


@pytest.mark.parametrize("case, want", [("subj_inherited", []), ("subj_edited", ["attach"]), ("subj_new", ["attach"])])
def test_an_inherited_subject_is_not_your_wording(case, want, r):
    assert r(case) == want


def test_a_file_still_reading_is_the_only_line(r):
    assert r("reading") == [{"key": "reading", "block": "hard", "name": "a.pdf", "more": 1}]


@pytest.mark.parametrize("case, want", [("late_soon", ["late"]), ("late_hour", []), ("late_is_hard", True)])
def test_a_passed_send_time(case, want, r):
    assert r(case) == want and type(r(case)) is type(want)


@pytest.mark.parametrize("case, want", [("fwd_files", []), ("fwd_no_files", ["attach"]), ("has_attach", []),
                                        ("attach_ack", []), ("reply_weak_word", [])])
def test_what_counts_as_attached(case, want, r):
    assert r(case) == want


@pytest.mark.parametrize("case, want", [
    ("fan6", {"key": "fanout", "block": "confirm", "n": 6, "removable": 0}),
    ("fan5", []),
    ("fan_own_excluded", []),
    ("fan_dupes_once", []),
    ("fan_reply_mode", []),
])
def test_reply_all_size(case, want, r):
    assert r(case) == want


@pytest.mark.parametrize("case, want", [("fan_ack_8_n7", []), ("fan_ack_8_n9", ["fanout"])])
def test_a_wave_through_covers_up_to_that_many_people(case, want, r):
    assert r(case) == want


@pytest.mark.parametrize("case, want", [("fan_removable", 4), ("fan_none_removable", 0)])
def test_removable_counts_only_people_still_copied_in(case, want, r):
    assert r(case) == want


def test_new_to_you_never_stops_a_send_alone(r):
    assert r("soft_alone") == [["new"], False]
    assert r("soft_with_subject") is True


@pytest.mark.parametrize("case, want", [("list_0", ""), ("list_1", "a"), ("list_2", "a and b"),
                                        ("list_3", "a, b and c"), ("list_4", "a, b and 2 more")])
def test_names_list(case, want, r):
    assert r(case) == want


@pytest.mark.parametrize("i", range(len(LINES)), ids=[line for line, _, _ in LINES])
def test_exact_wording_of_every_line(i, r):
    _, text, fix = LINES[i]
    got = r(f"line{i}")
    assert (got["text"], got["fix"]) == (text, fix)
    assert "!" not in got["text"]
