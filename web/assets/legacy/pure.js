// Written by `just gen-web` from web/src/compose: edit those.
"use strict";
/* draft-resume:pure:start */
// Reopening a saved draft: what the composer opens with and sends back. Pure, like sendGuard.ts, and written
// into assets/legacy/pure.js the same way.
function resumeOpts(d              )              { const t=d.tail_text||"";   // composeOpen options for a draft the server reopened
  return {mode:d.mode||"new", from:d.account, to:(d.to||[]).slice(), cc:(d.cc||[]).slice(), bcc:(d.bcc||[]).slice(),
    subject:d.subject||"", subject0:d.subject||"", origPk:d.orig_pk!=null?d.orig_pk:null, draftId:d.draft_id,
    participants:(d.participants||[]).slice(), quote:t?{html:null,text:t.replace(/^\n+/,"")}:null, fwdAttach:0, noSig:true,
    resume:{in_reply_to:d.in_reply_to||"", references:d.references||"", tail_text:t}}; }
function resumeFields(r                            )                       { return r?{resume:{in_reply_to:r.in_reply_to||"", references:r.references||"", tail_text:r.tail_text||""}}:{}; }
function resumeHidesAi(cmp                                                )        { return !!(cmp&&cmp.resume&&!cmp.quote); }   // the quote is in the box: an AI draft would replace it
function resumeSig(text        , sig       )       { const blk=sig?"\n\n-- \n"+sig:"";   // the signature block a reopened text still ends with, or ""
  return blk&&String(text||"").endsWith(blk)?blk:""; }
function resumeNotice(n                           )       { n=n||{}; const o         =[];
  if((n.inline??0)>0)o.push(n.inline===1?"1 picture in the text is now an attachment.":`${n.inline} pictures in the text are now attachments.`);
  if((n.bcc_dropped??0)>0)o.push("The Bcc wasn’t restored, because this draft changed since MailMind saved it.");
  if(n.plain)o.push("This draft opens as plain text, so its formatting won’t be kept when you save or send it.");
  return o.join(" "); }
/* draft-resume:pure:end */
/* send-guard:pure:start */
// The composer's send-time checks: pure, with no DOM and no app state, so they run anywhere. `just gen-web`
// writes these, types erased, into assets/legacy/pure.js for the page's classic scripts.
// What a line can be written for: a check's warning, or what the composer itself finds before the checks.
const GUARD_FANOUT=5, GUARD_ARM_MS=700, GUARD_SCAN_MAX=50000;
// Sources are in folded form (NFD, marks removed, lowercase). Strong words present a file of your
// own ("attached", "ci-joint", "te adjunto"), so they fire in every mode. Weak ones are the bare
// nouns ("the attachment", "la pièce jointe", "l'allegato"), which in a reply are usually about the
// other person's file, so they fire only in a new message or a forward. A lookbehind sits behind a
// lookahead for its word, so it only runs where the word is.
const _GW_STRONG         =["attached","attaching","enclosed","enclosing",
  "en pieces? jointes?","ci[-\\s]?jointe?s?","en annexe","je (?:vous |te )?joins","nous (?:vous |te )?joignons",
  "(?:veuillez trouver|trouverez|trouveras) (?:en |la |les )?(?:pieces? jointes?|pj)",
  "(?:te|le|les|os) adjunt(?:o|amos)","adjuntamos","adjuntad[oa]s?",
  "(?=adjunt)(?<=(?:^|[\\n.!?:;,¡]) {0,3})adjunt(?:o|a|os|as)",                // "Adjunto el informe", not "directora adjunta"
  "(?:mando|envio|remito|paso|enviamos|mandamos|remitimos|va|van|encontraras|encontrara|encontrareis|encontraran) adjunt[oa]s?",
  "(?:archivos?|documentos?|ficheros?|informes?|facturas?|presupuestos?|contratos?|curriculum|fotos?|imagen(?:es)?) adjunt[oa]s?",
  "em anexo","(?:segue|seguem|envio|enviamos|mando|mandamos|encaminho|remeto|va|van) anex[oa]s?",
  "anexad[oa]s?","anexei","anexamos",
  "anbei","im anhang","beigefugt","angehangt","angefugt","beiliegend","(?=anliegend)(?<!eng |enger )anliegend",
  "(?:in der|als) anlage (?:finden|erhalten|sende|senden|schicke|schicken|ubersende|ubersenden)",   // not "Gold als Anlage"
  "in allegato","(?=allegat)(?<!['’]|gli |un |tuoi |suoi |vostri |loro )allegat[oaie]","allego","alleghiamo",   // "il file allegato", not "l'allegato"
  "bijgevoegd","bijgesloten","bijgaand","in de bijlage","als bijlage",
  "[وف]?[بل]?(?:مرفق|ارفق|نرفق|ارفاق)[\\u0621-\\u064A]{0,3}",                  // مرفق, أرفقت, نرفقها, إرفاق, with no article
  "[وف]?(?:ب|في )ال?مرفق[\\u0621-\\u064A]{0,2}",                             // بالمرفق, في المرفق: "in the attachment"
  "(?=المرفق)(?<=ال[\\u0621-\\u064A]{2,12} )المرفق(?:ة|ين|تين)?"];             // الملف المرفق: "the attached file"
const _GW_WEAK         =["attach","attachments?","enclosures?","cover letter",
  "pieces? jointes?","jointes?","lettres? de motivation",
  "(?:el|los|un|unos|del|al|tu|tus|su|sus|mi|mis|este|estos|ese|esos) adjuntos?","carta de presentacion",
  "anexos?","anexar","carta de apresentacao","anhange?","in der anlage","begleitschreiben",
  "allegat[oi]","lettera di accompagnamento","bijlagen?","sollicitatiebrief",
  "[وفبلك]{0,2}(?:ال|ل)مرفق[\\u0621-\\u064A]{0,3}"];                         // المرفقات: "the attachments"
const _gwRe=(a         )=>new RegExp("(^|[^\\p{L}\\p{N}])("+a.join("|")+")(?![\\p{L}\\p{N}])","u");
const _GW_S=_gwRe(_GW_STRONG), _GW_W=_gwRe(_GW_WEAK);
// A file name (weak). "_" and "-" can't start one mid-run, so a long run of them stays fast.
const _GW_FILE=/(^|[^\p{L}\p{N}_-])([\p{L}\p{N}_-]+\.(?:pdf|docx?|xlsx?|pptx?|odt|ods|odp|rtf|csv|zip))(?![\p{L}\p{N}])/u;
// Case-sensitive, on unfolded text. A bare "PJ" is also a nickname, "pessoa jurídica" or "police
// judiciaire", so it needs "en PJ", "voir PJ" or "PJ :" to be strong and an article to be weak.
const _GW_PJ=/(^|[^\p{L}\p{N}])((?:[Ee]n|[Vv]oir|[Cc]f\.?) PJ(?![\p{L}\p{N}])|PJ(?=\s{0,3}:))/u;
const _GW_PJW=/(^|[^\p{L}\p{N}])((?:[Ll]a|[Ll]es|[Uu]ne|[Dd]es|[Mm]a|[Mm]es|[Vv]otre|[Vv]os|[Nn]otre|[Nn]os|[Cc]ette|[Cc]es) PJ)(?![\p{L}\p{N}])/u;
const _GW_CV=/(^|[^\p{L}\p{N}])(CV)(?![\p{L}\p{N}])/u;
const _RE_PFX=/^\s*(?:(?:re|fwd?|fw)\s*:\s*)+/i;
function guardFold(s        )                        { let t="", i=0; const map         =[];   // map[k] = index in s of folded char k, so a match echoes as typed
  for(const ch of String(s)){ const f=ch.normalize("NFD").replace(/\p{M}/gu,"").toLowerCase();
    for(let k=0;k<f.length;k++)map.push(i); t+=f; i+=ch.length; }
  map.push(i); return {t,map}; }
function guardTypedText(body        ,sigBlock        )       {   // what you wrote: not the signature, not a quote
  let s=String(body||"").replace(/\r\n?/g,"\n");
  const sig=String(sigBlock||"").replace(/^\n+/,""), at=sig?s.lastIndexOf(sig):-1;
  if(at>=0&&(at===0||s[at-1]==="\n")) s=s.slice(0,at)+s.slice(at+sig.length);   // a P.S. below it is still yours
  const out         =[];
  for(const ln of s.split("\n")){
    if(/^-- ?$/.test(ln)) break;                                   // signature separator
    if(/^On .{0,300}wrote:\s*$/.test(ln)) break;                   // compose._attribution
    if(/^-{5,} ?Forwarded message ?-{5,}\s*$/i.test(ln)) break;    // compose.forward_bodies banner
    if(/^-{3,} ?Original Message ?-{3,}\s*$/i.test(ln)) break;     // a pasted Outlook quote
    if(/^\s*>/.test(ln)) continue;
    out.push(ln); }
  return out.join("\n").trimEnd().slice(0,GUARD_SCAN_MAX); }
function guardScrub(text        )       {   // links and addresses out (attachment.cgi, attachments@…), a word at a time so it stays linear
  return String(text||"").split(/(\s+)/).map(k=>k.includes("@")?" ":k.replace(/(?:https?|ftp):\/\/.*|www\..*/i," ")).join(""); }
function guardAttachWord(text        ,allowWeak        )       {   // the attachment word as typed, or ""
  const clean=guardScrub(text), {t,map}=guardFold(clean);
  const hit=(re       ,src       ,folded        )       =>{ const m=re.exec(src); if(!m)return ""; const a=m.index+m[1] .length, b=a+m[2] .length;
    return folded?clean.slice(map[a],map[b]):clean.slice(a,b); };
  return hit(_GW_S,t,true)||hit(_GW_PJ,clean,false)
    ||(allowWeak?(hit(_GW_W,t,true)||hit(_GW_FILE,t,true)||hit(_GW_PJW,clean,false)||hit(_GW_CV,clean,false)):""); }
function guardSubjectEmpty(subject        ,mode       )        { const s=String(subject||"").trim();
  return !s||(mode==="new"&&!s.replace(_RE_PFX,"").trim()); }   // a reply's inherited bare "Re:" is not empty
function guardSubjectAuthored(subject        ,subject0        ,mode       )        { const f=(x        )=>guardFold(String(x||"").replace(_RE_PFX,"")).t.trim();
  return mode==="new"||f(subject)!==f(subject0); }               // "Re: Contract attached" is the other sender's
function guardFanout(to         ,cc         ,mine         )       { const m=new Set(mine); return new Set([...to,...cc].filter(a=>a&&!m.has(a))).size; }
function guardList(a         )       { if(a.length<=1)return a[0]||""; if(a.length===2)return a[0]+" and "+a[1];
  if(a.length===3)return a[0]+", "+a[1]+" and "+a[2]; return a[0]+", "+a[1]+" and "+(a.length-2)+" more"; }
// Addresses are lowercased bare addresses. Bcc is left out: it's hidden from the others and typed on purpose.
function guardCheck(s              )               {
  if(s.reading.length) return [{key:"reading",block:"hard",name:s.reading[0] ,more:s.reading.length-1}];
  const w               =[];
  if(s.sendAfter>0&&s.sendAfter<=s.now+5) w.push({key:"late",block:"hard",when:s.sendAfter});
  if(!s.ack.subject&&guardSubjectEmpty(s.subject,s.mode)) w.push({key:"subject",block:"confirm"});
  const has=s.nAttach>0||(s.mode==="forward"&&s.fwdAttach>0);   // the server re-attaches a forward's files
  if(!has&&!s.ack.attach){ const weak=s.mode==="new"||s.mode==="forward";
    const word=guardAttachWord(guardTypedText(s.body,s.sigBlock),weak)
      ||(guardSubjectAuthored(s.subject,s.subject0,s.mode)?guardAttachWord(s.subject,weak):"");
    if(word) w.push({key:"attach",block:"confirm",word}); }
  if(s.mode==="replyall"){ const n=guardFanout(s.to,s.cc,s.mine);
    if(n>GUARD_FANOUT&&n>(s.ack.fanout||0)){ const mine=new Set(s.mine);
      const auto=s.autoCc, removable=auto?new Set(s.cc.filter(a=>auto.includes(a)&&!mine.has(a))).size:0;
      w.push({key:"fanout",block:"confirm",n,removable}); } }
  if(s.fresh.length) w.push({key:"new",block:"soft",addrs:s.fresh});
  return w; }
function guardBlocks(w               )        { return w.some(x=>x.block!=="soft"); }
function guardHard(w               )        { return w.some(x=>x.block==="hard"); }
function guardLine(w            )          {   // whenText for "late" is filled in by the caller
  switch(w.key){
    case "reading": return {text:`Still adding ${w.name}${w.more?` and ${w.more} more`:""}. Send again once ${w.more?"they’re":"it’s"} added.`, fix:null};
    case "late": return {text:`The time you picked, ${w.whenText}, has passed.`, fix:{kind:"time",label:"Pick a new time"}};
    case "subject": return {text:"There’s no subject.", fix:{kind:"subject",label:"Add a subject"}};
    case "attach": return {text:`You wrote “${w.word}”, but nothing is attached.`, fix:{kind:"attach",label:"Attach a file"}};
    case "fanout": return {text:`This reply goes to ${w.n} people.`,
      fix:w.removable>0?{kind:"trim",label:`Remove the ${w.removable} copied from the original`}:{kind:"review",label:"Review recipients"}};
    case "new": return {text:`New to you: ${guardList(w.addrs)}.`, fix:null};
    case "unchecked": return {text:"The checks before sending couldn’t run.", fix:null};
    case "norcpt": return {text:"Add at least one recipient.", fix:{kind:"to",label:"Add one"}};
    case "bad": return {text:`“${w.v}” isn’t a valid address.${w.more?` ${w.more} more ${w.more===1?"needs":"need"} fixing too.`:""}`,
      fix:{kind:"bad",label:"Edit it"}};
  }
  return {text:"",fix:null}; }
/* send-guard:pure:end */
