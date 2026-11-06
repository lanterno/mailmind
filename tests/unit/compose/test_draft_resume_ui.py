"""Reopening a saved draft in the composer (web/src/features/compose/draftResume.ts, as pure.js has it: the block
between /* draft-resume:pure:start */ and /* draft-resume:pure:end */): the composer options built from
/api/draft/resume's answer, the `resume` fields every save and send carries, when AI Draft is
hidden, the signature a reopened text still ends with, and the wording of the notes. The block
is pure JavaScript with no DOM, so this runs it once in node, like test_send_guard.py. All
addresses are fictional."""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
START, END = "/* draft-resume:pure:start */", "/* draft-resume:pure:end */"
TAIL = "\n\nOn Mon, 02 Feb 2026, Bo Park <bo@example.test> wrote:\n> Shall we meet Saturday?\n"
D = {"ok": True, "draft_id": 12, "mode": "reply", "account": "ada@example.test", "subject": "Re: Plans",
     "to": ["Bo Park <bo@example.test>"], "cc": [], "bcc": ["dee@example.test"], "text": "Saturday works.",
     "tail_text": TAIL, "in_reply_to": "<orig1@example.test>", "references": "<a@example.test> <orig1@example.test>",
     "orig_pk": 7, "participants": ["bo@example.test"], "files": [], "split": "exact",
     "notes": {"inline": 0, "bcc_dropped": 0, "plain": False}}

BCC_NOTE = "The Bcc wasn’t restored, because this draft changed since MailMind saved it."
PLAIN_NOTE = "This draft opens as plain text, so its formatting won’t be kept when you save or send it."

# (a call, what it answers)
FIELDS_NONE = [("resumeFields(null)", {}), ("resumeFields(undefined)", {})]
HIDES_AI = [("resumeHidesAi(null)", False), ("resumeHidesAi({resume:null, quote:null})", False),
            ("resumeHidesAi({resume:{}, quote:{text:'q'}})", False), ("resumeHidesAi({resume:{}, quote:null})", True)]
SIGS = [(r'resumeSig("Saturday works.\n\n-- \nAda", "Ada")', "\n\n-- \nAda"),
        (r'resumeSig("Saturday works.\n\n-- \nAda L.", "Ada")', ""),
        (r'resumeSig("Hi\n\n-- \nAda\nP.S. later", "Ada")', ""),
        ('resumeSig("Hi", "")', ""),
        ('resumeSig("", "Ada")', ""),
        ('resumeSig(null, "Ada")', ""),
        (r'resumeSig("Hi\n-- \nAda", "Ada")', "")]
NOTES = [("resumeNotice(null)", ""),
         ("resumeNotice({inline:0, bcc_dropped:0, plain:false})", ""),
         ("resumeNotice({inline:1})", "1 picture in the text is now an attachment."),
         ("resumeNotice({inline:3})", "3 pictures in the text are now attachments."),
         ("resumeNotice({bcc_dropped:2})", BCC_NOTE),
         ("resumeNotice({plain:true})", PLAIN_NOTE),
         ("resumeNotice({inline:2, bcc_dropped:1, plain:true})",
          f"2 pictures in the text are now attachments. {BCC_NOTE} {PLAIN_NOTE}")]

CASES = {
    "exact": f"()=>resumeOpts({json.dumps(D)})",
    "whole": "()=>resumeOpts(" + json.dumps({**D, "tail_text": "", "orig_pk": None, "split": "whole"}) + ")",
    "forward": "()=>resumeOpts(" + json.dumps({**D, "mode": "forward", "tail_text": "\n\n---------- Forwarded message "
                                               "----------\nFrom: Bo", "in_reply_to": "", "references": "",
                                               "orig_pk": None, "participants": []}) + ")",
    "new": "()=>resumeOpts({draft_id:3, account:'ada@example.test'})",
    "fields": "()=>resumeFields({in_reply_to:'<x@example.test>', references:null, tail_text:'q', extra:1})",
    **{call: f"()=>{call}" for call, _ in FIELDS_NONE + HIDES_AI + SIGS + NOTES},
}

HARNESS = r"""
const R={};
for(const [name,fn] of Object.entries(CASES)){ try{ R[name]=fn(); }catch(e){ R[name]="THROW "+e; } }
process.stdout.write(JSON.stringify(R));
"""


@pytest.fixture(scope="module")
def r(tmp_path_factory):
    """What each case answered, from one run of the block in node."""
    assert shutil.which("node"), "needs node, like just check-frontend"
    page = (ROOT / "web" / "assets" / "legacy" / "pure.js").read_text(encoding="utf-8")
    assert START in page and END in page, "the draft-resume:pure markers are missing"
    cases = "const CASES={" + ",".join(f"{json.dumps(k)}:{v}" for k, v in CASES.items()) + "};"
    path = tmp_path_factory.mktemp("draft-resume") / "resume_test.js"
    path.write_text(page[page.index(START) + len(START):page.index(END)] + "\n" + cases + "\n" + HARNESS, encoding="utf-8")
    out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    results = json.loads(out.stdout)

    def answer(name):
        v = results[name]
        assert not (isinstance(v, str) and v.startswith("THROW")), (name, v)
        return v
    return answer


def test_a_reopened_reply_opens_like_a_fresh_one(r):
    o = r("exact")
    assert (o["mode"], o["from"], o["draftId"], o["origPk"], o["noSig"]) == ("reply", "ada@example.test", 12, 7, True), o
    assert (o["to"], o["cc"], o["bcc"]) == (["Bo Park <bo@example.test>"], [], ["dee@example.test"]), o
    assert o["subject"] == o["subject0"] == "Re: Plans" and o["participants"] == ["bo@example.test"], o
    assert o["quote"] == {"html": None, "text": TAIL.lstrip("\n")}, o["quote"]   # the preview drops the blank lines
    assert o["fwdAttach"] == 0, o
    assert o["resume"] == {"in_reply_to": "<orig1@example.test>", "references": "<a@example.test> <orig1@example.test>",
                           "tail_text": TAIL}, o["resume"]


def test_a_whole_draft_has_no_quote_to_fold(r):
    o = r("whole")
    assert o["quote"] is None and o["origPk"] is None and o["resume"]["tail_text"] == "", o


def test_a_forward_keeps_its_section_and_no_thread(r):
    o = r("forward")
    assert o["mode"] == "forward" and o["quote"]["text"].startswith("---------- Forwarded message"), o
    assert o["resume"]["in_reply_to"] == "" and o["origPk"] is None, o


def test_defaults_for_a_bare_answer(r):
    o = r("new")
    assert (o["mode"], o["to"], o["subject"], o["quote"], o["origPk"]) == ("new", [], "", None, None), o
    assert o["resume"] == {"in_reply_to": "", "references": "", "tail_text": ""}, o


@pytest.mark.parametrize("call, want", FIELDS_NONE)
def test_resume_fields_of_no_draft_are_none(call, want, r):
    assert r(call) == want


def test_resume_fields_on_every_save_and_send(r):
    assert r("fields") == {"resume": {"in_reply_to": "<x@example.test>", "references": "", "tail_text": "q"}}


@pytest.mark.parametrize("call, want", HIDES_AI)
def test_ai_draft_hidden_only_when_the_quote_is_in_the_box(call, want, r):
    assert r(call) is want


@pytest.mark.parametrize("call, want", SIGS)
def test_the_signature_a_reopened_text_ends_with(call, want, r):
    assert r(call) == want


@pytest.mark.parametrize("call, want", NOTES)
def test_notes_wording(call, want, r):
    assert r(call) == want
    assert "!" not in r(call)
