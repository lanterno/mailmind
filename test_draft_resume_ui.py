"""Reopening a saved draft in the composer (dashboard.html, the block between
/* draft-resume:pure:start */ and /* draft-resume:pure:end */): the composer options built from
/api/draft/resume's answer, the `resume` fields every save and send carries, when AI Draft is
hidden, the signature a reopened text still ends with, and the wording of the notes. The block
is pure JavaScript with no DOM, so this runs it once in node, like test_send_guard.py. All
addresses are fictional.
Run: python3 test_draft_resume_ui.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
START, END = "/* draft-resume:pure:start */", "/* draft-resume:pure:end */"
TAIL = "\n\nOn Mon, 02 Feb 2026, Bo Park <bo@example.test> wrote:\n> Shall we meet Saturday?\n"
D = {"ok": True, "draft_id": 12, "mode": "reply", "account": "ada@example.test", "subject": "Re: Plans",
     "to": ["Bo Park <bo@example.test>"], "cc": [], "bcc": ["dee@example.test"], "text": "Saturday works.",
     "tail_text": TAIL, "in_reply_to": "<orig1@example.test>", "references": "<a@example.test> <orig1@example.test>",
     "orig_pk": 7, "participants": ["bo@example.test"], "files": [], "split": "exact",
     "notes": {"inline": 0, "bcc_dropped": 0, "plain": False}}

CASES = {
    "exact": f"()=>resumeOpts({json.dumps(D)})",
    "whole": "()=>resumeOpts(" + json.dumps({**D, "tail_text": "", "orig_pk": None, "split": "whole"}) + ")",
    "forward": "()=>resumeOpts(" + json.dumps({**D, "mode": "forward", "tail_text": "\n\n---------- Forwarded message "
                                               "----------\nFrom: Bo", "in_reply_to": "", "references": "",
                                               "orig_pk": None, "participants": []}) + ")",
    "new": "()=>resumeOpts({draft_id:3, account:'ada@example.test'})",
    "fields_none": "()=>[resumeFields(null), resumeFields(undefined)]",
    "fields": "()=>resumeFields({in_reply_to:'<x@example.test>', references:null, tail_text:'q', extra:1})",
    "hides_ai": "()=>[resumeHidesAi(null), resumeHidesAi({resume:null, quote:null}),"
                " resumeHidesAi({resume:{}, quote:{text:'q'}}), resumeHidesAi({resume:{}, quote:null})]",
    "sig": """()=>[resumeSig("Saturday works.\\n\\n-- \\nAda", "Ada"), resumeSig("Saturday works.\\n\\n-- \\nAda L.", "Ada"),
                resumeSig("Hi\\n\\n-- \\nAda\\nP.S. later", "Ada"), resumeSig("Hi", ""), resumeSig("", "Ada"),
                resumeSig(null, "Ada"), resumeSig("Hi\\n-- \\nAda", "Ada")]""",
    "notes": """()=>[resumeNotice(null), resumeNotice({inline:0, bcc_dropped:0, plain:false}),
                resumeNotice({inline:1}), resumeNotice({inline:3}), resumeNotice({bcc_dropped:2}),
                resumeNotice({plain:true}), resumeNotice({inline:2, bcc_dropped:1, plain:true})]""",
}

HARNESS = r"""
const R={};
for(const [name,fn] of Object.entries(CASES)){ try{ R[name]=fn(); }catch(e){ R[name]="THROW "+e; } }
process.stdout.write(JSON.stringify(R));
"""

_RESULTS = None


def _run():
    global _RESULTS
    if _RESULTS is not None:
        return _RESULTS
    with open(os.path.join(HERE, "dashboard.html"), encoding="utf-8") as f:
        html = f.read()
    assert START in html and END in html, "the draft-resume:pure markers are missing"
    a, b = html.index(START), html.index(END)
    cases = "const CASES={" + ",".join(f"{json.dumps(k)}:{v}" for k, v in CASES.items()) + "};"
    d = tempfile.mkdtemp()
    path = os.path.join(d, "resume_test.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html[a + len(START):b] + "\n" + cases + "\n" + HARNESS)
    try:
        out = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    assert out.returncode == 0, out.stderr
    _RESULTS = json.loads(out.stdout)
    return _RESULTS


def _r(name):
    v = _run()[name]
    assert not (isinstance(v, str) and v.startswith("THROW")), (name, v)
    return v


def test_a_reopened_reply_opens_like_a_fresh_one():
    o = _r("exact")
    assert (o["mode"], o["from"], o["draftId"], o["origPk"], o["noSig"]) == ("reply", "ada@example.test", 12, 7, True), o
    assert (o["to"], o["cc"], o["bcc"]) == (["Bo Park <bo@example.test>"], [], ["dee@example.test"]), o
    assert o["subject"] == o["subject0"] == "Re: Plans" and o["participants"] == ["bo@example.test"], o
    assert o["quote"] == {"html": None, "text": TAIL.lstrip("\n")}, o["quote"]   # the preview drops the blank lines
    assert o["fwdAttach"] == 0, o
    assert o["resume"] == {"in_reply_to": "<orig1@example.test>", "references": "<a@example.test> <orig1@example.test>",
                           "tail_text": TAIL}, o["resume"]


def test_a_whole_draft_has_no_quote_to_fold():
    o = _r("whole")
    assert o["quote"] is None and o["origPk"] is None and o["resume"]["tail_text"] == "", o


def test_a_forward_keeps_its_section_and_no_thread():
    o = _r("forward")
    assert o["mode"] == "forward" and o["quote"]["text"].startswith("---------- Forwarded message"), o
    assert o["resume"]["in_reply_to"] == "" and o["origPk"] is None, o


def test_defaults_for_a_bare_answer():
    o = _r("new")
    assert (o["mode"], o["to"], o["subject"], o["quote"], o["origPk"]) == ("new", [], "", None, None), o
    assert o["resume"] == {"in_reply_to": "", "references": "", "tail_text": ""}, o


def test_resume_fields_on_every_save_and_send():
    assert _r("fields_none") == [{}, {}], _r("fields_none")
    assert _r("fields") == {"resume": {"in_reply_to": "<x@example.test>", "references": "", "tail_text": "q"}}, _r("fields")


def test_ai_draft_hidden_only_when_the_quote_is_in_the_box():
    assert _r("hides_ai") == [False, False, False, True], _r("hides_ai")


def test_the_signature_a_reopened_text_ends_with():
    assert _r("sig") == ["\n\n-- \nAda", "", "", "", "", "", ""], _r("sig")


def test_notes_wording():
    want = ["", "", "1 picture in the text is now an attachment.", "3 pictures in the text are now attachments.",
            "The Bcc wasn’t restored, because this draft changed since MailMind saved it.",
            "This draft opens as plain text, so its formatting won’t be kept when you save or send it.",
            "2 pictures in the text are now attachments. The Bcc wasn’t restored, because this draft changed "
            "since MailMind saved it. This draft opens as plain text, so its formatting won’t be kept when you "
            "save or send it."]
    got = _r("notes")
    assert got == want, got
    assert not any("!" in t for t in got)


if __name__ == "__main__":
    if not shutil.which("node"):
        print("needs node, like just check-frontend")
        sys.exit(1)
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", fn.__name__, repr(e))
        except Exception as e:
            failed += 1
            print("ER:U", fn.__name__, repr(e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
