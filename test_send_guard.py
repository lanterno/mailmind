"""The composer's send-time checks (dashboard.html, the block between /* send-guard:pure:start */
and /* send-guard:pure:end */): attachment wording in eight languages, what counts as typed text,
empty subjects, reply-all size, speed on long unbroken text, acknowledgements, and the exact wording of every line. The block
is pure JavaScript with no DOM, so this runs it once in node, like `just check-frontend` does.
All addresses are fictional.
Run: python3 test_send_guard.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
START, END = "/* send-guard:pure:start */", "/* send-guard:pure:end */"

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
STRONG = ["Please see attached.", "Enclosed is the form.", "I'm enclosing the invoice.", "Ci-joint le devis.",
          "Voici le devis en pièce jointe.", "le devis en piece jointe", "Je vous joins le devis.",
          "Veuillez trouver la pièce jointe.", "PJ : devis", "Vous trouverez en PJ le devis.", "Voir PJ.",
          "Te mando adjunto el informe.", "Te adjunto el informe.", "Adjunto el informe.",
          "Hola Ana, adjunto la factura.", "Va el archivo adjunto.", "Te envío anexa la factura.",
          "Segue em anexo o relatório.", "Segue anexa a planilha.", "Seguem anexos os documentos.",
          "Anbei die Rechnung.", "Die Datei ist im Anhang.", "Das Formular ist beigefügt.", "Ich habe es angehängt.",
          "Beiliegend erhalten Sie die Rechnung.", "Anliegend sende ich Ihnen den Vertrag.",
          "In der Anlage finden Sie das Angebot.", "Als Anlage sende ich den Vertrag.",
          "Trovi il file in allegato.", "Trovi il file allegato.", "Ecco la fattura allegata.", "Allego il contratto.",
          "Zie het bestand in de bijlage.", "Het rapport is bijgevoegd.", "Bijgaand de offerte.",
          "مرفق التقرير", "تجد التقرير بالمرفق", "وبالمرفق التقرير", "في المرفق التقرير", "الملف المرفق",
          "ومرفقات الملف", "أرفقت الملف", "نرفقها لكم", "تم إرفاق الملف"]
# Weak wording names a file without presenting it: in a reply it's usually the other person's.
WEAK = ["The attachment is big.", "Attachments below.", "My CV, as promised.", "See report.pdf for numbers.",
        "See Q3-report_v2.pdf", "Voici la facture jointe.", "Voir la PJ.", "Les pièces jointes sont lourdes.",
        "Reenvío el adjunto.", "Mando el anexo.", "Anhang folgt.", "In der Anlage ist ein Fehler.",
        "Vedi l'allegato.", "Bijlage volgt.", "المرفقات كبيرة"]
NEVER = ["We joined forces.", "A joint venture.", "The attaché said hello.", "My resume is online.",
         "cv and pj in lower case", "Unattached luggage.", "اكتب برفق", "مرافق الفندق",
         "Bug tracker: https://x.example.test/attachment.cgi?id=4", "(see https://x.example.test/attached)",
         "Write to attachments@example.test.", "Thanks PJ, see you Monday.", "O contrato PJ está pronto?",
         "Hablé con la directora adjunta.", "Falei com o diretor adjunto.",
         "Hablé con el director adjunto la semana pasada.", "Gold als Anlage ist sicher.",
         "Das Kleid ist eng anliegend."]
# Replies about the file they sent: weak at most, so a reply never warns.
REPLY_NEVER = ["Thanks, I can't open the attachment.", "Merci pour la pièce jointe, c'est parfait.",
               "Merci pour la PJ.", "La PJ a ouvert une enquête.", "Je l'ai jointe ce matin au téléphone.",
               "No puedo abrir el adjunto.", "Non riesco ad aprire l'allegato.", "Grazie, ho ricevuto gli allegati.",
               "Grazie per i tuoi allegati.", "شكرا على المرفقات"]
# Long runs with no spaces: a pasted base64 blob, minified JSON, a line of dashes. They run in a
# node process of their own, which stops at the first that takes SLOW_MS or more.
SLOW_MS = 250
SLOW = ["'QUJD'.repeat(12500)", "'-'.repeat(50000)", "'_'.repeat(50000)", "'a-'.repeat(25000)", "'.'.repeat(50000)",
        "'@'.repeat(50000)", "'ال'.repeat(25000)", "', '.repeat(25000)", "'a'+' '.repeat(49998)+'b'",
        "JSON.stringify(Array.from({length:4000},(_,i)=>({k:i,v:'x'+i}))).slice(0,50000)"]


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
        "list": "()=>[guardList([]),guardList(['a']),guardList(['a','b']),guardList(['a','b','c']),guardList(['a','b','c','d'])]",
        "lines": """()=>[
            guardLine({key:'reading',name:'a.pdf',more:0}), guardLine({key:'reading',name:'a.pdf',more:2}),
            guardLine({key:'late',whenText:'today 6:00 PM'}), guardLine({key:'subject'}),
            guardLine({key:'attach',word:'pièce jointe'}), guardLine({key:'fanout',n:7,removable:6}),
            guardLine({key:'fanout',n:7,removable:0}), guardLine({key:'new',addrs:['a@example.test','b@example.test']}),
            guardLine({key:'unchecked'}), guardLine({key:'norcpt'}), guardLine({key:'bad',v:'bo@',more:0}),
            guardLine({key:'bad',v:'bo@',more:1}), guardLine({key:'bad',v:'bo@',more:2})]""",
    })
    return c


def _slow_cases():
    return {f"slow{i}": (f"()=>{{ const b={t}, t0=performance.now(); for(const mode of ['new','reply']){{ "
                         f"guardCheck(S({{mode,body:b}})); guardCheck(S({{mode,subject:b}})); }} "
                         f"return performance.now()-t0; }}") for i, t in enumerate(SLOW)}


_RESULTS = {}   # group → results, or the error that run raised (so a broken block fails fast, once)
_GROUPS = {"main": (_cases, "()=>false"), "slow": (_slow_cases, f"ms=>ms>={SLOW_MS}")}


def _run(group="main"):
    if group in _RESULTS:
        if isinstance(_RESULTS[group], Exception):
            raise _RESULTS[group]
        return _RESULTS[group]
    make, stop = _GROUPS[group]
    try:
        with open(os.path.join(HERE, "dashboard.html"), encoding="utf-8") as f:
            html = f.read()
        a, b = html.index(START), html.index(END)
        block = html[a + len(START):b]
        cases = "const CASES={" + ",".join(f"{json.dumps(k)}:{v}" for k, v in make().items()) + "};"
        d = tempfile.mkdtemp()
        path = os.path.join(d, "guard_test.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(block + "\n" + cases + "\nconst STOP=" + stop + ";\n" + HARNESS)
        try:
            out = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        assert out.returncode == 0, out.stderr
        _RESULTS[group] = json.loads(out.stdout)
    except Exception as e:
        _RESULTS[group] = e
        raise
    return _RESULTS[group]


def _r(name):
    v = _run()[name]
    assert not (isinstance(v, str) and v.startswith("THROW")), (name, v)
    return v


def test_strong_words_fire_in_every_mode():
    for i, t in enumerate(STRONG):
        strict, weak = _r(f"strong{i}")
        assert strict and weak, (t, strict, weak)


def test_weak_words_fire_only_where_allowed():
    for i, t in enumerate(WEAK):
        strict, weak = _r(f"weak{i}")
        assert strict == "" and weak, (t, strict, weak)


def test_lookalikes_links_and_addresses_never_fire():
    for i, t in enumerate(NEVER):
        assert _r(f"never{i}") == "", (t, _r(f"never{i}"))


def test_a_reply_about_their_file_never_warns():
    for i, t in enumerate(REPLY_NEVER):
        word, keys = _r(f"reply_never{i}")
        assert word == "" and keys == [], (t, word, keys)


def test_long_unbroken_text_checks_fast():
    res = _run("slow")
    for i, t in enumerate(SLOW):
        ms = res.get(f"slow{i}")
        assert isinstance(ms, (int, float)) and ms < SLOW_MS, (t, ms)   # was 2-5 s at the 50k cap


def test_the_word_is_echoed_as_typed():
    assert _r("echo") == "en pièce jointe", _r("echo")


def test_typed_text_leaves_out_signature_and_quote():
    assert _r("typed_sig") == "see attached", _r("typed_sig")
    assert _r("typed_keeps_above_sig") == "see attached"
    assert _r("typed_sig_edited").strip() == "Hi there", _r("typed_sig_edited")
    assert _r("typed_no_sigblock") == "Hi there", _r("typed_no_sigblock")
    assert _r("typed_attribution") == "Thanks", _r("typed_attribution")
    assert _r("typed_forward").strip() == "FYI", _r("typed_forward")
    assert _r("typed_outlook") == "Noted", _r("typed_outlook")
    assert _r("typed_quoted") == "ok, thanks", _r("typed_quoted")
    assert _r("disclaimer") == [], _r("disclaimer")


def test_text_on_either_side_of_your_signature_is_yours():
    assert _r("typed_ps") == "Hi Bo,\nnotes below.\n\n\n\nP.S. I attached the deck.", _r("typed_ps")
    assert _r("typed_below_sig").strip() == "Hi Bo, the deck is attached.", _r("typed_below_sig")
    assert _r("typed_sig_mid_line") == "See x-- \nAda Lovelace", _r("typed_sig_mid_line")
    assert _r("ps_check") == ["attach"], _r("ps_check")


def test_empty_subject():
    assert _r("subj_empty_reply") is True
    assert _r("subj_blank_new") is True
    assert _r("subj_re_reply") is False
    assert _r("subj_fwd_forward") is False
    assert _r("subj_re_new") is True
    assert _r("subj_ack") == [], _r("subj_ack")


def test_an_inherited_subject_is_not_your_wording():
    assert _r("subj_inherited") == [], _r("subj_inherited")
    assert _r("subj_edited") == ["attach"], _r("subj_edited")
    assert _r("subj_new") == ["attach"], _r("subj_new")


def test_a_file_still_reading_is_the_only_line():
    assert _r("reading") == [{"key": "reading", "block": "hard", "name": "a.pdf", "more": 1}], _r("reading")


def test_a_passed_send_time():
    assert _r("late_soon") == ["late"], _r("late_soon")
    assert _r("late_hour") == [], _r("late_hour")
    assert _r("late_is_hard") is True


def test_what_counts_as_attached():
    assert _r("fwd_files") == [], _r("fwd_files")
    assert _r("fwd_no_files") == ["attach"], _r("fwd_no_files")
    assert _r("has_attach") == [], _r("has_attach")
    assert _r("attach_ack") == [], _r("attach_ack")
    assert _r("reply_weak_word") == [], _r("reply_weak_word")


def test_reply_all_size():
    assert _r("fan6") == {"key": "fanout", "block": "confirm", "n": 6, "removable": 0}, _r("fan6")
    assert _r("fan5") == [], _r("fan5")
    assert _r("fan_own_excluded") == [], _r("fan_own_excluded")
    assert _r("fan_dupes_once") == [], _r("fan_dupes_once")
    assert _r("fan_reply_mode") == [], _r("fan_reply_mode")


def test_a_wave_through_covers_up_to_that_many_people():
    assert _r("fan_ack_8_n7") == [], _r("fan_ack_8_n7")
    assert _r("fan_ack_8_n9") == ["fanout"], _r("fan_ack_8_n9")


def test_removable_counts_only_people_still_copied_in():
    assert _r("fan_removable") == 4, _r("fan_removable")
    assert _r("fan_none_removable") == 0, _r("fan_none_removable")


def test_new_to_you_never_stops_a_send_alone():
    assert _r("soft_alone") == [["new"], False], _r("soft_alone")
    assert _r("soft_with_subject") is True


def test_names_list():
    assert _r("list") == ["", "a", "a and b", "a, b and c", "a, b and 2 more"], _r("list")


def test_exact_wording_of_every_line():
    got = [(x["text"], x["fix"]) for x in _r("lines")]
    want = [
        ("Still adding a.pdf. Send again once it’s added.", None),
        ("Still adding a.pdf and 2 more. Send again once they’re added.", None),
        ("The time you picked, today 6:00 PM, has passed.", {"kind": "time", "label": "Pick a new time"}),
        ("There’s no subject.", {"kind": "subject", "label": "Add a subject"}),
        ("You wrote “pièce jointe”, but nothing is attached.", {"kind": "attach", "label": "Attach a file"}),
        ("This reply goes to 7 people.", {"kind": "trim", "label": "Remove the 6 copied from the original"}),
        ("This reply goes to 7 people.", {"kind": "review", "label": "Review recipients"}),
        ("New to you: a@example.test and b@example.test.", None),
        ("The checks before sending couldn’t run.", None),
        ("Add at least one recipient.", {"kind": "to", "label": "Add one"}),
        ("“bo@” isn’t a valid address.", {"kind": "bad", "label": "Edit it"}),
        ("“bo@” isn’t a valid address. 1 more needs fixing too.", {"kind": "bad", "label": "Edit it"}),
        ("“bo@” isn’t a valid address. 2 more need fixing too.", {"kind": "bad", "label": "Edit it"}),
    ]
    for g, w in zip(got, want):
        assert g == w, (g, w)
    assert len(got) == len(want)
    assert not any("!" in t for t, _ in got)


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
