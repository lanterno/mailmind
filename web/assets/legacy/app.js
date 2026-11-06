"use strict";
const BVAR={"spam/scam":"--scam","blocked":"--block","newsletters/marketing":"--mkt","automated-notifications":"--notif","receipts/transactional":"--txn","personal/protected":"--keep","other/personal-ish":"--personal"};
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const state={all:[],q:"",account:"",live:null,lastSync:null,armed:null,liveStats:null,aliases:{},
  folder:"INBOX",folders:[],cmsgs:[],cSyncing:false,cSyncFailed:false,cCatching:false,csel:null,cseq:0,fseq:0,rseq:0,cmarked:new Set(),
  compose:null,myAddrs:new Set(),outbox:[],pending:null,failedSend:null,
  threadView:true,thread:null,searching:false,searchItems:[],searchSeq:0,searchScope:"all",sseLive:false,allInbox:false,
  views:[],viewId:null,screener:[],screenerCount:0,replylaterCount:0,
  asking:false,askQuery:"",askAnswer:null,askSeq:0,
  doSeq:0,_doPlan:null,_doCmd:"",_doArmed:false,_cleanupPlan:null};
const nameOf=a=>(state.aliases&&state.aliases[a])||short(a);  // alias-aware display label

const esc=s=>(s==null?"":""+s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const short=a=>(a||"").split("@")[0];
const hue=a=>{let h=0;for(const c of(a||""))h=(h*31+c.charCodeAt(0))%360;return 22+(h%34);};  // amber band 22–56°, not full rainbow
function ico(n,c){ return `<svg class="ico${c?" "+c:""}" aria-hidden="true" focusable="false"><use href="#i-${n}"/></svg>`; }   // the sprite after <body>
function toast(m){const t=$("#toast");t.textContent=m;t.classList.add("show");clearTimeout(t._t);t._t=setTimeout(()=>t.classList.remove("show"),2600);}
const CSRF=(document.querySelector('meta[name=csrf]')||{}).content||"";
const AI_OVER="X-MailMind-AI-Over-Limit";   // "allow": the user said this request may go over the daily AI limit
async function api(p,o){o=o||{}; o.headers=Object.assign({"X-CSRF-Token":CSRF},o.headers||{});   // on GETs too: cost-bearing reads (draft-ahead kick) verify it
  const r=await fetch(p,o);
  if(!r.ok){ let m="HTTP "+r.status, j=null; try{ j=await r.json(); if(j&&j.error)m=j.error; }catch(e){}
    if(r.status===402&&j&&j.over_limit&&o.askOver!==false&&!o.headers[AI_OVER]&&await askOverLimit(j))   // at the limit: ask, then again with leave
      return api(p,Object.assign({},o,{headers:Object.assign({},o.headers,{[AI_OVER]:"allow"})}));
    throw Object.assign(new Error(m),{status:r.status,overLimit:!!(j&&j.over_limit),aiOff:!!(j&&j.ai_off)}); }
  return r.json();}
/* The daily AI limit: an AI request you started that would go over it asks first. Requests that hit
   the limit while the question is on screen share its one answer. One the page makes on its own
   passes askOver:false and gets the 402 as an error (overLimit) instead. */
let _overAsk=null;
function askOverLimit(j){
  if(_overAsk) return _overAsk;
  const o=$("#aiLimitAsk"), back=document.activeElement;
  $("#alText").textContent=`MailMind has spent ${usd(j.spent_micros||0)} on AI today, and its daily limit is ${usd(j.limit_micros||0)}. Go over the limit for this?`;
  _overAsk=new Promise(done=>{
    const answer=yes=>{ o.classList.remove("show"); o.removeEventListener("click",onClick); o.removeEventListener("keydown",onKey);
      _overAsk=null; if(back&&back.focus)back.focus(); done(yes); };
    const onClick=e=>{ const b=e.target.closest("[data-al]"); if(b)answer(b.dataset.al==="yes"); else if(e.target===o)answer(false); };
    const onKey=e=>{ if(e.key==="Escape"){ e.preventDefault(); e.stopPropagation(); answer(false); } };
    o.addEventListener("click",onClick); o.addEventListener("keydown",onKey);
    o.classList.add("show"); o.querySelector('[data-al="no"]').focus();
  });
  return _overAsk;
}

/* ---------- the digest: AI-scored mail behind the brief, commitments and tidy ---------- */
async function loadDigest(silent){
  let d; try{ d=await api("/api/digest?min=1&account="); }
  catch(e){ if(!silent&&!state.account){ $("#rows").innerHTML='<div class="nores">Couldn’t reach the server. <button class="ai-rerun" id="dgRetry">Retry</button></div>'; const b=$("#dgRetry"); if(b)b.onclick=()=>loadDigest(false); setTimeout(()=>loadDigest(true),5000); } return; }
  state.all=d.items||[]; state.stats=d.stats||{}; state.statusMap=d.status||{}; state.ai=d.ai||null;
  // prune pre-draft state for pks no longer in ANY live surface (bounds memory; drops stale chips for
  // gone messages). The union matters: a digest reload must never clobber chips owned by the reader,
  // search results, an open thread, or the currently-viewed message.
  { const live=new Set([...state.all.map(x=>x.pk), ...(state.cmsgs||[]).map(x=>x.pk),
      ...(state.searchItems||[]).map(x=>x.pk),
      ...((state.thread&&state.thread.items)||[]).map(x=>x.pk)]);
    if(state.thread&&state.thread.reply_pk!=null)live.add(state.thread.reply_pk);
    if(state.csel!=null)live.add(state.csel);
    [..._draftReady.keys()].forEach(k=>{ if(!live.has(k))_draftReady.delete(k); });
    [..._draftingPks].forEach(k=>{ if(!live.has(k))_draftingPks.delete(k); }); }
  state.aliases=d.aliases||{};
  state.signatures=d.signatures||{}; state.signatureDefault=d.signature_default||"";
  state.accountsList=d.accounts||[]; if(d.sync)state.lastSync=d.sync;
  state.profile=d.profile||"full";   // read-only: nothing is written to a mail server; moves and sends wait
  // sendable identities come from the configured-account list (works even before sync / under NOSYNC)
  state.sendAccounts=(d.send_accounts&&d.send_accounts.length)?d.send_accounts:(d.accounts||[]);
  state.myAddrs=new Set([...(d.send_accounts||[]),...(d.accounts||[])].map(e=>(e||"").toLowerCase()));
  renderMonos(); renderStatus();
  renderAccountAlert();
  if(!state.account) openMailbox();   // boot, or the first account just arrived
  else renderBrief();                 // the brief's needs-you list reads the digest
}
let _alertDismissed=new Set();
function renderAccountAlert(){
  const bad=Object.entries(state.statusMap||{}).filter(([e,s])=>s&&s.health==="error"&&!_alertDismissed.has(e)).map(([e])=>e);
  const bar=$("#acctAlert");
  if(!bad.length){ bar.hidden=true; bar.innerHTML=""; return; }
  bar.hidden=false;
  bar.innerHTML=`<span>${ico("alert")} ${bad.length} account${bad.length>1?"s":""} couldn’t connect (${bad.map(nameOf).join(", ")}). </span>
    <button class="aa-fix" id="aaFix">Open Settings</button><button class="aa-x" id="aaX" aria-label="Dismiss">✕</button>`;
  $("#aaFix").onclick=()=>settingsOpen(true);
  $("#aaX").onclick=()=>{ bad.forEach(e=>_alertDismissed.add(e)); renderAccountAlert(); };
}
/* ---------- sync status: the pill names the one thing that matters; the popover has the rest ---------- */
let _manualSync=false, _statusPoll=0, _syncDoneT=0;
function badAccounts(){ return Object.entries(state.statusMap||{}).filter(([,st])=>st&&st.health==="error").map(([e])=>e); }
function syncLabel(){
  const bad=badAccounts(), sy=state.lastSync||{};
  if(bad.length) return {cls:"err", txt:bad.length===1?`${nameOf(bad[0])} can’t connect`:`${bad.length} accounts can’t connect`};
  if(reconciling) return {cls:"busy", txt:"Reconciling…"};
  if(_manualSync||sy.running||Object.values(state.statusMap||{}).some(st=>st&&st.health==="syncing")) return {cls:"busy", txt:"Syncing…"};
  if(readOnly()) return {cls:"", txt:"Read-only"};   // no background sync, and nothing it files reaches the server yet
  if(sy.last){ const d=new Date(sy.last), today=d.toDateString()===new Date().toDateString();
    return {cls:"ok", txt:"Synced "+(today?d.toLocaleTimeString([],{hour:"numeric",minute:"2-digit"}):d.toLocaleDateString([],{month:"short",day:"numeric"}))}; }
  return {cls:"", txt:"Not synced yet"};
}
function readOnly(){ return state.profile==="read-only"; }
function renderStatus(){
  const L=syncLabel(), b=$("#statusBtn"); if(!b)return;
  b.querySelector(".sdot").className="sdot "+L.cls; $("#statusTxt").textContent=L.txt;
  b.setAttribute("aria-label","Sync status: "+L.txt);
  const info=$("#statusMenu .sm-info"); if(info)info.innerHTML=statusInfoHtml(L);   // in place: a focused button keeps focus
  const sb=$('#statusMenu [data-st="sync"]'); if(sb)sb.disabled=L.txt==="Syncing…";
  const rb=$('#statusMenu [data-st="reconcile"]'); if(rb)rb.disabled=reconciling;
  watchSync();
}
function statusInfoHtml(L){
  const sy=state.lastSync||{}, s=state.stats||{};
  const live=state.live!=null?`<b>${state.live.toLocaleString()}</b> in your inboxes`:"counting your inboxes…";
  const accts=(state.accountsList||[]).map(a=>{ const h=((state.statusMap||{})[a]||{}).health||"";
    return `<div class="sm-acct"><i class="sdot ${h==="error"?"err":h==="syncing"?"busy":h==="ok"?"ok":""}" aria-hidden="true"></i><span>${esc(nameOf(a))}</span>`
      +`<small${h==="error"?' class="err"':""}>${h==="error"?"can’t connect":h==="syncing"?"syncing…":h==="ok"?"connected":""}</small></div>`; }).join("");
  return `<div class="sm-hd"><i class="sdot ${L.cls}" aria-hidden="true"></i>${esc(L.txt)}</div>
    ${readOnly()?'<div class="sm-sub">MailMind is running read-only: it writes nothing to your mail servers and syncs only when asked. What you file, star or send waits for a full run.</div>':""}
    ${sy.last&&sy.result?`<div class="sm-sub">Last sync: ${esc(sy.result)}</div>`:""}
    <div class="sm-sub">${live} · <b>${(s.summarized||0).toLocaleString()}</b> summarized</div>
    ${aiLineHtml()}
    ${(state.accountsList||[]).length>1||badAccounts().length?`<div class="sm-accts">${accts}</div>`:""}`;
}
function openStatusMenu(){
  closeStatusMenu(); closeAcctMenu(); closeMoreMenu(); closeReadMenu(); closeSnoozeMenu();
  const anchor=$("#statusBtn"), L=syncLabel();
  const notify=window.Notification&&Notification.permission==="default";
  const m=document.createElement("div"); m.className="status-menu"; m.id="statusMenu"; m.setAttribute("role","dialog"); m.setAttribute("aria-label","Sync status");
  m.innerHTML=`<div class="sm-info">${statusInfoHtml(L)}</div><div class="sm-acts">
    <button data-st="sync"${L.txt==="Syncing…"?" disabled":""}><span class="mi-ic" aria-hidden="true">${ico("refresh")}</span>Sync now</button>
    <button data-st="reconcile"${reconciling?" disabled":""}><span class="mi-ic" aria-hidden="true">${ico("restore")}</span>Reconcile<small>drop mail deleted elsewhere</small></button>
    <button data-st="curate"><span class="mi-ic" aria-hidden="true">${ico("leaf")}</span>Curate<small>noisy senders, rules, audit</small></button>
    ${badAccounts().length?`<button data-st="settings"><span class="mi-ic" aria-hidden="true">${ico("sliders")}</span>Fix connections in Settings</button>`:""}
    ${notify?`<button data-st="notify"><span class="mi-ic" aria-hidden="true">${ico("bell")}</span>Turn on desktop notifications</button>`:""}</div>`;
  document.body.appendChild(m);
  const r=anchor.getBoundingClientRect();
  m.style.top=(r.bottom+6)+"px"; m.style.left=Math.max(10,Math.min(r.right-m.offsetWidth,window.innerWidth-m.offsetWidth-10))+"px";
  anchor.setAttribute("aria-expanded","true");
  (m.querySelector(".sm-acts button:not(:disabled)")||m).focus();
  m.addEventListener("click",e=>{ e.stopPropagation(); const it=e.target.closest("[data-st]"); if(!it||it.disabled)return; const a=it.dataset.st;
    if(a==="notify"){ Notification.requestPermission().then(()=>toast(Notification.permission==="granted"?"Notifications on":"Notifications blocked")); closeStatusMenu(true); return; }
    closeStatusMenu();
    if(a==="sync")sync(); else if(a==="reconcile")reconcile(); else if(a==="curate")openDrawer(true); else if(a==="settings")settingsOpen(true); });
  setTimeout(()=>{ if(m.isConnected)document.addEventListener("click",_statusOutside); },0);   // after the opening click finishes
  window.addEventListener("blur",_statusBlur);
  if(state.live==null||!state.liveStats) loadStats();   // the live inbox count is an IMAP scan: only when asked
}
function _statusOutside(e){ if(!e.target.closest("#statusMenu"))closeStatusMenu(); }
function _statusBlur(){ setTimeout(()=>{ if(frameTookFocus())closeStatusMenu(); },0); }
function closeStatusMenu(refocus){
  const m=$("#statusMenu"); if(!m)return;
  m.remove(); document.removeEventListener("click",_statusOutside); window.removeEventListener("blur",_statusBlur);
  const b=$("#statusBtn"); b.setAttribute("aria-expanded","false"); if(refocus)b.focus();
}
function watchSync(){   // while the server syncs, re-read the digest so the pill lands on "Synced" when it ends
  if(_statusPoll||!(state.lastSync&&state.lastSync.running))return;
  let n=0; _statusPoll=setInterval(async()=>{ n++; await loadDigest(true);
    if(!(state.lastSync&&state.lastSync.running)||n>60){ clearInterval(_statusPoll); _statusPoll=0; renderStatus(); } },5000);
}
function onSyncDone(){ clearTimeout(_syncDoneT); _syncDoneT=setTimeout(()=>loadDigest(true),1500); }   // one per account → one refresh
function acctColor(a){ return `var(--acct-${Math.max(0,(state.accountsList||[]).indexOf(a))%6+1})`; }
function acctDots(accts){   // each account's own color; several overlap into a stack
  return `<span class="acct-stack" aria-hidden="true">${accts.slice(0,4).map(a=>`<i style="--a:${acctColor(a)}"></i>`).join("")}</span>`;
}
function acctDown(a){ return (((state.statusMap||{})[a]||{}).health||"")==="error"; }
function renderMonos(){   // the account switcher heading the rail
  const list=state.accountsList||[], cur=state.allInbox?"__all__":state.account;
  if(!list.length||!cur){ $("#monos").innerHTML=""; return; }
  const down=list.filter(acctDown).length;
  const name=cur==="__all__"?"All inboxes":nameOf(cur);
  const sub=cur==="__all__"?(down?`${down} of ${list.length} can’t connect`:`${list.length} account${list.length===1?"":"s"}, one inbox`):acctDown(cur)?"can’t connect":cur;
  $("#monos").innerHTML=`<button class="acct-sw" id="acctSw" aria-haspopup="menu" aria-expanded="false" title="${esc(name)} — switch account" aria-label="Account: ${esc(name)}${down?` (${esc(sub)})`:""}. Switch account">${acctDots(cur==="__all__"?list:[cur])}<span class="as-txt"><b>${esc(name)}</b><small${down&&(cur==="__all__"||acctDown(cur))?' class="err"':""}>${esc(sub)}</small></span>${ico("down","as-chev")}</button>`;
}
function switchAccount(a){
  clearSearch(false);
  if(a==="__all__"){ enterAllInbox(); return; }
  state.allInbox=false; state.account=a; renderMonos(); loadFolders();
}
function openAcctMenu(){
  closeAcctMenu(); closeStatusMenu();
  const anchor=$("#acctSw"); if(!anchor)return;
  const list=state.accountsList||[], cur=state.allInbox?"__all__":state.account;
  const item=a=>{ const bad=a!=="__all__"&&acctDown(a);
    const sub=a==="__all__"?`${list.length} account${list.length===1?"":"s"}, one inbox`:bad?"can’t connect":a;
    return `<button role="menuitemradio" aria-checked="${a===cur}" data-acct="${esc(a)}">${acctDots(a==="__all__"?list:[a])}<span class="am-txt"><b>${esc(a==="__all__"?"All inboxes":nameOf(a))}</b><small${bad?' class="err"':""}>${esc(sub)}</small></span>${a===cur?ico("check","am-chk"):""}</button>`; };
  const m=document.createElement("div"); m.className="acct-menu"; m.id="acctMenu"; m.setAttribute("role","menu"); m.setAttribute("aria-label","Show mail from");
  m.innerHTML=`<div class="am-ttl" aria-hidden="true">Show mail from</div>`+item("__all__")+list.map(item).join("")+`<hr><button role="menuitem" data-manage>${ico("sliders")}<span class="am-txt"><b>Add or manage accounts</b></span></button>`;
  document.body.appendChild(m);
  const r=anchor.getBoundingClientRect();
  m.style.top=Math.max(10,Math.min(r.bottom+6,window.innerHeight-m.offsetHeight-10))+"px";
  m.style.left=Math.max(10,Math.min(r.left,window.innerWidth-m.offsetWidth-10))+"px";
  anchor.setAttribute("aria-expanded","true");
  (m.querySelector('[aria-checked="true"]')||m.querySelector("button")).focus();
  m.addEventListener("click",e=>{ e.stopPropagation(); if(e.target.closest("[data-manage]")){ closeAcctMenu(); settingsOpen(true); return; }
    const it=e.target.closest("[data-acct]"); if(!it)return; closeAcctMenu(); switchAccount(it.dataset.acct); });
  setTimeout(()=>{ if(m.isConnected)document.addEventListener("click",_acctOutside); },0);   // after the opening click finishes
  window.addEventListener("blur",_acctBlur);
}
function _acctOutside(e){ if(!e.target.closest("#acctMenu"))closeAcctMenu(); }
function _acctBlur(){ setTimeout(()=>{ if(frameTookFocus())closeAcctMenu(); },0); }
function closeAcctMenu(refocus){
  const m=$("#acctMenu"); if(!m)return;
  m.remove(); document.removeEventListener("click",_acctOutside); window.removeEventListener("blur",_acctBlur);
  const b=$("#acctSw"); if(b){ b.setAttribute("aria-expanded","false"); if(refocus)b.focus(); }
}

/* ---------- list ---------- */

/* ---------- reader ---------- */

/* ---------- actions ---------- */
function afterRemove(pk){
  state.all=state.all.filter(x=>x.pk!==pk);
  _draftReady.delete(pk); _draftingPks.delete(pk);   // acted on → drop any pre-draft state (no stale chip / grow)
  if(cfindAny(pk))cRemove(pk); renderBrief();
}
// Brief/summary ARCHIVE — the everyday "I'm done, file it" verb (reversible). `e` means archive in BOTH
// the Brief and the Mailbox now (was: e=trash in the Brief — a dangerous mode-dependent meaning).
async function archiveSel(pk){ const it=state.all.find(x=>x.pk===pk); if(!it)return;
  try{ const r=await cpost("/api/archive",{pk});
    if(r.ok){ afterRemove(pk); toast("Archived · in the mailbox's Archive"); if(r.undoable)showUndoFloat("Archived"); refreshStats(); }
    else toast("Couldn’t archive: "+(r.msg||r.error||"")); }
  catch(e){ toast("Archive failed"); } }
// importance filter (1-5) — keeps aria-checked in sync (the keyboard path used to desync it from the radios)
function openIt(pk){ const it=state.all.find(x=>x.pk===pk); if(it&&typeof it.link==="string"&&/^https:\/\//.test(it.link)) window.open(it.link,"_blank","noopener"); }
async function postRule(token,verdict,pk){ try{ const d=await api("/api/rules",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({token,verdict,pk})}); loadRules(); refreshStats(); onReclassified(d); return d; }catch(e){ toast("Rule failed"); } }
function onReclassified(d){ if(d&&d.reclassified){ loadDigest(true); if(state.account)onReconcile({account:state.account}); } }   // a rule changed how stored mail reads

/* ---------- curate drawer ---------- */
// the #scrim backdrop is shared by the curate drawer, settings, compose, and the mobile rail —
// show it iff ANY of them is open, so closing one never strands another without its backdrop
function _syncScrim(){ const on=$("#drawer").classList.contains("open")||settingsIsOpen()
  ||($("#composeModal")&&$("#composeModal").classList.contains("show"))||document.body.classList.contains("rail-open");
  $("#scrim").classList.toggle("on",on); }
function openDrawer(o){ $("#drawer").classList.toggle("open",o); _syncScrim(); if(o){loadFloodSenders();loadStats();loadRules();loadAudit();} }
async function refreshStats(){ if(!$("#drawer").classList.contains("open"))return;   // drawer closed → skip the live 8-account scan; it reloads on openDrawer + the 120s poll
  try{ await loadStats(); }catch(e){} }
async function loadStats(){
  let d; try{ d=await api("/api/stats"); }catch(e){ return; }
  state.liveStats=d; state.live=d.totals.in_inbox; renderStatus();
  $("#railAccounts").innerHTML=d.accounts.map(a=>{
    const total=a.in_inbox||0;
    const segs=d.buckets.map(b=>{const n=(a.by_bucket||{})[b.name]||0;if(!n)return"";return `<i style="width:${total?n/total*100:0}%;background:var(${BVAR[b.name]||"--mute"})" title="${esc(b.name)}:${n}"></i>`;}).join("");
    const armed=state.armed&&state.armed.account===a.account;
    return `<div class="acctmod ${a.error?"error":"ok"}" data-acct="${esc(a.account)}">
      <div class="top2"><span class="led"></span><span>${esc(nameOf(a.account))}</span><span class="cnt">${a.error?"—":total}</span></div>
      <div class="microbar">${segs}</div>
      <div class="r3"><span>${a.error?'<span style="color:var(--err)">'+esc(a.error.slice(0,24))+'</span>':(a.junk+" junk · "+a.keep+" keep")}</span>
        ${a.junk?`<button class="sweep" data-acct="${esc(a.account)}">Sweep ${a.junk}</button>`:""}</div>
      ${armed?`<div class="armline">▸ sweep <b>${esc(nameOf(a.account))}</b> · <b>${state.armed.planned}</b> msgs<br>[Enter] confirm · [Esc] abort</div>`:""}</div>`;
  }).join("");
}
async function loadRules(){ let d; try{ d=await api("/api/rules"); }catch(e){ return; } const r=d.rules;
  const ch=(arr,cls)=>arr.map(t=>`<span class="rchip ${cls}" data-token="${esc(t)}">${esc(t)}</span>`).join("")||'<span style="color:var(--faint);font-size:10px">none</span>';
  $("#keepList").innerHTML=ch([...r.keep_domains,...r.keep_senders],"keep"); $("#blockList").innerHTML=ch([...r.block_domains,...r.block_senders],"block"); }
async function loadAudit(){ let d; try{ d=await api("/api/audit?limit=60"); }catch(e){ return; }
  $("#console").innerHTML=d.events.map(e=>{const t=e.ts?new Date(e.ts).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):"";
    const txt=e.kind==="reconcile"?`struck ${e.pruned} · ${esc(nameOf(e.account))}`:`${esc(nameOf(e.account))} · ${esc((e.from||"").slice(0,22))}`;
    return `<div class="ln" data-acct="${esc(e.account||"")}"><span class="k ${esc(e.kind)}">${esc(e.kind)}</span><span class="t">${esc(t)}</span><span>${txt}</span></div>`;}).join("")||'<div style="color:var(--faint)">No activity yet.</div>'; }

async function armSweep(account){ try{ const d=await api("/api/sweep",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({account,dry_run:true})}); const r=d.results[0];
  if(r.error)return toast("Sweep: "+r.error); if(!r.planned)return toast("Nothing to sweep"); state.armed={account,planned:r.planned}; loadStats(); }catch(e){toast("Plan failed");} }
async function confirmSweep(){ if(!state.armed)return; const {account,planned}=state.armed; state.armed=null; toast("Sweeping "+planned+"…");
  try{ const d=await api("/api/sweep",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({account,dry_run:false})}); const r=d.results[0];
    toast(`Swept ${r.moved}/${r.planned}`+(r.stragglers&&r.stragglers.length?` · ${r.stragglers.length} unverified`:"")); }catch(e){toast("Sweep failed");}
  loadStats(); loadDigest(true); loadAudit(); }
function abortSweep(){ if(state.armed){state.armed=null;loadStats();toast("Aborted");} }

/* ── Senders flooding you: unsubscribe & cleanup (drawer lane → #doPanel confirm) ── */
async function loadFloodSenders(){
  const el=$("#floodList"); if(!el)return;
  let d; try{ d=await api("/api/cleanup/senders"); }catch(e){ el.innerHTML='<div style="color:var(--faint);font-size:11px">Couldn’t load.</div>'; return; }
  const s=(d&&d.senders)||[];
  if(!s.length){ el.innerHTML='<div style="color:var(--faint);font-size:11.5px;padding:6px 2px">No sender is flooding you right now.</div>'; return; }
  el.innerHTML=s.map(x=>{
    const hot=(x.unseen_ratio||0)>=0.7, multi=((x.accounts||[]).length)>1;
    const chips=[`<span class="flood-chip">${x.count} msg${x.count===1?"":"s"}</span>`,
      x.unseen?`<span class="flood-chip${hot?" hot":""}">${x.unseen} unread</span>`:"",
      multi?`<span class="flood-chip multi">${x.accounts.length} inboxes</span>`:"",
      x.lu_likely?`<span class="flood-chip lu1">1-click</span>`:"",
      x.last_seen?`<span class="flood-chip">${esc(x.last_seen)}</span>`:""].filter(Boolean).join("");
    return `<div class="flood-row" data-flood="${esc(x.from_addr)}">
      <div class="flood-main"><div class="flood-nm">${esc(x.from_name||x.from_addr)}</div>
        <div class="flood-ad">${esc(x.from_addr)}</div><div class="flood-chips">${chips}</div></div>
      <div class="flood-side"><span class="flood-score" title="noise score">${x.noise_score}</span>
        <button class="flood-go" data-flood-go="${esc(x.from_addr)}">Clean up</button></div></div>`;
  }).join("");
}
async function openCleanupPreview(addr){
  renderDoUI("loading",{cmd:"cleanup "+addr});
  let d; try{ d=await api("/api/cleanup/sender?addr="+encodeURIComponent(addr)); }catch(e){ closeDoPanel(); toast("Couldn’t load sender"); return; }
  if(!d.ok){ closeDoPanel(); toast(d.error||"error"); return; }
  state._cleanupPlan={from_addr:d.from_addr, confirm_token:d.confirm_token, scope:"sender",
    method:(d.unsub&&d.unsub.method)||"none", host:(d.unsub&&d.unsub.https_host)||"", mailto_to:(d.unsub&&d.unsub.mailto_to)||"",
    count:d.count, sweep:d.sweep_count, sweep_capped:d.sweep_capped, domain:d.domain, accounts:d.accounts||[],
    do_unsub:((d.unsub&&d.unsub.method)==="one-click"||(d.unsub&&d.unsub.method)==="mailto")};
  state._doArmed=false; renderCleanupPlan();
}
function renderCleanupPlan(){
  const p=state._cleanupPlan, body=$("#doPanelBody"); if(!p||!body)return; openDoPanel();
  const m=p.method;
  const unsubText = m==="one-click" ? `Send a <b>one-click</b> unsubscribe to ${esc(p.host||"the sender")} — no email, no browser`
                  : m==="mailto"   ? `Email the list’s unsubscribe address <b>${esc(p.mailto_to||"")}</b> from your account`
                  : m==="http-page"? `Open the unsubscribe <b>page</b> in a new tab (nothing is auto-submitted)`
                  : "";
  const unsubLine = m==="none" ? `<div class="do-line"><span class="ic">·</span><span style="color:var(--faint)">No List-Unsubscribe found — block + sweep only</span></div>`
                  : `<div class="do-line irrev"><span class="ic">${ico("alert")}</span><span>${unsubText}</span><span class="tag">can’t undo</span></div>`;
  const optLabel = m==="http-page" ? "open the unsubscribe page" : "also send the unsubscribe now";
  const unsubOpt = m==="none" ? "" : `<label class="do-unsubopt"><input type="checkbox" id="doUnsubChk" ${p.do_unsub?"checked":""}> ${optLabel}</label>`;
  const scopeLbl = p.scope==="domain"?p.domain:p.from_addr;
  const sweepFrom = p.scope==="domain"?` from ${esc(p.from_addr)}`:"";   // domain block, sender-only sweep
  const capNote = p.sweep_capped?`<div class="do-cap">${ico("alert")} Sweeps the most recent ${p.sweep} — run again for the rest.</div>`:"";
  body.innerHTML=`<div class="do-plan do-plan-danger">
    <div class="do-hd">Clean up <b>${esc(p.from_addr)}</b>?</div>
    <div class="do-scope">${p.count} message${p.count===1?"":"s"} in your synced mail${p.accounts.length>1?` · ${p.accounts.length} inboxes`:""}</div>
    <div class="do-line rev"><span class="ic">✓</span><span>Block <b>${esc(scopeLbl)}</b> — future mail auto-filed</span><span class="tag">reversible</span></div>
    <div class="do-scopetoggle"><button data-cleanup-scope="sender" class="${p.scope==="sender"?"on":""}">this sender</button><button data-cleanup-scope="domain" class="${p.scope==="domain"?"on":""}">whole domain</button></div>
    <div class="do-line rev"><span class="ic">✓</span><span>Move <b>${p.sweep}</b> inbox message${p.sweep===1?"":"s"}${sweepFrom} to Trash</span><span class="tag">reversible</span></div>
    ${unsubLine}${unsubOpt}${capNote}
    <div class="do-danger-note">Block + sweep undo with <b>z</b>. ${m==="none"?"":"Unsubscribing can’t be undone."}</div>
    <div class="do-foot"><button class="do-confirm do-confirm-danger" data-cleanup-confirm>Clean up</button>
      <button class="do-cancel" data-do-cancel>Cancel</button></div></div>`;
}
async function cleanupConfirm(){
  const p=state._cleanupPlan; if(!p)return;
  const chk=$("#doUnsubChk"), do_unsub=chk?chk.checked:false;
  // http-page: pre-open a tab in the click gesture so the post-await navigate isn't popup-blocked.
  // NO "noopener" in features (that would force window.open to return null → unusable handle); we
  // sever the opener manually below to keep the tabnabbing protection.
  const win=(p.method==="http-page"&&do_unsub)?window.open("about:blank","_blank"):null;
  state._cleanupPlan=null; state._doArmed=false; closeDoPanel();
  const grp=undoGroup();
  toast("Cleaning up "+p.from_addr+"…");
  let d; try{ d=await cpost("/api/cleanup/execute",{from_addr:p.from_addr,block_scope:p.scope,do_unsubscribe:do_unsub,confirm_token:p.confirm_token,undo_group:grp}); }
  catch(e){ if(win)win.close(); toast("Cleanup failed"); return; }
  if(!d.ok){ if(win)win.close(); toast(d.error||"failed"); return; }
  if(win){ if(d.browser_url){ try{win.opener=null;}catch(_){} win.location=d.browser_url; } else win.close(); }
  const stmap={unsubscribed_oneclick:"unsubscribed",mailto_sent:"unsub email sent",needs_browser:"opened the unsub page",no_method:"no unsub link",failed:"unsub couldn’t send — retry later",no_token:"unsub not sent — reopen to retry",read_only:"unsub not sent — MailMind is read-only",skipped:""};
  const extra=stmap[d.status]||"";
  toast(`Blocked · swept ${d.swept}${extra?" · "+extra:""}`);
  showUndoFloat(`Cleaned up ${p.from_addr} — Undo (z)`);
  loadFloodSenders(); loadStats(); loadRules(); loadAudit();
  if(!state.searching)loadCMessages();
}

/* ---------- reconcile ---------- */
let reconciling=false;
async function reconcile(){ if(reconciling)return; reconciling=true; renderStatus();   // in-flight feedback on the pill
  const done=()=>{ reconciling=false; renderStatus(); };
  try{ const dry=await api("/api/reconcile",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({account:"all",dry_run:true})});
    if(!dry.total_ghosts){ toast("Nothing to reconcile — the record is straight."); done(); return; }
    toast("Reconciling — striking "+dry.total_ghosts+" ghosts…");
    const real=await api("/api/reconcile",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({account:"all",dry_run:false})});
    await loadDigest(false); loadStats(); loadAudit();
    toast(`Struck ${real.total_pruned.toLocaleString()} deleted items · ${(state.stats.kept||0).toLocaleString()} stand`);
  }catch(e){ toast("Reconcile failed"); } done(); }

/* ---------- sync ---------- */
async function sync(){ if(_manualSync)return;   // guard: no double-submit / leaked poll loop
  _manualSync=true; renderStatus(); toast("Syncing…");
  const done=()=>{ _manualSync=false; };
  try{ await api("/api/refresh",{method:"POST"}); }catch(e){}
  let n=0; const iv=setInterval(async()=>{ n++; let d; try{ d=await api("/api/digest?min=1&account="); }catch(e){ return; }
    if(!(d.sync&&d.sync.running)||n>40){ clearInterval(iv); done(); loadDigest(true); loadStats(); toast("Sync complete"); } },3000); }

/* ---------- Mailbox mode (full email client) ---------- */
const FOLDER_ICON={inbox:"inbox",sent:"send",drafts:"file",archive:"archive",all:"archive",trash:"trash",junk:"ban",custom:"folder",snoozed:"clock"};
const ROLE_LABEL={all:"All mail"};   // a role whose name isn’t the folder’s: all = every message (Gmail’s All Mail)
function roleLabel(f){ return f.role==="custom"?f.name:(ROLE_LABEL[f.role]||f.role[0].toUpperCase()+f.role.slice(1)); }
function openMailbox(){   // the only view: folders, list and reader, its Inbox opening on today's brief
  if(!state.account) state.account=(state.accountsList||[])[0]||"";
  renderMonos(); openClient();
}
async function openClient(){
  if(!state.account){
    if(!(state.accountsList||[]).length){ firstRun(); return; }
    $("#rows").innerHTML='<div class="nores">No account selected.</div>'; return; }
  await loadFolders();
}
function firstRun(){   // nothing connected yet: say so, and offer the way in (loadDigest opens the mailbox once one is)
  $("#rows").innerHTML=`<div class="nores firstrun"><div class="fr-h">Welcome to MailMind</div>
    <div class="fr-b">No email accounts connected yet.</div>
    <button class="abtn prim" data-firstrun>+ Connect your first account</button></div>`;
  $("#count").textContent=""; $("#folderrail").innerHTML=""; $("#clientCtx").textContent="";
  const r=$("#reader"); r.className="reader empty-pane"; r.textContent="Your mail shows up here once an account is connected.";
}
async function loadFolders(refreshOnly){
  if(!state.account)return;   // no account yet (first run) → /api/folders?account= would 500
  // refreshOnly = update rail counts + re-render WITHOUT reloading the message list (used by
  // background SSE ticks so they never yank the reader / clear a selection / disrupt a search).
  const seq=++state.fseq, acct=state.account;   // the rail's own sequence: a list or reader load never drops it, nor it them
  state.foldersByAcct=state.foldersByAcct||{};
  const cached=state.foldersByAcct[acct];
  if(cached){                                  // render instantly from cache, refresh in the background
    state.folders=cached;
    if(!state.folders.some(f=>f.name===state.folder)) state.folder=(state.folders[0]||{}).name||"INBOX";
    renderFolders(); if(!refreshOnly)loadCMessages();
  } else if(!refreshOnly) $("#folderrail").innerHTML='<div class="frail-acct">loading…</div>';
  let d; try{ d=await api("/api/folders?account="+encodeURIComponent(acct)); }
  catch(e){ if(!cached&&seq===state.fseq){ $("#folderrail").innerHTML='<div class="frail-acct">Couldn’t list folders</div><button class="frail-retry" id="frRetry">↻ Retry</button>'; const b=$("#frRetry"); if(b)b.onclick=loadFolders; } return; }
  if(seq!==state.fseq||acct!==state.account)return;  // a newer rail load, or an account switch, superseded this
  state.foldersByAcct[acct]=d.folders||[]; state.snoozedCount=d.snoozed||0; state.mutedCount=d.muted||0; state.waitingCount=d.waiting||0; state.replylaterCount=d.replylater||0;
  state.folders=d.folders||[];
  if(!state.folders.some(f=>f.name===state.folder)) state.folder=(state.folders[0]||{}).name||"INBOX";
  renderFolders(); renderBrief();   // its tallies count waiting / reply later
  if(!state.views.length) loadViews();          // app-global saved views/lanes (once)
  if(!refreshOnly) loadScreener();              // undecided first-contact senders (rail badge + tray)
  if(!cached && !refreshOnly) loadCMessages();  // when cached we already kicked the list load above
}
function renderFolders(){
  if(!state.account)return;   // first run: no account, no folders — firstRun owns the empty rail
  const snz="__snoozed__";
  const rows=state.folders.map(f=>{
    const lbl=roleLabel(f);
    return `<div class="frow${f.name===state.folder?" on":""}" data-folder="${esc(f.name)}">
      <span class="ic2">${ico(FOLDER_ICON[f.role]||"folder")}</span><span class="fn">${esc(lbl)}</span>
      ${f.unseen?`<span class="unread">${f.unseen}</span>`:""}</div>`;
  }).join("");
  const snzRow=`<div class="frow${state.folder===snz?" on":""}" data-folder="${snz}">
      <span class="ic2">${ico("clock")}</span><span class="fn">Snoozed</span>
      ${state.snoozedCount?`<span class="unread">${state.snoozedCount}</span>`:""}</div>`;
  const mutRow=state.mutedCount?`<div class="frow${state.folder==="__muted__"?" on":""}" data-folder="__muted__">
      <span class="ic2">${ico("bell-off")}</span><span class="fn">Muted</span>
      <span class="unread">${state.mutedCount}</span></div>`:"";
  const waitRow=state.waitingCount?`<div class="frow${state.folder==="__waiting__"?" on":""}" data-folder="__waiting__">
      <span class="ic2">${ico("hourglass")}</span><span class="fn">Waiting</span>
      <span class="unread">${state.waitingCount}</span></div>`:"";
  const rlRow=state.replylaterCount?`<div class="frow${state.folder==="__replylater__"?" on":""}" data-folder="__replylater__">
      <span class="ic2">${ico("bookmark")}</span><span class="fn">Reply later</span>
      <span class="unread">${state.replylaterCount}</span></div>`:"";
  const scrRow=state.screenerCount?`<div class="frow${state.folder==="__screener__"?" on":""}" data-folder="__screener__">
      <span class="ic2">${ico("gate")}</span><span class="fn">Screener</span>
      <span class="unread">${state.screenerCount}</span></div>`:"";
  const vrows=(state.views||[]).map(v=>
    `<div class="frow${state.viewId===v.id?" on":""}" data-view="${esc(v.id)}">
      <span class="ic2">${ico(v.id.startsWith("__lane_")?"tag":"search")}</span><span class="fn">${esc(v.name)}</span>
      ${v.builtin?"":`<button class="vdel" data-vdel="${esc(v.id)}" title="Remove view" aria-label="Remove view">×</button>`}</div>`).join("");
  const vsec=vrows?`<div class="frail-acct">Views</div>${vrows}`:"";
  $("#folderrail").innerHTML=rows+vsec+scrRow+rlRow+waitRow+snzRow+mutRow;
}
function selectFolder(name){ state.allInbox=false; state.folder=name; state.csel=null; state.viewId=null; clearSearch(false); renderMonos(); renderFolders(); loadCMessages(); }
/* ---------- saved searches / lanes ("Views") ---------- */
async function loadViews(){ try{ const d=await api("/api/views"); state.views=d.views||[]; renderFolders(); }catch(e){} }
function selectView(id){
  const v=(state.views||[]).find(x=>x.id===id); if(!v)return;
  let q=v.q;
  if(v.dynamic==="last7"||q==="__LAST7__"){ const d=new Date(Date.now()-7*864e5); q="after:"+d.toISOString().slice(0,10); }
  state.viewId=id; state.q=q; $("#search").value=q; runSearch(); renderFolders();
}
async function saveCurrentSearch(){
  const q=(state.q||"").trim(); if(!q){ toast("Type a search first"); return; }
  const name=(prompt("Name this view:", q.slice(0,40))||"").trim(); if(!name)return;
  try{ const d=await cpost("/api/views",{verb:"create",name,q}); if(!d.ok){ toast(d.msg||"Couldn’t save"); return; }
    state.views=d.views||state.views; const v=state.views.find(x=>x.name===name&&x.q===q); if(v)state.viewId=v.id;
    renderFolders(); toast("Saved view · "+name); }
  catch(e){ toast("Couldn’t save view"); }
}
async function deleteView(id){
  try{ const d=await cpost("/api/views",{verb:"delete",id}); state.views=d.views||state.views;
    if(state.viewId===id)state.viewId=null; renderFolders(); toast("View removed"); }
  catch(e){ toast("Couldn’t remove view"); }
}
async function loadCMessages(){
  if(state.folder==="__screener__"){ renderScreener(); return; }   // the Screener is a sender-tray, not a message list
  const seq=++state.cseq, acct=state.account, fol=state.folder;
  listTitle(folderTitle(fol)); $("#clientCtx").innerHTML="";
  $("#count").textContent="loading…";
  const grp=state.threadView?"&group=thread":"";
  const fol0=fol;
  let d; try{ d=await api(`/api/messages?account=${encodeURIComponent(acct)}&folder=${encodeURIComponent(fol)}&limit=200${grp}`); }
  catch(e){ if(seq===state.cseq){ $("#rows").innerHTML='<div class="nores">Couldn’t load messages. <button class="ai-rerun" id="cmRetry">Retry</button></div>'; const b=$("#cmRetry"); if(b)b.onclick=()=>{state.folder=fol0;loadCMessages();}; } return; }
  if(seq!==state.cseq||acct!==state.account||fol!==state.folder)return;  // superseded
  state.cmsgs=d.items||[]; state.cHasMore=!!d.has_more; state.cmarked.clear();
  state.cSyncing=!!d.syncing; state.cSyncFailed=!!d.sync_failed;   // a first scan still reading, or one that failed
  state.cCatching=!!d.catching_up;                                  // a folder read before, still checking for more
  if(state.csel!=null&&draftsOnScreen()&&!state.cmsgs.some(m=>m.pk===state.csel))state.csel=null;   // that copy was replaced or discarded
  renderClientList();
  if((state.folder||"").toUpperCase()==="INBOX"){ touchLastLook(); clearNewPill(); }   // looking at the inbox now → reset watermark + drop the stale "N new" pill
  // desktop: preview the first message in the reader pane. Mobile (single pane): stay on the list.
  // Only touch the reader when nothing is already selected — a deep-link (docOpen / contact-card) sets
  // state.csel synchronously before this async load resolves; clobbering it would show the wrong email.
  if(state.csel==null){
    if(briefHere()) showBrief(false);   // the Inbox opens on today's brief, not its first message
    else if(listInView().length && !isMobile()) openConversation(listInView()[0],false);
    else if(state.cmsgs.length){ document.body.classList.remove("reading"); }
    else { state.thread=null; const r=$("#reader"); r.className="reader empty-pane"; r.textContent=state.cSyncing?"Reading this folder for the first time…":"No messages in this folder."; document.body.classList.remove("reading"); }
  }
}
const avHue=a=>{let h=0;for(const c of(a||""))h=(h*31+c.charCodeAt(0))%360;return h;};   // full wheel at low saturation — amber stays the needs-you signal
function initialsOf(name,addr){
  const all=/@/.test(name||"")?[]:(String(name||"").match(/\p{L}[\p{L}\p{M}]*/gu)||[]);
  const caps=all.filter(x=>x[0]!==x[0].toLowerCase()), w=caps.length>=2?caps:all;   // "Library of Ash Hollow" → LA, not LO
  return (w.length?w.slice(0,2).map(x=>x[0]).join(""):(String(addr||name||"?").match(/\p{L}|\d/u)||["?"])[0]).toUpperCase();
}
function rowMarkup(it,sub){const m=state.cmarked.has(it.pk),n=it.count||1;
  const needs=!!String(it.action||"").trim(), gist=it.summary||"";
  const what=convoRow(it)?" conversation":"";   // a conversation row archives/deletes every message in it, server-side
  const acts=`<button data-racts="archive" tabindex="-1" title="Archive${what} (e)" aria-label="Archive${what}">${ico("archive")}</button><button class="del" data-racts="trash" tabindex="-1" title="Delete${what} (d)" aria-label="Delete${what}">${ico("trash")}</button>`
    +`<button data-racts="snooze" tabindex="-1" title="Snooze (h)" aria-label="Snooze">${ico("clock")}</button>`;
  return `<div class="row cmsg${it.seen?"":" unread"}${needs?" needs":""}${it.pk===state.csel?" sel":""}${m?" marked":""}" data-pk="${it.pk}"${it.thread_id?` data-tid="${esc(it.thread_id)}"`:""}>
    <div class="avwrap"><span class="av" style="--h:${avHue(it.from_addr||it.from_name)}" aria-hidden="true">${esc(initialsOf(it.from_name,it.from_addr))}</span>
      <button class="cbx${m?" on":""}" data-mark="${it.pk}" aria-label="Select" aria-pressed="${m}" title="Select (x)">${m?"✓":""}</button></div>
    <div class="cmsg-body">
      <div class="r1"><span class="from">${esc(it.from_name)}</span>
        ${n>1?`<span class="thr-count" title="${n} messages">${n}</span>`:""}
        ${it.flagged?`<span class="rflag" style="color:var(--gold)">${ico("star","fill")}</span>`:""}${it.has_attach?`<span class="rflag" style="color:var(--mute)">${ico("clip")}</span>`:""}
        ${it.is_junk?'<span class="tags"><span class="jb">'+esc((it.bucket||"junk").split("/")[0])+'</span></span>':""}
        ${sub?`<span class="acctchip">${esc(sub)}</span>`:""}
        <span class="when">${esc(it.waiting_label||it.when)}</span></div>
      <div class="r2sub">${it.seen?"":'<span class="visually-hidden">Unread. </span>'}<span class="sj">${esc(it.subject)}</span></div>
      ${gist?`<div class="gist"><span class="visually-hidden">Summary: </span>${esc(gist)}</div>`:`<div class="pv">${esc(it.snippet||"")}</div>`}
      ${needs?`<div class="r4"><span class="rchip need" title="${esc(it.action)}"><span class="visually-hidden">Next step: </span>${esc(it.action)}</span></div>`:""}
    </div>
    <div class="racts">${acts}</div></div>`;}
function _dayGroup(ts){   // list section for a message time (epoch seconds), by local calendar day
  const t=new Date(); t.setHours(0,0,0,0); const d=new Date(ts*1000); d.setHours(0,0,0,0);
  const days=Math.round((t-d)/86400000);
  return days<=0?"Today":days===1?"Yesterday":days<7?"Past week":"Earlier";
}
const FOLDER_TITLE={__snoozed__:"Snoozed",__muted__:"Muted",__waiting__:"Waiting",__replylater__:"Reply later",__screener__:"Screener"};
function folderTitle(name){   // as the rail names it
  const f=(state.folders||[]).find(x=>x.name===name);
  return FOLDER_TITLE[name]||(!f?name:roleLabel(f));
}
function listTitle(t){ $("#listTitle").textContent=t; }
const INBOX_TABS=[["needs","Needs you"],["all","Everything"],["unread","Unread"]];
state.inboxTab=(()=>{ try{ const t=localStorage.getItem("mm_inbox_tab"); return INBOX_TABS.some(x=>x[0]===t)?t:"needs"; }catch(e){ return "needs"; } })();
function tabFilter(v){   // the Inbox's tab; a message you just opened stays put until you move on
  if(!briefHere())return v;
  return state.inboxTab==="needs"?v.filter(it=>needsIt(it)||it.pk===state.csel)
    :state.inboxTab==="unread"?v.filter(it=>!it.seen||it.pk===state.csel):v;
}
function listInView(){ return state.searching?state.searchItems:tabFilter(state.cmsgs); }   // what j/k and "next" walk
function setInboxTab(t){
  state.inboxTab=t; try{ localStorage.setItem("mm_inbox_tab",t); }catch(e){}
  renderClientList(); const b=$(`#lhTabs [data-tab="${t}"]`); if(b)b.focus();
}
function renderTabs(v,more){
  const el=$("#lhTabs"); el.hidden=!briefHere(); if(el.hidden)return;
  const n={needs:v.filter(needsIt).length, all:v.length+more, unread:v.filter(it=>!it.seen).length};
  el.innerHTML=INBOX_TABS.map(([id,label])=>{ const on=state.inboxTab===id;
    return `<button class="lh-tab" role="tab" id="tab-${id}" data-tab="${id}" aria-selected="${on}" aria-controls="rows" tabindex="${on?0:-1}">${label}<span class="n${id==="needs"&&n.needs?" sun":""}">${n[id]}</span></button>`; }).join("");
}
function renderClientList(){
  if(state.searching){ renderSearchList(); return; }
  const q=state.q.toLowerCase();
  let v=state.cmsgs;
  if(q) v=v.filter(it=>(it.from_name+" "+it.from_addr+" "+it.subject+" "+it.snippet).toLowerCase().includes(q));
  const noun=state.threadView?"conversation":"message";
  const more=(state.cHasMore&&!q)?"+":"";   // server has more than the loaded page
  $("#count").textContent=`${v.length}${more} ${noun}${v.length===1&&!more?"":"s"}`+(state.cSyncing?" · reading the rest…":"");
  renderTabs(v,more); v=tabFilter(v);
  renderBulk();
  const br=briefHere()&&!q?briefRowHtml():"";
  if(!v.length){ $("#rows").innerHTML=br+(briefHere()&&state.inboxTab!=="all"&&state.cmsgs.length
      ?`<div class="nores">${state.inboxTab==="needs"?"Nothing needs you.":"Nothing unread."} Everything else is in <button class="linkbtn" data-tab="all">Everything</button>.</div>`
      :state.cSyncing?'<div class="nores" role="status">Reading this folder for the first time…</div>'
      :state.cSyncFailed?'<div class="nores" role="status">Couldn’t sync this folder. <button class="ai-rerun" data-resync>Retry</button></div>'
      :'<div class="nores">Nothing here.</div>'); return; }
  // day sections only for real folders listed newest-first (virtual folders sort by their own clock)
  const byDay=!(state.folder||"").startsWith("__") && v.every((it,i)=>it.ts && (i===0||v[i-1].ts>=it.ts));
  let grp="";
  $("#rows").innerHTML=br+v.map(it=>{
    let h=""; if(byDay){ const g=_dayGroup(it.ts); if(g!==grp){ grp=g; h=`<div class="rgrp" role="presentation">${g}</div>`; } }
    return h+rowMarkup(it, state.allInbox?(it.alias||short(it.account)):""); }).join("")
    +((state.cHasMore&&!q)?'<button class="load-more" data-loadmore="list">Load 200 more</button>':"");
}
async function loadMoreClient(){
  const acct=state.account, fol=state.folder, off=state.cmsgs.length;
  const grp=state.threadView?"&group=thread":"";
  let d; try{ d=await api(`/api/messages?account=${encodeURIComponent(acct)}&folder=${encodeURIComponent(fol)}&limit=200&offset=${off}${grp}`); }
  catch(e){ toast("Couldn’t load more"); return; }
  if(acct!==state.account||fol!==state.folder)return;
  state.cmsgs=state.cmsgs.concat(d.items||[]); state.cHasMore=!!d.has_more; renderClientList();
}
async function loadMoreSearch(){
  const off=state.searchItems.length;
  let d; try{ d=await api(`/api/search?q=${encodeURIComponent(state.q)}&scope=${state.searchScope}&account=${encodeURIComponent(state.account)}&limit=200&offset=${off}`); }
  catch(e){ toast("Couldn’t load more"); return; }
  if(!d.ok)return; state.searchItems=state.searchItems.concat(d.items||[]); state.searchHasMore=!!d.has_more; renderSearchList();
}
function setThreadView(on){ state.threadView=on; state.csel=null;
  try{localStorage.setItem("mm_threadview",on?"1":"0");}catch(e){}
  const b=$("#threadToggle"); if(b){b.classList.toggle("on",on); b.innerHTML=on?ico("chat")+" Conversations":ico("list")+" Messages";}
  loadCMessages(); }
function convoRow(it){   // a list row that stands for a whole conversation (it opens in the thread reader)
  return !!(it && !state.searching && state.threadView && (it.count||1)>1 && it.thread_id
    && !draftsOnScreen());   // a draft's older copies are no conversation: the newest opens alone, with its Edit
}
function openConversation(it,scroll){
  if(it){ announce(`${it.seen?"":"unread, "}${it.from_name||it.from_addr||""}: ${it.subject||""}`); }
  if(state.searching) return openSearchHit(it.pk);
  if(convoRow(it)) return openThread(it.thread_id, it.pk, scroll, it.account||state.account);
  openClientMsg(it.pk,scroll);
}
/* ---- unified All inboxes (cross-account INBOX merge) ---- */
function enterAllInbox(){
  state.allInbox=true; state.csel=null; renderMonos();
  $("#folderrail").innerHTML=`<div class="frow on"><span class="ic2">${ico("inbox")}</span><span class="fn">Inbox</span></div>`;
  loadAllInboxes();
}
async function loadAllInboxes(){
  const seq=++state.cseq;
  listTitle("Inbox"); $("#clientCtx").innerHTML="";
  $("#count").textContent="loading…";
  const grp=state.threadView?"&group=thread":"";
  const accts=state.sendAccounts||state.accountsList||[];
  const results=await Promise.all(accts.map(a=>
    api(`/api/messages?account=${encodeURIComponent(a)}&folder=INBOX&limit=40${grp}`).catch(()=>({items:[]}))));
  if(seq!==state.cseq||!state.allInbox)return;   // superseded
  const merged=[].concat(...results.map(r=>r.items||[])).sort((a,b)=>(b.ts||0)-(a.ts||0));
  state.cmsgs=merged; state.cHasMore=false; state.cmarked.clear();
  renderClientList();
  touchLastLook(); clearNewPill();   // viewing all inboxes → reset every account's watermark + drop the stale pill
  if(state.csel==null) showBrief(false);   // a reload under an open message (undo) leaves it open
}
/* ---- multi-select + bulk actions ---- */
function cmarkToggle(pk){ if(state.cmarked.has(pk))state.cmarked.delete(pk); else state.cmarked.add(pk); refreshRow(pk); renderBulk(); }
function cmarkClear(){ state.cmarked.clear(); renderClientList(); }
function renderBulk(){
  const bar=$("#bulkbar"), n=state.cmarked.size;
  if(!n){ bar.hidden=true; bar.innerHTML=""; return; }
  bar.hidden=false;
  bar.innerHTML=`<span class="bcount">${n} selected</span>
    <button class="bb" data-bulk="archive">${ico("archive")} Archive</button>
    <button class="bb bb-del" data-bulk="trash">${ico("trash")} Delete</button>
    <button class="bb" data-bulk="read">Mark read</button>
    <button class="bb" data-bulk="unread">Mark unread</button>
    <button class="bb" data-bulk="star">${ico("star")} Star</button>
    <button class="bb bb-del" data-bulk="junk">${ico("ban")} Junk</button>
    <button class="bb" data-bulk="snooze">${ico("clock")} Snooze</button>
    <button class="bb" data-bulk="mute">${ico("bell-off")} Mute</button>
    <button class="bclear" data-bulk="clear">Clear</button>`;
}
async function cbulk(action, anchor){
  const pks=[...state.cmarked]; if(!pks.length)return;
  if(action==="clear")return cmarkClear();
  if(action==="snooze"){ openSnoozeMenu(anchor||$("#bulkbar"), {pks}); return; }   // preset menu, not a prompt
  if(action==="mute"){   // mute each distinct selected thread under ONE undo group (one z reverses all)
    // resolve the account PER thread — in "All inboxes" the selection spans accounts, so a single
    // state.account would mute the WRONG account's thread (silent wrong-target mutation). Bug fix.
    const pairs=[...new Map(pks.map(p=>{const it=cfindAny(p);return it&&it.thread_id?[it.account+"\u0000"+it.thread_id,{account:it.account||state.account,thread_id:it.thread_id}]:null;}).filter(Boolean)).values()];
    const grp=undoGroup();
    for(const x of pks){ const it=cfindAny(x); if(it && it.thread_id){ state.cmsgs=state.cmsgs.filter(r=>r.thread_id!==it.thread_id); state.searchItems=(state.searchItems||[]).filter(r=>r.thread_id!==it.thread_id); } }
    if(state.cmarked.has(state.csel))state.csel=null;
    state.cmarked.clear(); renderClientList();
    if(state.csel==null){ const first=(state.searching?state.searchItems:state.cmsgs)[0]; if(first)openClientMsg(first.pk,false); else clearReader(); }
    let ok=0;
    for(const {account,thread_id} of pairs){ try{ const r=await cpost("/api/mute",{account,thread_id,undo_group:grp}); if(r.ok)ok++; }catch(e){} }
    toast(`Muted ${ok} thread${ok===1?"":"s"}`); if(ok)showUndoFloat(`Muted ${ok}`); loadFolders();
    return;
  }
  if(action==="archive"||action==="trash"||action==="junk"){
    const path={archive:"/api/archive",trash:"/api/trash",junk:"/api/junk"}[action];
    const verb={archive:"Archived",trash:"Trashed",junk:"Marked junk"}[action];
    const paths=new Map(pks.map(p=>[p, action!=="junk"&&convoRow(cfindAny(p))?"/api/thread/"+action:path]));   // a conversation row files whole; decide while the rows are still listed
    state.cmsgs=state.cmsgs.filter(x=>!state.cmarked.has(x.pk));
    state.searchItems=(state.searchItems||[]).filter(x=>!state.cmarked.has(x.pk));
    if(state.cmarked.has(state.csel))state.csel=null;
    state.cmarked.clear(); renderClientList();
    if(state.csel==null){ const first=(state.searching?state.searchItems:state.cmsgs)[0]; if(first)openClientMsg(first.pk,false); else clearReader(); }
    const grp=undoGroup(); let fail=0, undoable=false, partial=false;
    for(const pk of pks){ try{ const r=await cpost(paths.get(pk),{pk,undo_group:grp}); if(!r.ok)fail++;
      else{ if(r.undoable)undoable=true; if(r.failed)partial=true; if(r.pks)cForget(r.pks); } }catch(e){ fail++; } }
    toast(fail?`${verb} ${pks.length-fail}, ${fail} failed`:partial?`${verb} ${pks.length} — some messages stayed`:`${verb} ${pks.length}`);
    if(undoable)showUndoFloat(`${verb} ${pks.length-fail}`);
    if(fail||partial)loadCMessages(); return;
  }
  const seen=action==="read"?true:action==="unread"?false:null;  // star -> null
  pks.forEach(pk=>{ const it=cfindAny(pk); if(it){ if(seen!==null)it.seen=seen?1:0; else it.flagged=1; } });
  state.cmarked.clear(); renderClientList();
  let fail=0; for(const pk of pks){ try{
    const r=seen!==null?await cpost("/api/seen",{pk,seen}):await cpost("/api/flag",{pk,flag:"flagged",on:true});
    if(!r.ok)fail++; }catch(e){ fail++; } }
  toast(fail?`Updated ${pks.length-fail}, ${fail} failed`:`Updated ${pks.length}`);
  if(fail)loadCMessages();
}
function cmove(dir){
  const list=listInView(); if(!list.length)return;
  let i=list.findIndex(x=>x.pk===state.csel);
  if(i===0&&dir<0&&briefHere()){ showBrief(true); const b=$("#rows .brief-row"); if(b)b.scrollIntoView({block:"nearest"}); return; }   // above the first message: the brief
  i=i<0?0:Math.max(0,Math.min(list.length-1,i+dir)); openConversation(list[i],true);
}
function cmoveUnread(dir){   // jump to the next/prev UNREAD conversation
  const list=listInView(); if(!list.length)return;
  let i=list.findIndex(x=>x.pk===state.csel);
  for(let j=i+dir; j>=0 && j<list.length; j+=dir){ if(!list[j].seen){ openConversation(list[j],true); return; } }
  toast(dir>0?"No more unread below":"No more unread above");
}
function setLoading(el, label, slow){
  el.innerHTML=`<div class="nores loading" style="padding:40px"><span class="spin">↻</span> <span class="llbl">${esc(label)}</span></div>`;
  setTimeout(()=>{ const n=el.querySelector(".loading .llbl"); if(n&&n.textContent===label)n.textContent=slow; }, 4000);
}
function readerSkeleton(it){
  const s=it||{};
  return `${readerBarSkel()}
    <div class="rd-top"><span class="rd-tag">${esc({__snoozed__:"Snoozed",__muted__:"Muted",__waiting__:"Waiting"}[state.folder]||state.folder)}</span></div>
    <div class="rd-subj">${esc(s.subject||"")}</div>
    <div class="rd-from"><span class="nm">${esc(s.from_name||"")}</span> <span class="ad">${esc(s.from_addr||"")}</span></div>
    <div class="rd-skel"><div class="skb"></div><div class="skb"></div><div class="skb short"></div><div class="skb"></div><div class="skb"></div><div class="skb short"></div></div>
    <div class="rd-skelhint" id="rdSkelHint"></div>`;
}
function readerBarSkel(){   // the action bar while a body loads: navigation only — verbs wait for the loaded message
  return `<div class="rd-actions"><button class="cab rd-back" data-back title="Back to list"><span>${ico("left")}</span> List</button><span class="rd-sp" aria-hidden="true"></span>${readerNavHtml()}</div>`;
}
function readerNavHtml(){   // right end of the reading pane's action bar: focus reading, position, previous / next
  const list=state.searching?state.searchItems:state.cmsgs, n=list.length, i=list.findIndex(x=>x.pk===state.csel);
  const more=(state.searching?state.searchHasMore:state.cHasMore)?"+":"";
  return `<span class="rd-nav">
    <button class="cab rd-focus" data-readfocus aria-pressed="${readFocusOn()}" title="Focus reading — hide folders and list (↵)" aria-label="Focus reading"><span>${ico("expand")}</span></button>
    ${i<0?"":`<span class="rd-pos">${i+1} of ${n}${more}</span>`}
    <button class="cab" data-nav="-1" title="Previous (k)" aria-label="Previous message"${i<=0?" disabled":""}><span>${ico("up")}</span></button>
    <button class="cab" data-nav="1" title="Next (j)" aria-label="Next message"${i<0||i>=n-1?" disabled":""}><span>${ico("down")}</span></button></span>`;
}
/* prefetch: warm the SQLite body cache for the next rows so j/k feels instant */
const _prefetched=new Set(), _prefetchInflight=new Set();
function prefetchBody(pk){   // non-blocking server-side warm; never delays a foreground open
  if(pk==null||_prefetched.has(pk)||_prefetchInflight.has(pk))return;
  _prefetchInflight.add(pk);
  fetch(`/api/message/${pk}/body?prefetch=1`).then(r=>r.json()).then(d=>{ if(d&&d.prefetched)_prefetched.add(pk); }).catch(()=>{}).finally(()=>_prefetchInflight.delete(pk));
}
function prefetchAround(pk){
  const list=state.searching?state.searchItems:state.cmsgs;
  const i=list.findIndex(x=>x.pk===pk); if(i<0)return;
  [list[i+1],list[i+2]].forEach(x=>x&&prefetchBody(x.pk));
}
async function openClientMsg(pk,scroll){
  state.csel=pk; state.thread=null;   // a single message owns the reader now — thread-level actions must not hit the last conversation
  $$(".row.cmsg,#rows .brief-row").forEach(r=>r.classList.toggle("sel",+r.dataset.pk===pk));
  const rd=$("#reader"); rd.className="reader"; document.body.classList.add("reading");
  rd.innerHTML=readerSkeleton(cfindAny(pk));   // paint known subject/from + skeleton instantly
  const slowT=setTimeout(()=>{ const h=$("#rdSkelHint"); if(h)h.innerHTML='<span class="spin">↻</span> Still fetching from the server — the first open of an older message can take up to a minute.'; },4000);
  let d; try{ d=await api(`/api/message/${pk}/body`); }catch(e){ clearTimeout(slowT); if(state.csel===pk)rd.innerHTML='<div class="nores">Couldn’t load. <button class="ai-rerun" id="rdRetry">Retry</button></div>'; const rb=$("#rdRetry"); if(rb)rb.onclick=()=>openClientMsg(pk,false); return; }
  clearTimeout(slowT);
  if(state.csel!==pk)return;   // user moved on while we waited
  if(!d.ok){ rd.innerHTML='<div class="nores">'+esc(d.error||"error")+'</div>'; return; }
  _prefetched.add(pk);
  renderClientReader(d);
  if(scroll){const r=$(`.row.cmsg[data-pk="${pk}"]`); if(r)r.scrollIntoView({block:"nearest"});}
  clearTimeout(_seenTimer);    // dwell: only mark read if it stays open ~1.2s (a fast j/k skim won't burn unread)
  _seenTimer=setTimeout(()=>{ if(state.csel===pk)cmarkseen(pk); }, 1200);
  prefetchAround(pk);          // warm the next 1-2 bodies in the background
}
let _seenTimer=null;
// content-aware default: rich HTML → original sender palette; plain text → dark reading mode.
// A choice under Aa → Mail colors (mm_mailtheme: dark | original; absent = auto) wins for all mail.
function effMailDark(isHtml){ const p=localStorage.getItem("mm_mailtheme");
  if(p) return p!=="original";
  if(document.body.classList.contains("theme-light")) return false;   // paper app → paper mail by default
  return !isHtml; }
/* Reading settings (Aa). Width is the Mailbox reading measure; face and size typeset plain-text
   mail only — rich mail keeps the sender's own type. System stacks: the mail iframe's CSP
   (font-src data:) can't load the app's web fonts. Kept in mm_read; colors stay in mm_mailtheme. */
const READ_W={narrow:640,medium:760,wide:920}, READ_SIZE={s:15,m:16.5,l:18,xl:20};
const READ_FONT={sans:'-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif',
  serif:'"Iowan Old Style",Charter,"Source Serif 4",Georgia,serif', mono:'ui-monospace,"SF Mono",Menlo,Consolas,monospace'};
function readPrefs(){ let p={}, c=null;
  try{ p=JSON.parse(localStorage.getItem("mm_read")||"{}")||{}; c=localStorage.getItem("mm_mailtheme"); }catch(e){}
  return { width:READ_W[p.width]?p.width:"medium", size:READ_SIZE[p.size]?p.size:"m",
           font:READ_FONT[p.font]?p.font:"sans", colors:c==="dark"||c==="original"?c:"auto" }; }
function applyReadWidth(){ document.body.style.setProperty("--measure",READ_W[readPrefs().width]+"px"); }
function themeIsLight(){ return document.body.classList.contains("theme-light"); }
// a theme token's current value, for documents (the mail iframe) that can't see our CSS
function tk(name){ return getComputedStyle(document.body).getPropertyValue(name).trim(); }
const THEMES=["day","dusk","night"], THEME_NAME={day:"Day",dusk:"Dusk",night:"Night"};
function themeName(){ return !themeIsLight()?"night":document.body.classList.contains("theme-dusk")?"dusk":"day"; }
function nextTheme(){ return THEMES[(THEMES.indexOf(themeName())+1)%THEMES.length]; }
function paintThemeControls(){
  const t=themeName();
  $$("#legTheme [data-theme-set]").forEach(b=>b.setAttribute("aria-pressed", b.dataset.themeSet===t));
  const tb=$("#themeBtn"); if(tb)tb.setAttribute("aria-label", `Theme: ${THEME_NAME[t]} — switch to ${THEME_NAME[nextTheme()]}`);
}
function setTheme(t){
  const light=t!=="night";
  document.body.classList.toggle("theme-light", light);
  document.body.classList.toggle("theme-dusk", t==="dusk");
  try{ localStorage.setItem("mm_theme", t==="night"?"dark":t==="dusk"?"dusk":"light"); }catch(e){}
  paintThemeControls();
  // repaint an open message so its iframe polarity follows the app theme (unless the user pinned mail theme)
  if(!localStorage.getItem("mm_mailtheme")){
    if(state.thread) openThread(state.thread.thread_id, state.thread.reply_pk||state.csel, false, state.thread.account);
    else if(state.csel!=null && document.body.classList.contains("reading")) openClientMsg(state.csel,false);
  }
}
function toggleTheme(){ const t=nextTheme(); setTheme(t); toast(THEME_NAME[t]+" theme"); }
function _foldPlainQuote(t){   // t is HTML-escaped plain text; fold a trailing reply quote
  const lines=t.split("\n");
  let idx=-1;
  for(let i=0;i<lines.length;i++){   // an attribution line starts the quote ("On … wrote:", "-----Original Message-----", "From: …")
    if(/^\s*(On\s.+\bwrote:|-{2,}\s*Original Message\s*-{2,}|_{10,}|From:\s)/.test(lines[i])){ idx=i; break; }
  }
  if(idx<0){                         // else a trailing contiguous run of ">"-quoted lines
    let j=lines.length-1; while(j>=0 && !lines[j].trim()) j--;
    if(j>=0 && /^\s*&gt;/.test(lines[j])){ while(j>=0 && (/^\s*&gt;/.test(lines[j])||!lines[j].trim())) j--; idx=j+1; }
  }
  if(idx<=0 || !lines.slice(0,idx).join("").trim()) return null;   // nothing to fold above the quote
  return { head: lines.slice(0,idx).join("\n"), quoted: lines.slice(idx).join("\n") };
}
function bodyDoc(content,isHtml){
  // explicit-deny CSP: every channel stated (not just implied by default-src 'none') so a future
  // edit can't silently re-open one. (The CSP 'sandbox' directive is header-only — ignored via
  // <meta>; the real wall is the iframe sandbox attribute.) no-referrer pins zero Referer leakage.
  const csp='<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'none\'; object-src \'none\'; base-uri \'none\'; form-action \'none\'; frame-src \'none\'; child-src \'none\'; connect-src \'none\'; img-src https: data:; style-src \'unsafe-inline\'; font-src data:;">'
    +'<meta name="referrer" content="no-referrer">';
  const dark=effMailDark(isHtml);
  // folded quote chain (<details class=mm-quote> injected by the sanitizer / plain-text folder)
  const qcss="details.mm-quote{margin:10px 0}details.mm-quote>summary{cursor:pointer;list-style:none;"
    +"color:#8a8a8a;font-size:12px;padding:3px 9px;border:1px solid #8a8a8a66;border-radius:6px;display:inline-block}"
    +"details.mm-quote>summary::-webkit-details-marker{display:none}details.mm-quote[open]>summary{margin-bottom:8px}";
  let style, inner;
  if(isHtml){
    if(dark){
      // Invert the email tree to darken its (usually light) backgrounds; counter-invert media so
      // photos/logos render true. The 180° hue-rotate keeps brand hues roughly themselves.
      style="<style>html,body{background:"+tk("--bg")+"}body{margin:14px;overflow-wrap:break-word}"
        +".mm-dark{filter:invert(.92) hue-rotate(180deg)}"
        +".mm-dark img,.mm-dark picture,.mm-dark video,.mm-dark svg,.mm-dark [style*=background-image]{filter:invert(1) hue-rotate(180deg)}"
        +"img{max-width:100%;height:auto;max-height:8000px}table{max-width:100%}"+qcss+"</style>";
      inner='<div class="mm-dark">'+content+'</div>';
    } else {
      style="<style>body{font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;background:"+(themeIsLight()?tk("--panel"):"#ffffff")+";margin:14px;overflow-wrap:break-word}a{color:#0b66c3}img{max-width:100%;height:auto;max-height:8000px}blockquote{border-left:3px solid #ccc;margin:0 0 0 4px;padding-left:12px;color:#555}table{max-width:100%}"+qcss+"</style>";
      inner=content;
    }
  } else {   // plain text is fully controlled — always typeset cleanly, dark or light
    // themed from the live tokens, so plain text always sits on the app's own paper
    const ink=dark?tk("--ink"):"#24221E", bg=dark?tk("--bg"):"#FFFDF8", lk=dark?tk("--primary-ink"):"#2D4B70", rp=readPrefs();
    style="<style>body{margin:14px;color:"+ink+";background:"+bg+";overflow-wrap:break-word}a{color:"+lk+"}"
      +"pre{white-space:pre-wrap;font:"+READ_SIZE[rp.size]+"px/"+(rp.font==="mono"?1.55:1.62)+" "+READ_FONT[rp.font]+";margin:0}"
      +"blockquote{border-left:3px solid "+(dark?tk("--line-hi"):"#D5C9B1")+";margin:0 0 0 4px;padding-left:12px;color:"+(dark?tk("--mute"):"#625B4D")+"}"+qcss+"</style>";
    const f=_foldPlainQuote(content);
    inner = f ? '<pre>'+f.head+'</pre><details class="mm-quote"><summary>··· quoted text</summary><pre>'+f.quoted+'</pre></details>'
              : '<pre>'+content+'</pre>';
  }
  return '<!doctype html><html lang="en"><head><meta charset=utf-8><base target="_blank">'+csp+style+'</head><body>'+inner+'</body></html>';
}
function mailDoc(d){ return d.has_html?bodyDoc(d.html||"",true):bodyDoc(esc(d.text||"(no content)"),false); }   // a body payload → its iframe document
// A persistent, parent-rendered "external email" strip + protection shield. Because it lives in the
// app DOM (not the sandboxed iframe), a malicious body can neither overlay nor erase it — the
// red-team's mitigation for in-body chrome-spoofing (now that we allow rich CSS).
function extBar(d){
  const nblk=(d.blocked_images||0)+(d.blocked_css_urls||0);
  const dom=((d.from_addr||"").split("@")[1]||"").toLowerCase();
  const shield=`${ico("shield")} Sandboxed${nblk?` · ${nblk} tracker${nblk>1?"s":""} blocked`:" · no active content"}`;
  return `<div class="rd-extbar"><span class="ext">External${dom?" · "+esc(dom):""}</span><span class="shield">${shield}</span></div>`;
}
// Links in a sandboxed body are inert (no allow-popups). The parent (same-origin) intercepts clicks,
// warns on a domain/text mismatch or punycode look-alike, then opens externally with no referrer/opener.
function _reg(h){ return ((h||"").split(".")).slice(-2).join("."); }   // coarse registrable domain
function _linkHosts(href){
  // the href's OWN host + the host of any embedded http(s):// URL (legit ESP redirectors put it there)
  const hosts=[]; try{ hosts.push(new URL(href).hostname.replace(/^www\./,"").toLowerCase()); }catch(e){}
  let dec=href; try{ dec=decodeURIComponent(href); }catch(e){}
  const re=/https?:\/\/([a-z0-9.-]+)/ig; let m;
  while((m=re.exec(dec))){ hosts.push(m[1].replace(/^www\./,"").toLowerCase()); }
  return hosts;
}
function _hostHas(claimed, hosts){ return hosts.some(h=>h===claimed||h.endsWith("."+claimed)||claimed.endsWith("."+h)); }
const _SENSITIVE=/\b(sign[ -]?in|log[ -]?in|verify|confirm|account|password|reset|unlock|billing|invoice|payment|suspend)\b/i;
function _linkRisk(a, senderDom){
  const href=a.getAttribute("href")||""; if(!/^https?:/i.test(href)) return null;
  let host=""; try{ host=new URL(href).hostname.replace(/^www\./,"").toLowerCase(); }catch(e){ return null; }
  if(/(^|\.)xn--/i.test(host)) return {reason:`a non-Latin “punycode” domain (${host}), often used to imitate a real site`};
  const text=(a.textContent||"").trim();
  const m=text.match(/\b([a-z0-9-]+\.[a-z]{2,}(?:\.[a-z]{2,})?)\b/i);   // does the link TEXT name a domain?
  const claimed=m?m[1].toLowerCase().replace(/^www\./,""):null;
  if(claimed){
    // suppress the mismatch warning ONLY when the claimed brand is an ACTUAL host in the URL (a real
    // redirect TO it) — not a mere substring an attacker can plant in a path/query/subdomain.
    if(!_hostHas(claimed,_linkHosts(href)))
      return {reason:`a different domain (${host}) than the link text suggests (${claimed})`};
    return null;
  }
  // no domain in the text → flag a sensitive "sign in / verify / account" link that leaves the sender's domain
  if(senderDom && _SENSITIVE.test(text) && _reg(host)!==_reg(senderDom.toLowerCase()))
    return {reason:`an unfamiliar domain (${host}) — the text looks like a sign-in/account action but the sender is ${esc(senderDom)}`};
  return null;
}
function wireMailLinks(fr, senderDom){
  let doc; try{ doc=fr.contentWindow.document; }catch(e){ return; }   // needs allow-same-origin
  const handler=e=>{
    const a=e.target.closest&&e.target.closest("a[href]"); if(!a)return;
    e.preventDefault();
    const href=a.getAttribute("href")||"";
    if(/^mailto:/i.test(href)){ window.open(href); return; }
    if(!/^https?:/i.test(href)){ toast("This link can’t be opened"); return; }   // cid:/unknown → feedback, not silent
    const risk=_linkRisk(a, senderDom);
    if(risk && !confirm(`⚠ Careful — this link points to ${risk.reason}.\n\nOpen ${href} anyway?`)) return;
    window.open(href,"_blank","noopener,noreferrer");
  };
  doc.addEventListener("click",handler,true);
  doc.addEventListener("auxclick",handler,true);   // middle-click / open-in-new-tab also routed through the check
}
// SECURITY INVARIANT (load-bearing): reader iframes use sandbox="allow-same-origin" WITHOUT allow-scripts.
// Same-origin is safe ONLY because scripts can't run (no allow-scripts AND CSP script-src 'none'); if a
// future edit ever adds allow-scripts, the email could read the parent origin. This sentinel fails closed —
// it strips allow-scripts off any reader iframe the instant it appears. (Attributes-only → cheap.)
(function(){ try{
  new MutationObserver(ms=>{ for(const m of ms){ const fr=m.target;
    if(fr.tagName==="IFRAME" && fr.classList.contains("rd-iframe") && (fr.getAttribute("sandbox")||"").split(/\s+/).includes("allow-scripts")){
      fr.setAttribute("sandbox","allow-same-origin"); console.error("SECURITY: stripped allow-scripts from a reader iframe"); } } })
    .observe(document.documentElement,{subtree:true,attributes:true,attributeFilter:["sandbox"]});
}catch(e){} })();
// re-typeset the open mail from the payloads its frames keep (fr._mail): no refetch, and the
// rest of the reader — an AI summary, an expanded conversation — stays as it is
function rerenderMail(plainOnly){
  $$("#reader iframe.rd-iframe").forEach(fr=>{ const d=fr._mail; if(d&&!(plainOnly&&d.has_html))fr.srcdoc=mailDoc(d); });
}
function setReadPref(k,v){
  const p=readPrefs(); if(p[k]===v)return; p[k]=v;
  try{ if(k==="colors"){ if(v==="auto")localStorage.removeItem("mm_mailtheme"); else localStorage.setItem("mm_mailtheme",v); }
       else localStorage.setItem("mm_read",JSON.stringify({width:p.width,size:p.size,font:p.font})); }catch(e){}
  if(k==="width")applyReadWidth(); else rerenderMail(k!=="colors");
  $$(`#readMenu [data-rs="${k}"]`).forEach(b=>{ const on=b.dataset.v===v; b.classList.toggle("on",on); b.setAttribute("aria-pressed",on); });
}
const READ_ROWS=[["width","Column width",[["narrow","Narrow"],["medium","Medium"],["wide","Wide"]]],
  ["font","Plain-text face",[["sans","Sans"],["serif","Serif"],["mono","Mono"]]],
  ["size","Plain-text size",[["s","A","Small"],["m","A","Medium"],["l","A","Large"],["xl","A","Extra large"]]],
  ["colors","Mail colors",[["auto","Auto"],["dark","Dark"],["original","Original"]]]];
function openReadMenu(anchor){
  closeReadMenu(); closeSnoozeMenu(); anchor=visibleAnchor(anchor);
  const p=readPrefs(), m=document.createElement("div");
  m.className="read-menu"; m.id="readMenu"; m.setAttribute("role","dialog"); m.setAttribute("aria-labelledby","readMenuH");
  m.innerHTML='<h3 id="readMenuH">Reading settings</h3>'
    +READ_ROWS.map(([k,lab,opts])=>`<div class="rs-row"><span class="rs-lab" id="rs-${k}">${lab}</span><div class="seg" role="group" aria-labelledby="rs-${k}">`
      +opts.map(([v,t,al])=>`<button type="button" data-rs="${k}" data-v="${v}" class="${p[k]===v?"on":""}${k==="size"?" rs-sz-"+v:""}" aria-pressed="${p[k]===v}"${al?` aria-label="${al}"`:""}>${t}</button>`).join("")
      +"</div></div>").join("")
    +'<p class="rs-note">Face and size apply to plain-text mail; rich mail keeps the sender\u2019s own type. Auto shows rich mail in its own colors and plain text in the app\u2019s.</p>';
  m.querySelectorAll('[data-rs="font"]').forEach(b=>{ b.style.fontFamily=READ_FONT[b.dataset.v]; });
  document.body.appendChild(m);
  const r=anchor.getBoundingClientRect();
  m.style.top=(r.bottom+6)+"px"; m.style.left=Math.max(10,Math.min(r.right-m.offsetWidth,window.innerWidth-m.offsetWidth-10))+"px";
  anchor.setAttribute("aria-expanded","true");
  (m.querySelector("button.on")||m.querySelector("button")).focus();
  m.addEventListener("click",e=>{ e.stopPropagation(); const b=e.target.closest("[data-rs]"); if(b)setReadPref(b.dataset.rs,b.dataset.v); });
  setTimeout(()=>{ if(m.isConnected)document.addEventListener("click",_readOutside); },0);   // after the opening click finishes
  window.addEventListener("blur",_readBlur);
}
function _readOutside(e){ if(!e.target.closest("#readMenu"))closeReadMenu(); }
// a click in a mail body never reaches this document: the frame takes focus and the window blurs.
// Other blurs (another app, a native picker) leave activeElement where it was, and the menu open.
function frameTookFocus(){ const a=document.activeElement; return !!a&&a.tagName==="IFRAME"; }
function _readBlur(){ setTimeout(()=>{ if(frameTookFocus())closeReadMenu(); },0); }
function closeReadMenu(refocus){
  const m=$("#readMenu"); if(!m)return;
  m.remove(); document.removeEventListener("click",_readOutside); window.removeEventListener("blur",_readBlur);
  const b=$("#reader [data-readset]"); if(b){ b.setAttribute("aria-expanded","false"); if(refocus)visibleAnchor(b).focus(); }
}
/* ⋯ More: the verbs this width's @container pane tier hid, in bar order. Picking one clicks the
   hidden button itself, so each verb keeps a single code path. */
function visibleAnchor(el){ return el&&el.getClientRects().length?el:($("#reader [data-more]")||el); }   // a hidden verb's menu opens under ⋯
function openMoreMenu(anchor){
  closeMoreMenu(); closeReadMenu(); closeSnoozeMenu();
  const hidden=[...anchor.closest(".rd-actions").querySelectorAll(".cab[data-ca],.cab[data-tca],.cab[data-readset]")].filter(b=>!b.getClientRects().length);
  if(!hidden.length)return;
  const m=document.createElement("div"); m.className="snooze-menu"; m.id="moreMenu"; m.setAttribute("role","menu"); m.setAttribute("aria-label","More actions");
  m.innerHTML=hidden.map((b,i)=>{ const ic=b.querySelector("span"), icon=ic?ic.innerHTML:"";   // our own markup: an ico() or the Aa label
    const name=(b.getAttribute("aria-label")||b.textContent.replace(ic?ic.textContent:"","")).replace(/\s+/g," ").trim();
    return `<button role="menuitem" data-mi="${i}"><span class="mi-ic" aria-hidden="true">${icon}</span> ${esc(name)}</button>`; }).join("");
  document.body.appendChild(m);
  const r=anchor.getBoundingClientRect();
  m.style.top=(r.bottom+4)+"px"; m.style.left=Math.max(10,Math.min(r.right-m.offsetWidth,window.innerWidth-m.offsetWidth-10))+"px";
  anchor.setAttribute("aria-expanded","true");
  m.querySelector("button").focus();
  m.addEventListener("click",e=>{ e.stopPropagation(); const it=e.target.closest("[data-mi]"); if(!it)return; closeMoreMenu(); hidden[+it.dataset.mi].click(); });
  setTimeout(()=>{ if(m.isConnected)document.addEventListener("click",_moreOutside); },0);   // after the opening click finishes
  window.addEventListener("blur",_moreBlur);
}
function _moreOutside(e){ if(!e.target.closest("#moreMenu"))closeMoreMenu(); }
function _moreBlur(){ setTimeout(()=>{ if(frameTookFocus())closeMoreMenu(); },0); }
function closeMoreMenu(refocus){
  const m=$("#moreMenu"); if(!m)return;
  m.remove(); document.removeEventListener("click",_moreOutside); window.removeEventListener("blur",_moreBlur);
  const b=$("#reader [data-more]"); if(b){ b.setAttribute("aria-expanded","false"); if(refocus)b.focus(); }
}
function fmtSize(n){ if(!n)return""; if(n<1024)return n+" B"; if(n<1048576)return (n/1024).toFixed(0)+" KB"; return (n/1048576).toFixed(1)+" MB"; }
/* A mail frame is sized to its content on load; a narrower or wider column reflows that content
   (splitter drag, window resize, column width), so refit whenever the frame's width changes. */
const _frameRO=window.ResizeObserver?new ResizeObserver(es=>{ for(const en of es){
  const fr=en.target, w=Math.round(en.contentRect.width); if(fr._fitW!==w){ fr._fitW=w; fitMailFrame(fr); } } }):null;
const _fitFrames=new Set();
function fitMailFrame(fr){ try{ fr.style.height=Math.min((fr.contentWindow.document.body.scrollHeight||fr._fitMin)+fr._fitPad,5000)+"px"; }catch(e){} }
function sizeMailFrame(fr,min,pad){   // call from the frame's load handler
  fr._fitMin=min; fr._fitPad=pad; fitMailFrame(fr);
  if(!_frameRO)return;
  for(const f of _fitFrames) if(!f.isConnected){ _frameRO.unobserve(f); _fitFrames.delete(f); }   // frames from re-rendered readers
  fr._fitW=Math.round(fr.getBoundingClientRect().width); _fitFrames.add(fr); _frameRO.observe(fr);
}
function renderClientReader(d){
  state.lastBody=d;            // keep the full payload for reply/forward recipient math
  const rd=$("#reader");
  const _nblk=(d.blocked_images||0)+(d.blocked_css_urls||0);
  const imgbar=_nblk?`<div class="rd-imgbar">${ico("shield")} ${_nblk} remote image${_nblk>1?"s":""} hidden — loading one can let the sender know you opened this email <button data-loadimg="${d.pk}">Load images</button></div>`:"";
  const atts=(d.attachments||[]).filter(a=>!(a.is_inline&&a.content_id));
  const attbar=atts.length?'<div class="rd-attach">'+atts.map(a=>attChip(d.pk,a)).join("")+'</div>':"";
  const doc=mailDoc(d);
  const it=cfind(d.pk)||{};
  const isDraft=d.is_draft;
  const actions=isDraft?`<div class="rd-actions">
    <button class="cab rd-back" data-back title="Back to list"><span>${ico("left")}</span> List</button>
    <button class="cab" data-ca="edit" title="Edit draft"><span>${ico("pen")}</span> Edit</button>
    <button class="cab cab-del" data-ca="trash" title="Delete (d / ⌫ / #)"><span>${ico("trash")}</span> Delete</button>
    <span class="rd-sp" aria-hidden="true"></span>${readerNavHtml()}</div>`
   :`<div class="rd-actions">
    <button class="cab rd-back" data-back title="Back to list"><span>${ico("left")}</span> List</button>
    ${replyChipHtml(d.pk)}
    <button class="cab" data-ca="replyall" title="Reply all (a)" aria-label="Reply all"><span>${ico("replyall")}</span> All</button>
    <button class="cab" data-ca="forward" title="Forward (f)"><span>${ico("forward")}</span> Forward</button>
    <button class="cab" data-ca="summarize" title="AI summary (i)"><span>${ico("wind")}</span> Summarize</button>
    <button class="cab" data-ca="snooze" title="Snooze (h)"><span>${ico("clock")}</span> Snooze</button>
    ${state.folder==='__replylater__'
      ?'<button class="cab" data-ca="rldone" title="Done — remove from Reply later"><span>'+ico("check")+'</span> Done</button>'
      :'<button class="cab" data-ca="replylater" title="Reply later (l)" aria-label="Reply later"><span>'+ico("bookmark")+'</span> Later</button>'}
    <button class="cab" data-ca="${state.folder==='__muted__'?'unmute':'mute'}" title="Mute thread (w)"><span>${ico("bell-off")}</span> ${state.folder==='__muted__'?'Unmute':'Mute'}</button>
    ${state.folder==='__waiting__'?'<button class="cab" data-ca="dismiss" title="Dismiss follow-up"><span>'+ico("check")+'</span> Done</button>':''}
    <span class="rd-sp" aria-hidden="true"></span>
    <button class="cab" data-ca="archive" title="Archive (e)"><span>${ico("archive")}</span> Archive</button>
    <button class="cab cab-del" data-ca="trash" title="Delete (d / ⌫ / #)"><span>${ico("trash")}</span> Delete</button>
    <button class="cab" data-ca="junk" title="Mark junk (b)"><span>${ico("ban")}</span> Junk</button>
    <button class="cab${it.flagged?" on":""}" data-ca="star" title="Star (s)">${it.flagged?`<span>${ico("star","fill")}</span> Starred`:`<span>${ico("star")}</span> Star`}</button>
    <button class="cab" data-ca="unread" title="Mark unread (u)" aria-label="Mark unread"><span>${ico("mail")}</span> Unread</button>
    <button class="cab" data-readset title="Reading settings — column width, text and mail colors" aria-label="Reading settings" aria-haspopup="dialog" aria-expanded="false"><span class="rs-aa">Aa</span></button>
    <button class="cab rd-more" data-more title="More actions" aria-label="More actions" aria-haspopup="menu" aria-expanded="false"><span>${ico("more")}</span></button>
    ${readerNavHtml()}</div>`;
  rd.innerHTML=`
    ${actions}
    <div class="rd-top"><span class="rd-tag">${esc({__snoozed__:"Snoozed",__muted__:"Muted",__waiting__:"Waiting",__replylater__:"Reply later"}[state.folder]||state.folder)}</span><span class="rd-when">${esc(d.date||"")}</span></div>
    <div class="rd-subj">${esc(d.subject||"(no subject)")}</div>
    <div class="rd-from"><button class="rd-sender" data-contact="${esc(d.from_addr||"")}" title="About this sender"><span class="nm">${esc(d.from_name||"")}</span> <span class="ad">${esc(d.from_addr||"")}</span></button>${d.to?' · to '+esc((d.to||"").slice(0,80)):""}</div>
    <div class="ai-card" id="aiCard" hidden></div>
    ${extBar(d)}${inviteCard(d)}${imgbar}${attbar}
    <iframe class="rd-iframe" title="Email body — from ${esc(d.from_name||d.from_addr||"sender")}: ${esc(d.subject||"(no subject)")}" sandbox="allow-same-origin" srcdoc="${esc(doc)}"></iframe>
    ${!isDraft&&canQuickReply(d.from_addr,it)?quickRepliesHtml(d.pk):""}
    ${upNextHtml(!isDraft)}`;
  storedSummaryCard(it);
  const fr=rd.querySelector("iframe"); fr._mail=d;
  fr.addEventListener("load",()=>{ sizeMailFrame(fr,400,30); wireMailLinks(fr,(d.from_addr||"").split("@")[1]); });
  if(!isDraft && state.folder!=="__muted__") armDraftAhead(d.pk, _draftEligibleClient(it));   // instant peek + dwell kick — "Reply" may already be "Draft ready"
}
async function loadImagesFor(pk){
  let d; try{ d=await api(`/api/message/${pk}/body?images=1`); }catch(e){ return; }
  if(d.ok) renderClientReader(d);
}
function inviteCard(d){
  const v=d&&d.invite; if(!v)return "";
  const all=v.attendees||[], att=all.slice(0,8).map(a=>esc(a.name||a.email)).join(", ");
  const kind={REQUEST:"Invitation",REPLY:"RSVP",CANCEL:"Cancelled",COUNTER:"Counter-proposal"}[v.method]||"Event";
  const dl=d.invite_part!=null?`<a class="inv-dl" href="/api/message/${d.pk}/attachment/${encodeURIComponent(d.invite_part)}" download="invite.ics">＋ Add to calendar</a>`:"";
  const canRsvp=(v.method==="REQUEST"||!v.method)&&v.organizer&&v.organizer.email;
  const rsvp=canRsvp?`<div class="inv-rsvp" data-rsvp-pk="${d.pk}">
      <button class="inv-yes" data-rsvp="accept">✓ Accept</button>
      <button class="inv-maybe" data-rsvp="tentative">? Maybe</button>
      <button class="inv-no" data-rsvp="decline">✕ Decline</button>
    </div>`:"";
  return `<div class="invite-card">
    <div class="inv-hd">${ico("cal")} ${esc(kind)}${v.status&&v.status!=="CONFIRMED"?` · ${esc(v.status.toLowerCase())}`:""}</div>
    <div class="inv-title">${esc(v.summary)}</div>
    <div class="inv-line">${ico("clock")} ${esc(v.when)}${v.recurrence?` · ${esc(v.recurrence)}`:""}</div>
    ${v.location?`<div class="inv-line">${ico("pin")} ${esc(v.location)}</div>`:""}
    ${v.organizer&&v.organizer.email?`<div class="inv-line inv-sub">organizer: ${esc(v.organizer.name||v.organizer.email)}</div>`:""}
    ${att?`<div class="inv-line inv-sub">${all.length} attendee${all.length===1?"":"s"}: ${esc(att)}${all.length>8?" +"+(all.length-8):""}</div>`:""}
    ${rsvp}
    ${dl?`<div class="inv-actions">${dl}</div>`:""}</div>`;
}
async function inviteRsvp(pk, decision, rowEl){
  const VERB={accept:"Accept",tentative:"Maybe",decline:"Decline"}[decision]||decision;
  const SENT={accept:"Accepted ✓",tentative:"Tentative ?",decline:"Declined ✕"}[decision];
  if(rowEl)rowEl.innerHTML=`<span class="inv-sending">Sending RSVP…</span>`;
  try{
    const r=await cpost("/api/invite/rsvp",{pk,decision});
    if(!r.ok){ if(rowEl)rowEl.innerHTML=`<span class="inv-sending">Couldn’t RSVP: ${esc(r.error||"error")}</span>`; toast(r.error||"RSVP failed"); return; }
    if(rowEl)rowEl.innerHTML=`<span class="inv-done">${esc(SENT)} — reply sent to ${esc(r.organizer||"organizer")}</span>`;
    toast(`RSVP: ${SENT}`);
    if(r.outbox_id)startUndoCountdown(r.outbox_id,null,0);   // the outbox's grace window: Undo cancels this RSVP, not what was last filed
  }catch(e){ if(rowEl)rowEl.innerHTML=`<span class="inv-sending">Couldn’t RSVP</span>`; toast("RSVP failed"); }
}
function attChip(pk,a){
  const href=`/api/message/${pk}/attachment/${encodeURIComponent(a.part_id)}`;
  return `<a class="rd-att" href="${href}" download="${esc(a.filename)}" title="Download ${esc(a.filename)}">${ico("clip")} <span class="fn">${esc(a.filename)}</span> <span class="sz">${fmtSize(a.size)}</span> <span class="dl">${ico("download")}</span></a>`;
}

/* ---------- conversation (thread) reader ---------- */
async function openThread(tid, latestPk, scroll, acct){
  acct=acct||state.account;
  state.csel=latestPk; state.thread=null;   // set again once this conversation loads; until then actions target latestPk, never the previous thread
  $$(".row.cmsg,#rows .brief-row").forEach(r=>r.classList.toggle("sel", r.dataset.tid===tid));
  const rd=$("#reader"); rd.className="reader"; document.body.classList.add("reading");
  setLoading(rd, "Loading conversation…", "Still fetching — the first open can be slow.");
  rd.insertAdjacentHTML("afterbegin", readerBarSkel());
  const seq=++state.rseq;   // the reader's own sequence, apart from the list's and the rail's
  let d; try{ d=await api(`/api/thread?account=${encodeURIComponent(acct)}&thread_id=${encodeURIComponent(tid)}`); }
  catch(e){ if(seq===state.rseq&&state.csel===latestPk)rd.innerHTML='<div class="nores">Couldn’t load.</div>'; return; }
  if(seq!==state.rseq||state.csel!==latestPk)return;   // superseded — another conversation or a single message opened meanwhile
  if(!d.ok){ rd.innerHTML='<div class="nores">'+esc(d.error||"error")+'</div>'; return; }
  state.thread=d;
  const subj=(d.items[0]||{}).subject||"(no subject)";
  const actions=`<div class="rd-actions">
    <button class="cab rd-back" data-back title="Back to list"><span>${ico("left")}</span> List</button>
    <button class="cab" data-tca="reply"><span>${ico("reply")}</span> Reply</button>
    <button class="cab" data-tca="replyall" title="Reply all" aria-label="Reply all"><span>${ico("replyall")}</span> All</button>
    <button class="cab" data-tca="forward"><span>${ico("forward")}</span> Forward</button>
    <button class="cab" data-tca="summarize"><span>${ico("wind")}</span> Summarize</button>
    <button class="cab" data-tca="snooze"><span>${ico("clock")}</span> Snooze</button>
    ${state.folder==='__replylater__'
      ?'<button class="cab" data-tca="rldone" title="Done — remove from Reply later"><span>'+ico("check")+'</span> Done</button>'
      :'<button class="cab" data-tca="replylater" title="Reply later (l)" aria-label="Reply later"><span>'+ico("bookmark")+'</span> Later</button>'}
    <button class="cab" data-tca="${state.folder==='__muted__'?'unmute':'mute'}" title="Mute thread (w)"><span>${ico("bell-off")}</span> ${state.folder==='__muted__'?'Unmute':'Mute'}</button>
    ${state.folder==='__waiting__'?'<button class="cab" data-tca="dismiss" title="Dismiss follow-up"><span>'+ico("check")+'</span> Done</button>':''}
    <span class="rd-sp" aria-hidden="true"></span>
    <button class="cab" data-tca="archive" title="Archive conversation (e)" aria-label="Archive conversation"><span>${ico("archive")}</span> Archive</button>
    <button class="cab cab-del" data-tca="trash" title="Delete conversation (d / ⌫ / #)" aria-label="Delete conversation"><span>${ico("trash")}</span> Delete</button>
    <button class="cab" data-readset title="Reading settings — column width, text and mail colors" aria-label="Reading settings" aria-haspopup="dialog" aria-expanded="false"><span class="rs-aa">Aa</span></button>
    <button class="cab rd-more" data-more title="More actions" aria-label="More actions" aria-haspopup="menu" aria-expanded="false"><span>${ico("more")}</span></button>
    ${readerNavHtml()}</div>`;
  const qrTo=[...d.items].reverse().find(m=>!(state.myAddrs&&state.myAddrs.has((m.from_addr||"").toLowerCase())));
  rd.innerHTML=`${actions}
    <div class="rd-subj">${esc(subj)} <span class="thr-n">${d.count} messages</span></div>
    <div class="ai-card" id="aiCard" hidden></div>
    <div class="thread" id="thread"></div>
    ${qrTo&&canQuickReply(qrTo.from_addr,{...(cfindAny(latestPk)||{}),is_junk:qrTo.is_junk})?quickRepliesHtml(qrTo.pk):""}
    ${upNextHtml(true)}`;
  storedSummaryCard(cfindAny(latestPk));
  const wrap=$("#thread");
  wrap.innerHTML=d.items.map((m,i)=>`
    <div class="tmsg${i===d.items.length-1?" open":""}" data-pk="${m.pk}">
      <div class="tmsg-hd" data-texp="${m.pk}">
        <span class="tnm">${esc(m.from_name)}</span>
        ${m.flagged?`<span class="rflag" style="color:var(--gold)">${ico("star","fill")}</span>`:""}${m.has_attach?`<span class="rflag" style="color:var(--mute)">${ico("clip")}</span>`:""}${m.answered?'<span class="tans">↩</span>':""}
        <span class="tfolder">${esc(m.folder)}</span><span class="twhen">${esc(m.when)}</span>
        <div class="tsnip">${esc(m.snippet)}</div>
      </div>
      <div class="tmsg-body" id="tbody-${m.pk}"></div>
    </div>`).join("");
  // expand the latest message by default
  const last=d.items[d.items.length-1]; if(last) expandMember(last.pk);
  d.items.forEach(m=>{ if(!last||m.pk!==last.pk) prefetchBody(m.pk); });  // warm the rest so expanding any member is instant
  if(d.reply_pk!=null) draftPeek(d.reply_pk);   // peek ONLY (thread browsing ≠ reply intent — never kicks generation)
  if(scroll){const r=$(`.row.cmsg[data-tid="${tid}"]`); if(r)r.scrollIntoView({block:"nearest"});}
}
async function expandMember(pk){
  const el=$(`#tbody-${pk}`), strip=el&&el.closest(".tmsg"); if(!el)return;
  if(strip.classList.contains("open") && el.dataset.loaded){ strip.classList.remove("open"); return; }
  strip.classList.add("open");
  if(el.dataset.loaded)return; el.dataset.loaded="1";
  setLoading(el, "Loading…", "Still fetching this message…");
  let d; try{ d=await api(`/api/message/${pk}/body`); }catch(e){ el.innerHTML='<div class="nores">Couldn’t load.</div>'; return; }
  if(!d.ok){ el.innerHTML='<div class="nores">'+esc(d.error||"error")+'</div>'; return; }
  const atts=(d.attachments||[]).filter(a=>!(a.is_inline&&a.content_id));
  const attbar=atts.length?'<div class="rd-attach">'+atts.map(a=>attChip(d.pk,a)).join("")+'</div>':"";
  const _nblk=(d.blocked_images||0)+(d.blocked_css_urls||0);
  const imgbar=_nblk?`<div class="rd-imgbar">${ico("shield")} ${_nblk} remote image${_nblk>1?"s":""} hidden — loading one can let the sender know you opened this email <button data-loadimg="${d.pk}">Load images</button></div>`:"";
  const doc=mailDoc(d);
  el.innerHTML=`${extBar(d)}${inviteCard(d)}${imgbar}${attbar}<iframe class="rd-iframe" title="Email body — from ${esc(d.from_name||d.from_addr||"sender")}: ${esc(d.subject||"(no subject)")}" sandbox="allow-same-origin" srcdoc="${esc(doc)}"></iframe>`;
  const fr=el.querySelector("iframe");
  if(fr){ fr._mail=d; fr.addEventListener("load",()=>{ sizeMailFrame(fr,300,24); wireMailLinks(fr,(d.from_addr||"").split("@")[1]); }); }
  cmarkseen(pk);
}

/* ---------- server-side search (virtual folder) ---------- */
let _searchTimer=null;
function onClientSearch(){
  clearTimeout(_searchTimer); state.viewId=null;   // a manually-typed query detaches from any saved view
  const raw=state.q.trim(), wasAsking=state.asking;
  if(raw.startsWith("?")){                          // "?…" → ask-your-inbox (NL Q&A), not a search
    const q=raw.slice(1).trim(); state.askQuery=q;
    if(!q){ state.asking=false; state.askAnswer=null; state.askSeq++; clearSearch(true); loadCMessages(); return; }
    state.searching=false;
    _searchTimer=setTimeout(runAsk, 350); return;
  }
  if(raw.startsWith("!")){                          // "!…" → NL bulk action (previewed in a modal; NEVER mutates without an explicit confirm)
    const cmd=raw.slice(1).trim();
    state.asking=false; state.askAnswer=null; state.askSeq++;   // leave ask mode if we were in it
    state._doPlan=null; state._doArmed=false;
    if($("#doPanel").classList.contains("show")) renderDoUI("loading",{cmd});   // superseding a shown plan → repaint so its confirm button can't be clicked stale
    if(!cmd){ state.doSeq++; $("#clientCtx").innerHTML=`${ico("wind")} <b>Do</b> · natural-language action`; renderDoUI("intro",{}); return; }
    const seq=++state.doSeq; clearTimeout(_searchTimer);
    _searchTimer=setTimeout(()=>{ if(seq===state.doSeq)runDo(cmd,seq); }, 450); return;
  }
  // leaving ask mode → supersede any in-flight runAsk (bump askSeq) so its late response can't clobber
  state.asking=false; state.askAnswer=null; state.askSeq++;
  state.doSeq++; state._doPlan=null; closeDoPanel();  // also supersede + close any in-flight do-plan modal
  if(!raw){ clearSearch(false);
    $("#clientCtx").innerHTML="";   // drop the stale search label: the folder's title shows again
    if(wasAsking)loadCMessages(); else renderClientList(); renderFolders(); return; }
  _searchTimer=setTimeout(runSearch, 250);
}
async function runAsk(){
  const seq=++state.askSeq, q=state.askQuery.trim(); if(!q)return;
  state.asking=true; state.searching=false;
  $("#clientCtx").innerHTML=`${ico("wind")} <b>Ask</b> · your inbox`;
  $("#count").textContent="asking…";
  renderAskUI("loading");
  let d; try{ d=await cpost("/api/ai/ask",{account: state.searchScope==="account"?state.account:"all", question:q}); }
  catch(e){ if(seq===state.askSeq)renderAskUI("error",{error:"ask failed"}); return; }
  if(seq!==state.askSeq)return;                     // a newer ask/typing superseded this one
  if(!d.ok){ renderAskUI("error",{error:d.error||"couldn’t answer"}); return; }
  state.askAnswer=d; renderAskUI("answer",d);
  $("#count").textContent=`${(d.sources||[]).length} source${(d.sources||[]).length===1?"":"s"}`;
  announce(`Answer ready — ${(d.sources||[]).length} cited sources`);
}
function renderAskUI(kind,d){
  const rows=$("#rows"); if(!rows)return;
  if(kind==="loading"){ rows.innerHTML=`<div class="ask-wrap"><div class="ai-card">
    <div class="ai-hd"><span class="ai-mark">${ico("wind")}Answer</span><span class="ask-q">${esc(state.askQuery||"")}</span></div>
    <div class="rd-skel"><div class="skb"></div><div class="skb"></div><div class="skb"></div><div class="skb short"></div></div>
    <div class="rd-skelhint"><span class="spin">${ico("wind")}</span> Reading your inbox…</div></div></div>`; return; }
  if(kind==="error"){ rows.innerHTML=`<div class="ask-wrap"><div class="ai-card"><div class="ask-err">Couldn’t answer: ${esc((d&&d.error)||"error")} <button class="ai-rerun" data-askrerun>Retry</button></div></div></div>`; return; }
  const srcs=(d.sources||[]).map(s=>`<div class="ask-src" data-asksrc="${s.pk}">
      <div class="ask-src-from">${esc(s.from||"")}${state.searchScope!=="account"&&s.account?` · ${esc(short(s.account))}`:""}</div>
      <div class="ask-src-subj">${esc(s.subject||"(no subject)")}</div>
      <div class="ask-src-snip">${esc((s.snippet||"").slice(0,140))}</div>
    </div>`).join("");
  rows.innerHTML=`<div class="ask-wrap">
    <div class="ai-card">
      <div class="ai-hd"><span class="ai-mark">${ico("wind")}Answer</span><span class="ask-q">${esc(d.question||state.askQuery)}</span></div>
      <div class="ask-answer">${esc(d.answer||"")}</div>
      <div class="ai-foot">${esc(d.model||"claude")} · grounded in your mail · <button class="ai-rerun" data-askrerun>↻ Ask again</button></div>
    </div>
    ${srcs?`<div class="ask-srcs-hd">${(d.sources||[]).length} cited source${(d.sources||[]).length===1?"":"s"}</div><div class="ask-srcs">${srcs}</div>`:`<div class="ask-srcs-hd ask-nosrc">No specific message cited.</div>`}
  </div>`;
}
/* ---------- NL bulk action ("!…"): plan → mandatory preview MODAL → confirm → existing bulk path + undo ---------- */
const _DO_VERB_LABEL={archive:"Archive",trash:"Delete",junk:"Mark junk",read:"Mark read",unread:"Mark unread",star:"Star",snooze:"Snooze"};
function openDoPanel(){ const o=$("#doPanel"); if(o)o.classList.add("show"); }
function closeDoPanel(){ const o=$("#doPanel"); if(o)o.classList.remove("show"); const b=$("#doPanelBody"); if(b)b.innerHTML=""; state._doArmed=false; }
async function runDo(cmd, seq){
  state._doCmd=cmd; state.asking=false;
  $("#clientCtx").innerHTML=`${ico("wind")} <b>Do</b> · natural-language action`;
  renderDoUI("loading",{cmd});
  let d; try{ d=await cpost("/api/ai/do-plan",{cmd}); }
  catch(e){ if(seq===state.doSeq)renderDoUI("error",{error:"planner failed"}); return; }
  if(seq!==state.doSeq)return;                        // superseded by newer typing / mode change
  if(!d||!d.ok){ renderDoUI("error",{error:(d&&d.error)||"couldn’t plan that"}); return; }
  if(d.intent!=="act"){                               // DEGRADE → plain search, acts on nothing
    const q=(d.filter||"").trim();
    state.doSeq++; state._doPlan=null;                // supersede do-mode
    if(!q){ renderDoUI("nofilter",{cmd}); return; }   // couldn't map anything safe → keep the panel with guidance
    closeDoPanel();
    $("#search").value=q; state.q=q; state.searching=true;
    $("#clientCtx").innerHTML=`${ico("search")} <b>Search</b>${d.empty?` · nothing to ${esc(cmd)}`:""}`;
    return runSearch();
  }
  state._doPlan=d; state._doArmed=false;
  renderDoUI("plan",d);
  announce(`${_DO_VERB_LABEL[d.verb]||d.verb} ${d.count} messages? Review and confirm.`);
}
function renderDoUI(kind, d){
  const body=$("#doPanelBody"); if(!body)return;
  openDoPanel();
  if(kind==="loading"){ body.innerHTML=`<div class="do-load"><span class="spin">↻</span> Planning “${esc((d&&d.cmd)||"")}”…</div>`; return; }
  if(kind==="error"){ body.innerHTML=`<div class="do-hd">${ico("wind")} Bulk action</div><div class="do-err">Couldn’t plan that: ${esc((d&&d.error)||"error")}</div><div class="do-foot"><button class="do-confirm" data-do-retry>Retry</button><button class="do-cancel" data-do-cancel>Close</button></div>`; return; }
  if(kind==="intro"){ body.innerHTML=`<div class="do-hd">${ico("wind")} Bulk action</div><div class="do-intro">Type a command in plain English — you’ll see exactly what it affects <b>before anything happens</b>.<div class="do-egs"><span>archive newsletters older than a week</span><span>mark everything from LinkedIn as read</span><span>snooze all receipts until next week</span></div></div><div class="do-foot"><button class="do-cancel" data-do-cancel>Close</button></div>`; return; }
  if(kind==="nofilter"){ body.innerHTML=`<div class="do-hd">${ico("wind")} Bulk action</div><div class="do-intro">I couldn’t turn that into a <b>safe</b> bulk action. Try naming a scope <i>and</i> a verb — e.g. “archive newsletters older than a week”. Drop the <b>!</b> to search instead.</div><div class="do-foot"><button class="do-cancel" data-do-cancel>Close</button></div>`; return; }
  // kind === "plan"
  const danger=(d.verb==="trash"||d.verb==="junk");
  const VERB=_DO_VERB_LABEL[d.verb]||d.verb;
  const nAct=d.capped?d.cap:d.count;
  const sample=(d.sample||[]).map(s=>`<div class="do-src"><span class="do-src-from">${esc(s.from||"")}</span><span class="do-src-subj">${esc(s.subject||"")}</span><span class="do-src-when">${esc(s.when||"")}</span></div>`).join("");
  const moreN=d.count-(d.sample||[]).length;
  const noUndo=(d.verb==="read"||d.verb==="unread"||d.verb==="star");
  const capNote=d.capped?`<div class="do-cap">${ico("alert")} Acts on the most recent <b>${d.cap}</b> of ${d.count} — run “!${esc(state._doCmd||"")}” again to continue.</div>`:"";
  const dangerNote=danger?`<div class="do-danger-note">${d.verb==="trash"?"Moves to Trash":"Marks as junk"} — reversible with <b>Undo</b> (z).</div>`:"";
  const undoNote=noUndo?`<div class="do-note">Not tracked by Undo — reverse it with the opposite command.</div>`:"";
  body.innerHTML=`<div class="do-plan${danger?" do-plan-danger":""}">
    <div class="do-hd">${ico("wind")} ${esc(VERB)} <b>${d.count}</b> message${d.count===1?"":"s"}?</div>
    <div class="do-scope">matching <code>${esc(d.filter||"")}</code>${d.verb==="snooze"?` · until <b>${esc((d.snooze_preset||"tomorrow").replace(/_/g," "))}</b>`:""}</div>
    <div class="do-srcs">${sample}${moreN>0?`<div class="do-more">+ ${moreN} more</div>`:""}</div>
    ${capNote}${dangerNote}${undoNote}
    <div class="do-foot">
      <button class="do-confirm${danger?" do-confirm-danger":""}" data-do-confirm>${esc(VERB)} ${nAct}</button>
      <button class="do-cancel" data-do-cancel>Cancel</button>
      <button class="do-assearch" data-do-search>Show as search</button>
    </div></div>`;
}
const _DO_DONE={archive:"Archived",trash:"Trashed",junk:"Marked junk",snooze:"Snoozed"};
/* The server carries out what the preview froze, by the plan's id: the page sends no list of messages.
   Archive, trash, junk and snooze come back as one undo group; read, unread and star aren't Undo-tracked (the modal says so). */
async function doConfirm(plan){
  if(!plan||plan.intent!=="act"||!plan.plan)return;
  state._doPlan=null; state._doArmed=false; closeDoPanel();
  clearSearch(false); state.doSeq++;                 // post-action view = the mailbox
  toast(`${_DO_VERB_LABEL[plan.verb]||plan.verb} ${plan.capped?plan.cap:plan.count}…`);
  try{
    const r=await cpost("/api/ai/do-confirm",{plan:plan.plan,undo_group:undoGroup()}), done=_DO_DONE[plan.verb]||"Updated";
    toast([`${done} ${r.count}`,...(r.failed?[`${r.failed} failed`]:[]),...(r.gone?[`${r.gone} had moved`]:[])].join(", "));
    if(r.undoable)showUndoFloat(`${done} ${r.count}`);
  }catch(e){ toast(e.status===410?"That preview has expired. Run the command again.":"Couldn’t finish that. Check what was done before trying again."); }
  loadCMessages(); loadFolders();
}
async function runSearch(){
  const seq=++state.searchSeq, q=state.q.trim();
  let d; try{ d=await api(`/api/search?q=${encodeURIComponent(q)}&scope=${state.searchScope}&account=${encodeURIComponent(state.account)}&limit=200`); }
  catch(e){ if(seq===state.searchSeq)$("#rows").innerHTML='<div class="nores">Search failed.</div>'; return; }
  if(seq!==state.searchSeq)return;
  if(!d.ok){ $("#rows").innerHTML='<div class="nores">'+esc(d.error||"bad query")+'</div>'; return; }
  state.searching=true; state.searchItems=d.items||[]; state.searchHasMore=!!d.has_more;
  renderSearchList();
}
function renderSearchList(){
  const v=state.searchItems;
  const isFeed=state.viewId==="__lane_feed__", isTrail=state.viewId==="__lane_papertrail__";
  if((state.viewId||"").startsWith("__dg_")){   // a list from the digest (openDigestList)
    $("#count").textContent=`${v.length} message${v.length===1?"":"s"}`;
    $("#clientCtx").innerHTML=`${ico("wind")} <b>${esc(state.digestTitle||"")}</b> · from today’s brief · <button class="ctx-save" data-dg-back>‹ Inbox</button>`;
    renderBulk();
    $("#rows").innerHTML=v.length?v.map(it=>rowMarkup(it, it.alias||short(it.account))).join(""):'<div class="nores">Nothing left here.</div>';
    return;
  }
  $("#count").textContent=isFeed
    ? `${v.length}${state.searchHasMore?"+":""} newsletter${v.length===1&&!state.searchHasMore?"":"s"}`
    : isTrail
    ? `${v.length}${state.searchHasMore?"+":""} receipt${v.length===1&&!state.searchHasMore?"":"s"}`
    : `${v.length}${state.searchHasMore?"+":""} result${v.length===1&&!state.searchHasMore?"":"s"}`;
  $("#clientCtx").innerHTML=isFeed
    ? `${ico("book")} <b>Feed</b> · a calm reading river · <span class="scopetog" id="scopeTog">${state.searchScope==="all"?"all accounts":"this account"} ▾</span>`
    : isTrail
    ? `${ico("receipt")} <b>Paper Trail</b> · receipts &amp; invoices · <span class="scopetog" id="scopeTog">${state.searchScope==="all"?"all accounts":"this account"} ▾</span>`
    : `${ico("search")} <b>${esc(state.q)}</b> · ${v.length} <span class="scopetog" id="scopeTog">${state.searchScope==="all"?"all accounts":"this account"} ▾</span>`
      +(state.viewId?"":` · <button class="ctx-save" id="ctxSaveView" title="Save as a view (v)">+ Save view</button>`);
  renderBulk();
  if(isFeed){ renderFeedRiver(); return; }   // the Feed lane reads as a calm river, not a row table
  if(isTrail){ renderPaperTrail(); return; }  // the Paper-trail lane reads as a money ledger
  if(!v.length){ $("#rows").innerHTML='<div class="nores">No matches.</div>'; return; }
  $("#rows").innerHTML=v.map(it=>rowMarkup(it, it.alias||short(it.account))).join("")
    +(state.searchHasMore?'<button class="load-more" data-loadmore="search">Load more results</button>':"");
}
function feedCardMarkup(it){
  const deck=esc(it.summary||it.snippet||"");
  return `<article class="fr-card${it.seen?"":" unread"}${it.pk===state.csel?" reading":""}" data-pk="${it.pk}">
    <div class="fr-kicker"><span class="fr-pub">${esc(it.from_name||it.from_addr||"")}</span><span class="fr-dot">·</span><span class="fr-when">${esc(it.when||"")}</span>${state.searchScope==="all"?`<span class="fr-dot">·</span><span class="fr-acct">${esc(it.alias||short(it.account))}</span>`:""}</div>
    <h3 class="fr-head">${esc(it.subject||"(no subject)")}</h3>
    ${deck?`<p class="fr-deck">${deck}</p>`:""}
    <button class="fr-arch" data-fr-arch="${it.pk}" title="Read &amp; archive">Archive ↓</button>
  </article>`;
}
function renderFeedRiver(){
  const v=state.searchItems;
  if(!v.length){ $("#rows").innerHTML='<div class="nores">Your Feed is clear — no newsletters waiting.</div>'; return; }
  $("#rows").innerHTML=`<div class="feed-river">`+v.map(feedCardMarkup).join("")
    +(state.searchHasMore?'<button class="load-more" data-loadmore="search">Load more</button>':"")+`</div>`;
}
async function feedArchive(pk){
  const it=(state.searchItems||[]).find(x=>x.pk===pk); if(!it)return;
  const snap=state.searchItems.slice(), wasSel=state.csel;
  state.searchItems=state.searchItems.filter(x=>x.pk!==pk);
  if(state.csel===pk){ state.csel=null; clearReader(); }
  $("#count").textContent=`${state.searchItems.length}${state.searchHasMore?"+":""} newsletter${state.searchItems.length===1&&!state.searchHasMore?"":"s"}`;
  healPk(pk).then(()=>renderFeedRiver());
  try{ const r=await cpost("/api/archive",{pk}); if(!r.ok)throw 0; toast("Archived"); if(r.undoable)showUndoFloat("Archived"); }
  catch(e){ state.searchItems=snap; state.csel=wasSel; renderFeedRiver(); toast("Couldn’t archive"); }
}
/* ---- Paper Trail: a local, regex-extracted receipts ledger (no AI, no network) ---- */
const _MONEY=/(?:[$€£]\s?\d[\d.,]*\d|\d[\d.,]*\d\s?(?:USD|EUR|GBP|EGP|CHF|AED))/gi;
const _CURSYM={USD:"$",EUR:"€",GBP:"£",EGP:"E£",CHF:"CHF",AED:"AED"};
function _parseMoney(s){
  const symM=s.match(/[$€£]/), codeM=s.match(/USD|EUR|GBP|EGP|CHF|AED/i);
  const cur=symM?symM[0]:(codeM?_CURSYM[codeM[0].toUpperCase()]:"");
  let num=s.replace(/[^\d.,]/g,"");
  if(num.includes(",")&&num.includes(".")) num=(num.lastIndexOf(",")>num.lastIndexOf("."))?num.replace(/\./g,"").replace(",","."):num.replace(/,/g,"");
  else if(num.includes(",")) num=/,\d{2}$/.test(num)?num.replace(",","."):num.replace(/,/g,"");
  const val=parseFloat(num);
  return {cur, val:isNaN(val)?null:val};
}
function extractAmount(it){   // the most prominent currency amount in subject+snippet (receipts repeat the total → take the max)
  const hay=((it.subject||"")+"  "+(it.snippet||"")).slice(0,400);
  const ms=hay.match(_MONEY); if(!ms)return null;
  let best=null;
  for(const raw of ms){ const p=_parseMoney(raw); if(p.val==null)continue; if(!best||p.val>best.val)best={...p,raw:raw.trim()}; }
  return best;
}
function _isRefund(it){ return /\b(refund|credit note|reimburs|money back)\b/i.test(it.subject||""); }
function _fmtMoney(cur,val){ const neg=val<0; return (neg?"−":"")+(cur||"")+Math.abs(val).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}); }
function _monthName(ym){ if(!/^\d{4}-\d{2}$/.test(ym||""))return "Undated";
  const [y,m]=ym.split("-"); return ["January","February","March","April","May","June","July","August","September","October","November","December"][+m-1]+" "+y; }
function ptRowMarkup(it,amt){
  const refund=amt&&_isRefund(it), shown=amt?(refund?-amt.val:amt.val):null;
  return `<div class="pt-row${it.seen?"":" unread"}${it.pk===state.csel?" reading":""}" data-pk="${it.pk}">
    <div class="pt-main"><div class="pt-merch">${esc(it.from_name||it.from_addr||"")}${state.searchScope==="all"?` <span class="fr-acct" style="font-size:10px">· ${esc(it.alias||short(it.account))}</span>`:""}</div>
      <div class="pt-desc">${refund?"↩ ":""}${esc(it.subject||"(no subject)")}</div></div>
    <div><div class="pt-amount${amt?(refund?" refund":""):" none"}">${amt?esc(_fmtMoney(amt.cur,shown)):"—"}</div>
      <div class="pt-date">${esc(it.when||"")}</div></div>
  </div>`;
}
function renderPaperTrail(){
  const v=state.searchItems;
  if(!v.length){ $("#rows").innerHTML='<div class="nores">No receipts or invoices yet.</div>'; return; }
  // group by month (items arrive newest-first); per-month per-currency subtotals
  const groups=[]; let cur=null;
  for(const it of v){ const ym=(it.date||"").slice(0,7);
    if(!cur||cur.ym!==ym){ cur={ym,items:[]}; groups.push(cur); }
    cur.items.push(it); }
  let html=`<div class="paper-trail">`;
  for(const g of groups){
    const totals={};
    const rows=g.items.map(it=>{ const amt=extractAmount(it);
      if(amt){ totals[amt.cur]=(totals[amt.cur]||0)+(_isRefund(it)?-amt.val:amt.val); }
      return ptRowMarkup(it,amt); }).join("");
    const tot=Object.entries(totals).map(([c,v2])=>`<b>${esc(_fmtMoney(c,v2))}</b>`).join("  ·  ");
    html+=`<div class="pt-month"><span class="pt-mname">${esc(_monthName(g.ym))}</span><span class="pt-mtot">${g.items.length} item${g.items.length===1?"":"s"}${tot?"  —  "+tot:""}</span></div>`+rows;
  }
  html+=(state.searchHasMore?'<button class="load-more" data-loadmore="search">Load more</button>':"")+`</div>`;
  $("#rows").innerHTML=html;
}
function toggleSearchScope(){ state.searchScope=state.searchScope==="all"?"account":"all";
  try{localStorage.setItem("mm_searchscope",state.searchScope);}catch(e){} runSearch(); }
async function openSearchHit(pk){
  const it=(state.searchItems||[]).find(x=>x.pk===pk); if(!it)return;
  // STAY in search — the results are the working list: j/k walks them, reader actions heal
  // them, Esc / clearing the box returns to the folder. (Feed, Paper Trail and ask-sources
  // already open this way; the body API is pk-scoped so no account/folder jump is needed.)
  openClientMsg(pk,true);
}
function clearSearch(silent){ state.searching=false; state.asking=false; state.askAnswer=null; state.searchItems=[]; state.viewId=null; if(!silent){ $("#search").value=""; state.q=""; closeSearchBar(); } }
/* the compact top bar (<=760px) keeps search behind an icon; open, it spans the bar until cleared or cancelled */
const _compactTop=matchMedia("(max-width:760px)");
function openSearchBar(){ if(_compactTop.matches)document.body.classList.add("search-open"); $("#search").focus(); }
function closeSearchBar(){ document.body.classList.remove("search-open"); }
function cancelSearchBar(){
  const was=state.searching||state.asking||$("#search").value.trim();
  clearSearch(false); $("#search").blur();
  if(was){ if(state.allInbox)loadAllInboxes(); else loadCMessages(); }
}
function cfindAny(pk){ return cfind(pk) || (state.searchItems||[]).find(x=>x.pk===pk) || (state.thread&&(state.thread.items||[]).find(x=>x.pk===pk)); }

/* ---------- client-mode message actions (optimistic + rollback) ---------- */
function cpost(path,obj,opts){ return api(path,Object.assign({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(obj)},opts)); }
function cfind(pk){ return state.cmsgs.find(x=>x.pk===pk); }
function clearReader(){ if(briefHere()){ showBrief(false); return; }
  state.csel=null; state.thread=null; const r=$("#reader"); r.className="reader empty-pane"; r.textContent="No message selected."; document.body.classList.remove("reading"); }
/* ═══════════ Today's brief, in the Mailbox reading pane ═══════════
   The Inbox (and All inboxes) opens on the brief instead of its first message, which would also
   start that message's read timer. A pinned row heads the list to come back to it, and k from the
   first message does too. Built from the digest (state.all) for what needs you, and the
   /api/briefing narrative, which the server computes once and caches. */
let _briefErr=null, _briefAsked=false;
const BRIEF_LIMIT="limit";   // _briefErr when today's AI limit kept the brief from being written
function briefHere(){ return !state.searching && (state.allInbox||(state.folder||"").toUpperCase()==="INBOX"); }
function briefOpen(){ return $("#reader").classList.contains("brief-pane"); }
function needsIt(it){ return !!it.needs_you; }   // the to-do test: a next step, an action-needed category, or importance 4+
function needsYou(){
  return (state.all||[]).filter(needsIt)
    .sort((a,b)=>(b.importance||0)-(a.importance||0)||b.pk-a.pk);
}
/* The Meadow scene from the mockup: sky, sun or moon, clouds, hills, a tree and a cottage, painted
   from the theme's scene tokens so it follows Day and Night. p prefixes the SVG ids (one per copy
   on the page). It is seeded, so a (p, o) pair always draws the same picture: cached. */
function rnd(seed) { let s = seed; return () => (s = (s * 16807) % 2147483647) / 2147483647; }
const PUFFS = [[-74, 2, 30], [-40, -8, 38], [0, -14, 46], [44, -6, 36], [80, 4, 26], [-30, -46, 32], [10, -60, 40], [48, -40, 28], [-4, -96, 30], [24, -88, 25], [-22, -80, 22]];
function cloud(p, x, y, s, o = 1, extra = '') {
  return `<g transform="translate(${x} ${y}) scale(${s})" opacity="${o}" ${extra}><g clip-path="url(#${p}cb)">${PUFFS.map(([dx, dy, r]) => `<circle cx="${dx}" cy="${dy}" r="${r}" fill="url(#${p}cl)"/>`).join('')}</g></g>`;
}
function scene(p, o = {}) {
  const R = rnd(o.seed || 11);
  let stars = ''; for (let i = 0; i < 70; i++) stars += `<circle cx="${(R() * 1200).toFixed(0)}" cy="${(R() * 170).toFixed(0)}" r="${(R() * 1.3 + .3).toFixed(2)}" fill="#fff" opacity="${(R() * .6 + .3).toFixed(2)}"/>`;
  let grass = ''; const groups = ['', ' b', ' c'];
  for (let gI = 0; gI < 3; gI++) {
    let blades = '';
    for (let i = 0; i < 46; i++) {
      const x = R() * 1200, h = 10 + R() * 22, lean = (R() - .4) * 10, base = 322 - R() * 8;
      blades += `<path d="M${x.toFixed(1)} ${base.toFixed(1)}q${(lean * .4).toFixed(1)} ${(-h * .55).toFixed(1)} ${lean.toFixed(1)} ${(-h).toFixed(1)}" stroke="var(--${R() > .5 ? 'hill-lit' : 'meadow'})" stroke-width="${(1.2 + R()).toFixed(1)}" fill="none" stroke-linecap="round"/>`;
    }
    grass += `<g class="sway${groups[gI]}">${blades}</g>`;
  }
  let ff = ''; for (let i = 0; i < 12; i++) ff += `<circle class="ff" style="animation-delay:${(-R() * 4).toFixed(1)}s" cx="${(560 + R() * 620).toFixed(0)}" cy="${(250 + R() * 60).toFixed(0)}" r="1.8" fill="#FFE59A" filter="url(#${p}glow)"/>`;
  const canopy = [[-44, 10, 22], [-26, -10, 28], [2, -22, 33], [30, -8, 27], [48, 10, 21], [-8, 6, 31], [22, 14, 24], [-30, 16, 19], [8, -40, 22]];
  const house = `<g transform="translate(842 249)">
      <ellipse cx="2" cy="1" rx="26" ry="3" fill="var(--leaf-3)" opacity=".25"/>
      <rect x="-14" y="-15" width="30" height="15" fill="var(--wall)"/>
      <rect x="6" y="-27" width="4" height="9" fill="var(--trunk)"/>
      <path d="M-18 -14 L1 -28 L20 -14z" fill="var(--roof)"/>
      <rect x="-8" y="-11" width="6" height="6" fill="var(--window)"/>
      <circle class="winglow" cx="-5" cy="-8" r="16" fill="url(#${p}wg)"/>
    </g>
    <path d="M848 252 C 820 268 760 282 702 298 C 668 308 646 316 630 322" stroke="var(--path)" stroke-width="3.2" fill="none" opacity=".55" stroke-linecap="round"/>`;
  return `<svg class="scene" viewBox="0 0 1200 320" preserveAspectRatio="${o.par || 'xMidYMax slice'}" aria-hidden="true" focusable="false">
  <defs>
    <linearGradient id="${p}sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--sky-1)"/><stop offset=".55" style="stop-color:var(--sky-2)"/><stop offset="1" style="stop-color:var(--sky-3)"/></linearGradient>
    <radialGradient id="${p}sun" cx="1080" cy="70" r="380" gradientUnits="userSpaceOnUse"><stop offset="0" style="stop-color:var(--disc-glow)"/><stop offset="1" style="stop-color:var(--disc-glow);stop-opacity:0"/></radialGradient>
    <linearGradient id="${p}cl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--cloud-1)"/><stop offset=".5" style="stop-color:var(--cloud-2)"/><stop offset="1" style="stop-color:var(--cloud-3)"/></linearGradient>
    <clipPath id="${p}cb" clipPathUnits="userSpaceOnUse"><rect x="-200" y="-220" width="400" height="244"/></clipPath>
    <linearGradient id="${p}mid" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--hill-mid)"/><stop offset="1" style="stop-color:var(--hill-near)"/></linearGradient>
    <linearGradient id="${p}near" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--meadow)"/><stop offset="1" style="stop-color:var(--hill-near)"/></linearGradient>
    <radialGradient id="${p}leaf" cx=".34" cy=".28" r=".85"><stop offset="0" style="stop-color:var(--leaf-1)"/><stop offset=".55" style="stop-color:var(--leaf-2)"/><stop offset="1" style="stop-color:var(--leaf-3)"/></radialGradient>
    <radialGradient id="${p}wg"><stop offset="0" stop-color="#FFC768" stop-opacity=".7"/><stop offset="1" stop-color="#FFC768" stop-opacity="0"/></radialGradient>
    <filter id="${p}paint" x="-5%" y="-10%" width="110%" height="120%"><feTurbulence type="fractalNoise" baseFrequency=".018 .04" numOctaves="2" seed="4" result="t"/><feDisplacementMap in="SourceGraphic" in2="t" scale="7" xChannelSelector="R" yChannelSelector="G"/></filter>
    <filter id="${p}tex" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" seed="9"/><feColorMatrix values="0 0 0 0 .3 0 0 0 0 .3 0 0 0 0 .25 0 0 0 .22 0"/><feComposite in2="SourceGraphic" operator="in"/></filter>
    <radialGradient id="${p}halo"><stop offset=".25" style="stop-color:var(--disc-glow)"/><stop offset="1" style="stop-color:var(--disc-glow);stop-opacity:0"/></radialGradient>
    <mask id="${p}moon"><rect x="1040" y="30" width="80" height="80" fill="#fff"/><circle cx="1091" cy="63" r="19" fill="#000"/></mask>
    <filter id="${p}glow" x="-200%" y="-200%" width="500%" height="500%"><feGaussianBlur stdDeviation="2.2"/></filter>
  </defs>
  <rect width="1200" height="320" fill="url(#${p}sky)"/>
  <rect width="1200" height="320" fill="url(#${p}sun)"/>
  <g class="stars">${stars}</g>
  <circle class="sunbody" cx="1080" cy="70" r="30" fill="var(--disc)"/>
  <g class="moonbody"><circle cx="1080" cy="70" r="70" fill="url(#${p}halo)"/><circle cx="1080" cy="70" r="22" fill="var(--disc)" mask="url(#${p}moon)"/></g>
  <g filter="url(#${p}paint)">
    <g class="cdrift">${cloud(p, 930, 178, 1.3)}${cloud(p, 1130, 196, .8, .95)}${cloud(p, 700, 66, .34, .9)}${cloud(p, 760, 150, .22, .8)}</g>
    <path d="M0 205 C 90 186 150 170 230 178 C 320 186 360 160 450 150 C 540 140 600 172 690 176 C 780 180 830 158 920 168 C 1010 178 1080 192 1200 184 L1200 320 L0 320Z" fill="var(--mtn)" opacity=".85"/>
    <path d="M0 230 C 140 212 260 208 380 216 C 500 224 600 236 720 230 C 840 224 940 206 1060 212 C 1120 215 1170 222 1200 224 L1200 320 L0 320Z" fill="var(--hill-far)"/>
    <path d="M0 260 C 120 242 240 238 360 246 C 480 254 560 262 680 258 C 800 254 860 244 960 242 C 1060 240 1140 252 1200 250 L1200 320 L0 320Z" fill="url(#${p}mid)"/>
  </g>
  ${house}
  <g transform="translate(1012 242)">
    <ellipse cx="6" cy="2" rx="52" ry="5" fill="var(--leaf-3)" opacity=".3"/>
    <path d="M-5 1 C -4 -16 -7 -28 -11 -38 L -4 -39 C 0 -28 3 -16 6 1Z" fill="var(--trunk)"/>
    <path d="M-6 -24 C -16 -30 -22 -36 -26 -44 M0 -28 C 8 -36 16 -40 26 -44" stroke="var(--trunk)" stroke-width="3" fill="none" stroke-linecap="round"/>
    <g transform="translate(0 -62)" filter="url(#${p}paint)">${canopy.map(([dx, dy, r]) => `<circle cx="${dx}" cy="${dy}" r="${r}" fill="url(#${p}leaf)"/>`).join('')}</g>
  </g>
  <path d="M0 302 C 160 286 320 278 480 284 C 640 290 760 302 900 294 C 1020 287 1120 278 1200 282 L1200 320 L0 320Z" fill="url(#${p}near)"/>
  <rect width="1200" height="320" filter="url(#${p}tex)" opacity=".5" style="mix-blend-mode:multiply"/>
  ${grass}
  <g class="bird" stroke="var(--soft)" stroke-width="1.6" fill="none" stroke-linecap="round" opacity=".55"><path d="M820 92q5-5 10 0q5-5 10 0"/><path d="M846 104q4-4 8 0q4-4 8 0"/></g>
  ${ff}
</svg>`;
}
const _sceneCache={};
function sceneSvg(p,o){ const k=p+JSON.stringify(o); return _sceneCache[k]||(_sceneCache[k]=scene(p,o)); }
function briefRowNote(n){ return n?`${n} need${n===1?"s":""} you`:"Nothing needs you"; }
function briefRowHtml(){
  const n=needsYou().length;
  return `<button class="brief-row${briefOpen()&&state.csel==null?" sel":""}" data-brief-row><span class="bthumb" aria-hidden="true">${sceneSvg("bt",{par:"xMaxYMax slice",seed:4})}</span>
    <span><b>Today’s brief</b><small${n?' class="sun"':""}>${briefRowNote(n)}</small></span></button>`;
}
let _briefSide={up:[],wait:[]}, _briefSideAt=0;
async function loadBriefSide(){
  if(Date.now()-_briefSideAt<60000)return; _briefSideAt=Date.now();
  const accts=state.accountsList||[];
  const [up,...ws]=await Promise.all([api("/api/upcoming").catch(()=>null),
    ...accts.map(a=>api(`/api/messages?account=${encodeURIComponent(a)}&folder=__waiting__&limit=40`).catch(()=>null))]);
  const wait=[].concat(...ws.map((w,i)=>((w&&w.items)||[]).filter(x=>x.kind==="sent").map(x=>({...x,account:accts[i]}))));
  _briefSide={up:(up&&up.items)||[], wait:wait.sort((a,b)=>(a.ts||0)-(b.ts||0))};   // longest wait first
  renderBrief();
}
function evWhen(e){   // [big, small]: "3:00 pm" over "today", "Sat" over "8:10 am", "Thu 6" over "all day" (a week out)
  const d=new Date(e.start), t0=new Date(); t0.setHours(0,0,0,0);
  const days=Math.round((new Date(d).setHours(0,0,0,0)-t0)/864e5);
  const time=e.all_day?"all day":d.toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"}).toLowerCase();
  if(days<=0) return e.all_day?["Today","all day"]:[time,"today"];
  return [d.toLocaleDateString(undefined,days<7?{weekday:"short"}:{weekday:"short",day:"numeric"}),time];
}
function waitAge(ts){ const d=Math.floor((Date.now()/1000-(ts||0))/86400); return d<1?"today":d===1?"1 day":d+" days"; }
function nameIn(addr){ const a=String(addr||""), n=a.replace(/<[^>]*>/,"").replace(/"/g,"").trim(); return n||a.replace(/[<>]/g,"").trim(); }
function briefSideHtml(){
  const up=_briefSide.up.slice(0,4), wait=_briefSide.wait, multi=(state.accountsList||[]).length>1;
  const ev=e=>{ const [a,b]=evWhen(e), sub=[e.location,e.organizer].filter(Boolean).join(" · ");
    return `<div class="bs-ev"><time datetime="${esc(e.start)}">${esc(a)}<small>${esc(b)}</small></time><div style="min-width:0">
      <button class="bs-t" data-bopen="${e.pk}">${esc(e.summary||"(no title)")}</button>${sub||e.invite?`<small class="m">${esc(sub)}${e.invite?`${sub?" · ":""}invite`:""}</small>`:""}</div></div>`; };
  const wt=w=>{ const who=nameIn(w.to)||"—";
    return `<button class="bs-wt" data-bopen="${w.pk}"><span class="av" style="--h:${avHue(w.to)}" aria-hidden="true">${esc(initialsOf(who,w.to))}</span>
      <span style="min-width:0"><b>${esc(who)}</b><small>${esc(w.subject||"(no subject)")} · ${esc(waitAge(w.ts))}${multi?" · "+esc(nameOf(w.account)):""}</small></span></button>`; };
  return (up.length?`<section class="bs-card" aria-label="Today and next"><h3>${ico("cal")}Today &amp; next</h3>${up.map(ev).join("")}</section>`:"")
    +(wait.length?`<section class="bs-card" aria-label="Waiting on others"><h3>${ico("hourglass")}Waiting on others</h3>${wait.slice(0,3).map(wt).join("")}
      ${wait.length>3?`<button class="bs-more" data-brief="__waiting__">See all ${wait.length}</button>`:""}</section>`:"");
}
/* ---------- starting soon: the next timed event, 10 minutes out (a desktop notification when backgrounded) ---------- */
const SOON_MS=10*60000, SOON_AFTER=5*60000;
let _soonUp=[], _soonAt=0;
const _soonGone=new Set(), _soonTold=new Set(), soonKey=e=>e.pk+"|"+e.start;
function soonIn(e){ const m=Math.ceil((new Date(e.start)-Date.now())/60000); return m>0?`in ${m} min`:"now"; }
async function checkSoon(){
  if(Date.now()-_soonAt>=60000){ _soonAt=Date.now();
    try{ const d=await api("/api/upcoming"); _soonUp=(d&&d.items)||[]; }catch(err){} }
  const now=Date.now(), e=_soonUp.find(x=>{ const d=new Date(x.start)-now;
    return !x.all_day&&!_soonGone.has(soonKey(x))&&d<=SOON_MS&&d>-SOON_AFTER; });
  const p=$("#soonPill"); if(!p)return;
  if(!e){ p.hidden=true; return; }
  const k=soonKey(e), title=e.summary||"(no title)", msg=`${title} starts ${soonIn(e)}`;
  p.hidden=false; p.dataset.k=k; p.dataset.pk=e.pk;
  $("#soonTitle").textContent=title; $("#soonIn").textContent=soonIn(e);
  $("#soonOpen").title=[evWhen(e)[0],e.location,e.organizer].filter(Boolean).join(" · ");
  $("#soonOpen").setAttribute("aria-label",`${msg}${e.location?", "+e.location:""}. Open the invite`);
  if(_soonTold.has(k))return; _soonTold.add(k);
  if(document.hidden&&window.Notification&&Notification.permission==="granted"){
    try{ const n=new Notification(msg,{body:[evWhen(e)[0],e.location].filter(Boolean).join(" · "),tag:"mailmind-soon-"+k});
      n.onclick=()=>{ window.focus(); n.close(); openClientMsg(e.pk,false); }; }catch(err){} }
  else announce(msg);
}
const _briefPeeked=new Set();
async function briefDraftPeek(){   // which Needs-you cards already have a pre-written reply: read-only peeks, once a card
  const pks=needsYou().slice(0,BRIEF_CAP).map(x=>x.pk).filter(pk=>!_briefPeeked.has(pk)&&!_draftReady.has(pk));
  if(!pks.length)return; pks.forEach(pk=>_briefPeeked.add(pk));
  const got=await Promise.all(pks.map(pk=>api(`/api/ai/draft-ahead?pk=${pk}&peek=1`).catch(()=>null)));
  if(got.some((d,i)=>d&&d.ok&&d.status==="ready"&&_draftReady.add(pks[i])))renderBrief();
}
async function trashSel(pk){ const it=state.all.find(x=>x.pk===pk); if(!it)return;   // archiveSel's twin, for the brief's cards
  try{ const r=await cpost("/api/trash",{pk});
    if(r.ok){ afterRemove(pk); toast("Deleted · in the mailbox's Trash"); if(r.undoable)showUndoFloat("Deleted"); refreshStats(); }
    else toast("Couldn’t delete: "+(r.msg||r.error||"")); }
  catch(e){ toast("Delete failed"); } }
function briefCardAct(a,pk,el){   // a Needs-you card's buttons: the reader's verbs, without opening the message first
  if(a==="draft"){ if(_draftReady.has(pk))openDraftAhead(pk); else replyTo(pk,false); }
  else if(a==="reply") replyTo(pk,false);
  else if(a==="read") openFromBrief(pk);
  else if(a==="snooze") openSnoozeMenu(el,{pk});
  else if(a==="archive") archiveSel(pk);
  else if(a==="trash") trashSel(pk);
}
function showBrief(open){   // open: asked for (the row, k, ⌘K), so a phone moves to the pane
  state.csel=null; state.thread=null; clearTimeout(_seenTimer);
  $$(".row.cmsg.sel").forEach(r=>r.classList.remove("sel"));
  document.body.classList.toggle("reading", !!open);
  if(!_briefData && !_todayInflight && !_briefAsked){ _briefAsked=true; loadBrief(false); }
  renderBrief(true); loadBriefSide();
  const b=$("#rows .brief-row"); if(b)b.classList.add("sel");
}
function renderBrief(force){   // force: (re)open it; otherwise refresh in place, and only when something changed
  const r=$("#reader"); if(!force&&!briefOpen())return;
  const html=briefHtml(); if(!force&&r._briefHtml===html)return;
  r.className="reader brief-pane"; r._briefHtml=html; r.innerHTML=html; if(force)r.scrollTop=0;
  briefDraftPeek();
  const sm=$("#rows .brief-row small"); if(sm){ const n=needsYou().length; sm.className=n?"sun":""; sm.textContent=briefRowNote(n); }   // in place: the row may hold focus
}
const BRIEF_CAP=12;   // Needs-you cards shown before "Show all"
/* ---------- AI presence: /api/digest's "ai" (summarize.status) ---------- */
function aiOff(){ return !!(state.ai&&state.ai.engine==="none"); }
function aiFailing(){ return !!(state.ai&&state.ai.engine!=="none"&&state.ai.last_ok===false&&state.ai.last_error); }
const AI_PRIVACY="With AI on, the text of a message goes to Anthropic only when it is summarized, briefed or drafted; nothing else leaves this machine.";
function aiOffHtml(){
  return `<div class="bf-aioff"><p><b>AI is off.</b> Summaries, this brief and reply drafts come from Claude, and MailMind has no Anthropic API key. Your mail works the same without it.</p>
    <p class="m">To turn it on, put an <code>anthropic_api_key</code> in <code>mailmind.config.json</code>, or set <code>ANTHROPIC_API_KEY</code>, then restart MailMind.</p>
    <p class="m pv">${ico("lock")}<span>${AI_PRIVACY}</span></p></div>`;
}
// micro-dollars in dollars and cents, rounded up so a little spending never reads as none
// (the server's pricing.usd)
function usd(micros){ const c=Math.ceil(micros/10000); return "$"+Math.floor(c/100).toLocaleString("en-US")+"."+String(c%100).padStart(2,"0"); }
function aiLineHtml(){   // the sync menu's line about AI
  const a=state.ai; if(!a)return "";
  if(a.engine==="none") return `<div class="sm-sub sm-ai">${ico("wind")}<span>AI is off · no API key</span></div>`;
  const spent=a.spent_today_micros, lim=a.limit_micros,
    what="Claude API · "+a.model+(spent>0?` · ${usd(spent)}${lim?" of "+usd(lim):""} today`:"")+(a.batch_waiting?` · ${a.batch_waiting} out in a batch`:"");
  return `<div class="sm-sub sm-ai${aiFailing()?" err":""}">${ico("wind")}<span>${esc(what)}${aiFailing()?` · last call failed: ${esc(a.last_error)}`:""}</span></div>
    <div class="sm-sub sm-ai m">${ico("lock")}<span>${AI_PRIVACY}</span></div>`;
}
function briefHtml(){
  const now=new Date(), h=now.getHours(), b=_briefData, needs=needsYou();
  const greet=h<5?"Working late":h<12?"Good morning":h<18?"Good afternoon":"Good evening";
  const why=aiFailing()?state.ai.last_error:_briefErr;
  const narr=aiOff()&&!(b&&b.narrative)?aiOffHtml()
    :_todayInflight?`<p class="bf-wait"><span class="spin">${ico("wind")}</span> Reading your inbox…</p>`
    :b&&b.narrative?`<p class="ai-tldr bf-narr">${esc(b.narrative)}</p>`
    :_briefErr===BRIEF_LIMIT?`<p class="bf-wait">Today’s AI limit is reached, so the brief isn’t written yet. <button class="ai-rerun" data-brief="retry">Write it</button></p>`
    :_briefErr?`<p class="bf-wait">The brief isn’t available right now (${esc(why)}). <button class="ai-rerun" data-brief="retry">Try again</button></p>`
    :'<p class="bf-wait">Nothing to brief yet.</p>';
  const nc=commitments().length, t=[];
  if(needs.length) t.push(`<button class="bf-tally sun" data-brief="needs" title="List them"><b>${needs.length}</b> need${needs.length===1?"s":""} you</button>`);
  if(state.waitingCount) t.push(`<button class="bf-tally" data-brief="__waiting__"><b>${state.waitingCount}</b> waiting on others</button>`);
  if(state.replylaterCount) t.push(`<button class="bf-tally" data-brief="__replylater__"><b>${state.replylaterCount}</b> to reply later</button>`);
  if(nc) t.push(`<button class="bf-tally" data-brief="commits">${ico("todo")}<b>${nc}</b> commitment${nc>1?"s":""}</button>`);
  t.push(`<button class="bf-tally" data-brief="week">${ico("cal")}Your week</button>`);
  const topics=briefTopics().map((g,i)=>g.items.length?`<button class="bf-topic" data-brief-topic="${i}">${esc(g.label)}<b>${g.items.length}</b></button>`:"").join("");
  const nt=tidyCandidates().length;
  const multi=(state.accountsList||[]).length>1, CAP=BRIEF_CAP, side=briefSideHtml();
  const card=it=>{ const subj=esc(it.subject||"(no subject)");
    return `<article class="bf-need" data-pk="${it.pk}"><span class="av" style="--h:${avHue(it.from_addr||it.from_name)}" aria-hidden="true">${esc(initialsOf(it.from_name,it.from_addr))}</span>
    <div style="min-width:0"><div class="bf-n1"><b>${esc(it.from_name||it.from_addr||"—")}</b>${multi?`<span class="acctchip">${esc(nameOf(it.account))}</span>`:""}<time>${esc(it.when||"")}</time></div>
      <button class="bf-subj" data-bopen="${it.pk}">${esc(it.subject||"(no subject)")}</button>
      ${it.summary?`<p class="bf-gist">${esc(it.summary)}</p>`:""}
      ${String(it.action||"").trim()?`<span class="rchip need">${esc(it.action)}</span>`:""}
      <footer class="bf-acts">${_draftReady.has(it.pk)?`<button class="bf-act ai" data-bact="draft" title="Opens the pre-written reply in the composer; nothing sends until you do">${ico("wind")}Use draft</button>`
        :`<button class="bf-act" data-bact="reply">${ico("reply")}Reply</button>`}<button class="bf-act quiet" data-bact="read">${ico("eye")}Read</button><span class="sp"></span>
        <button class="bf-ic" data-bact="snooze" title="Snooze" aria-label="Snooze “${subj}”">${ico("clock")}</button><button class="bf-ic" data-bact="archive" title="Archive" aria-label="Archive “${subj}”">${ico("archive")}</button><button class="bf-ic del" data-bact="trash" title="Delete" aria-label="Delete “${subj}”">${ico("trash")}</button></footer></div></article>`; };
  return `<div class="rd-actions"><button class="cab rd-back" data-back title="Back to list"><span>${ico("left")}</span> List</button><span class="rd-sp" aria-hidden="true"></span>
      <button class="cab" data-brief="refresh" title="Write the brief again from your mail as it is now (runs the AI)"${_todayInflight?" disabled":""}><span>${ico("refresh")}</span> Refresh brief</button></div>
    <header class="bf-hero">${sceneSvg("bz",{seed:21,par:"xMaxYMax slice"})}<div class="bf-ht"><div class="bf-date">${esc(now.toLocaleDateString(undefined,{weekday:"long",day:"numeric",month:"long"}))}</div><h1>${greet}.</h1></div></header>
    <section class="ai-card bf-card" aria-label="Today’s brief"><div class="ai-hd"><span class="ai-mark">${ico("wind")}Your brief</span></div>${narr}
      ${topics?`<div class="bf-topics" aria-label="Topics in today’s mail">${topics}</div>`:""}<div class="bf-tallies">${t.join("")}</div></section>
    <div class="bf-grid"><div style="min-width:0"><h2 class="bf-h" id="bfNeeds">Needs you</h2>
    ${needs.length?`<div class="bf-needs">${needs.slice(0,CAP).map(card).join("")}</div>${needs.length>CAP?`<button class="bf-tally" data-brief="needs">Show all ${needs.length}</button>`:""}`
      :'<p class="bf-calm">Nothing needs you right now.</p>'}
    ${nt?`<div class="bf-tidy"><span>${ico("wind")} <b>${nt}</b> low-priority message${nt>1?"s":""} the AI rates as safe to file: no next step, nothing asked of you.</span>
      <button class="bf-tally" data-brief="tidyreview">Review</button><button class="bf-tally" data-brief="tidy">File to Archive</button></div>`:""}</div>
    ${side?`<aside class="bf-side" aria-label="Your day">${side}</aside>`:""}</div>`;
}
function briefTopics(){   // the brief's topic groups, resolved against the digest (a group can name mail the digest no longer holds)
  const by=new Map((state.all||[]).map(x=>[x.pk,x]));
  return ((_briefData&&_briefData.groups)||[]).map(g=>({label:g.label, items:(g.pks||[]).map(pk=>by.get(pk)).filter(Boolean)}));
}
function briefAct(a){
  if(a==="needs") openDigestList("__dg_needs__","Needs you",needsYou());
  else if(a==="tidyreview") openDigestList("__dg_tidy__","Safe to file",tidyCandidates());
  else if(a==="tidy") doTidy();
  else if(a==="commits") openCommitments();
  else if(a==="week") openReview();
  else if(a==="refresh"||a==="retry"){ loadBrief(a==="refresh",true); if(a==="refresh"){ _briefSideAt=0; loadBriefSide(); } renderBrief(); }
  else if(a.startsWith("__")) selectFolder(a);
}
/* Lists the digest answers rather than a folder holds: Needs you, a topic from the brief, mail that
   is safe to file. They ride the search list (state.searching + searchItems, viewId "__dg_*"), so
   j/k, the reader, healing on archive and Esc back to the folder work as they do for results. */
function openDigestList(id,title,items){
  state.searching=true; state.asking=false; state.askAnswer=null; state.viewId=id; state.digestTitle=title;
  state.q=""; $("#search").value=""; state.searchItems=items.slice(); state.searchHasMore=false; state.cmarked.clear();
  renderSearchList(); renderFolders();
  if(isMobile()) document.body.classList.remove("reading");   // a phone shows the list it just opened
  else if(items.length) openSearchHit(items[0].pk); else clearReader();
}
function leaveDigestList(){ clearSearch(false); if(state.allInbox)loadAllInboxes(); else loadCMessages(); }
function openFromBrief(pk){   // a conversation in this list opens as one; anything else as the message
  const it=state.cmsgs.find(x=>x.pk===pk);
  if(it) openConversation(it,true); else openClientMsg(pk,false);
}
function refreshRow(pk){   // update ONE row in place (no full-list rebuild -> no flicker)
  if(state.asking)return;   // ask-your-inbox owns #rows; a dwell-seen refresh must not clobber the answer
  if(state.searching&&state.viewId==="__lane_feed__"){   // the Feed river uses .fr-card, not .row
    const it=(state.searchItems||[]).find(x=>x.pk===pk), node=$(`#rows .fr-card[data-pk="${pk}"]`);
    if(it&&node)node.outerHTML=feedCardMarkup(it); else renderFeedRiver(); return; }
  if(state.searching&&state.viewId==="__lane_papertrail__"){ renderPaperTrail(); return; }   // ledger rows, not .row
  const list=state.searching?state.searchItems:state.cmsgs, it=list.find(x=>x.pk===pk);
  const node=$(`#rows .row.cmsg[data-pk="${pk}"]`);
  if(!it||!node){ renderClientList(); return; }
  node.outerHTML=rowMarkup(it, state.searching?(it.alias||short(it.account)):"");
}
async function cstar(pk){ const it=cfindAny(pk); if(!it)return; const on=!it.flagged; it.flagged=on?1:0; refreshRow(pk);
  try{ const r=await cpost("/api/flag",{pk,flag:"flagged",on}); if(!r.ok)throw 0; toast(on?"Starred":"Unstarred"); }
  catch(e){ it.flagged=on?0:1; refreshRow(pk); toast("Couldn’t star"); } }
async function cunread(pk){ const it=cfindAny(pk); if(!it)return; it.seen=0; refreshRow(pk);
  try{ const r=await cpost("/api/seen",{pk,seen:false}); if(!r.ok)throw 0; toast("Marked unread"); }
  catch(e){ it.seen=1; refreshRow(pk); toast("Couldn’t update"); } }
async function cmarkseen(pk){ const it=cfindAny(pk); if(it&&it.seen)return; if(it)it.seen=1; refreshRow(pk);
  try{ const r=await cpost("/api/seen",{pk,seen:true}); if(!r.ok)throw 0; }
  catch(e){ if(it)it.seen=0; refreshRow(pk); } }
/* ---------- mobile: folder-rail drawer + reader back ---------- */
function isMobile(){ try{ return matchMedia("(max-width:820px)").matches; }catch(e){ return false; } }
/* ---- Mailbox shell: folder rail, list width, focus reading ----
   Wide windows: ☰ / [ switch the rail between icons and icons + names (rail-wide, kept in mm_rail).
   <=1100px: the same control opens the rail as a drawer (rail-open). */
const _railDocked=matchMedia("(min-width:1101px)");
function _syncRailBtn(){
  const b=$("#railToggle"); if(!b)return; const docked=_railDocked.matches;
  b.setAttribute("aria-expanded", document.body.classList.contains(docked?"rail-wide":"rail-open"));
  b.setAttribute("aria-label", docked?(document.body.classList.contains("rail-wide")?"Show folder icons only":"Show folder names"):"Folders");
}
function applyRailWide(on){
  document.body.classList.toggle("rail-wide", !!on);
  try{ localStorage.setItem("mm_rail", on?"wide":"narrow"); }catch(e){}
  _syncRailBtn();
}
function toggleRail(){
  setReadFocus(false);   // asking for the folders means leaving focus reading
  if(_railDocked.matches) applyRailWide(!document.body.classList.contains("rail-wide"));
  else { document.body.classList.toggle("rail-open"); _syncScrim(); _syncRailBtn(); }
}
function closeRail(){ if(document.body.classList.contains("rail-open")){ document.body.classList.remove("rail-open"); _syncScrim(); _syncRailBtn(); } }
_railDocked.addEventListener("change",()=>{ closeRail(); _syncRailBtn(); });   // a drawer left open must not strand the scrim once the rail docks
const LIST_W0=372;
let _listW=LIST_W0;
function applyListW(w, save){
  _listW=Math.max(300, Math.min(560, Math.round(w)));
  document.body.style.setProperty("--list-w", _listW+"px");
  $("#splitter").setAttribute("aria-valuenow", _listW);
  if(save){ try{ localStorage.setItem("mm_list_w", _listW); }catch(e){} }
}
function readFocusOn(){ return document.body.classList.contains("read-focus"); }
function setReadFocus(on){
  on=!!on && !isMobile();   // a phone already shows one pane at a time
  if(on===readFocusOn())return;
  document.body.classList.toggle("read-focus", on);
  $$("#reader [data-readfocus]").forEach(b=>b.setAttribute("aria-pressed", on));
  if(on) closeRail();
}
function backToList(){ document.body.classList.remove("reading"); }
/* ---------- row "heal": collapse a row out before the list re-renders (reduced-motion → instant) ---------- */
function _reduceMotion(){ try{ return matchMedia("(prefers-reduced-motion:reduce)").matches; }catch(e){ return false; } }
function healNode(node){
  if(!node || node.classList.contains("heal-out") || _reduceMotion()) return Promise.resolve();
  node.classList.add("heal-out"); node.style.maxHeight=node.scrollHeight+"px"; node.getBoundingClientRect();
  requestAnimationFrame(()=>{ node.style.maxHeight="0"; node.style.paddingTop="0"; node.style.paddingBottom="0"; node.style.marginTop="0"; node.style.marginBottom="0"; });
  return new Promise(res=>setTimeout(res,240));
}
function healPk(pk){ return healNode($(`#rows [data-pk="${CSS.escape(String(pk))}"]`)); }   // deferred render re-syncs DOM to current state (idempotent → race-safe with undo)
function cRemove(pk){ const list=listInView();   // "next" comes from the list the user is looking at
  const i=list.findIndex(x=>x.pk===pk); const next=list[i+1]||list[i-1]||null;
  state.cmsgs=state.cmsgs.filter(x=>x.pk!==pk);
  state.searchItems=(state.searchItems||[]).filter(x=>x.pk!==pk);   // acting from search heals the results too
  state.all=state.all.filter(x=>x.pk!==pk); renderBrief();   // the brief counts what needs you from the digest
  state.cmarked.delete(pk);   // drop from bulk selection so the count/actions can't go stale
  _draftReady.delete(pk); _draftingPks.delete(pk);   // acted on → drop any pre-draft chip state
  healPk(pk).then(()=>renderClientList());
  if(state.csel===pk){ if(next)openConversation(next,true); else clearReader(); } }   // as j would: a conversation opens in the thread reader
function cForget(pks){   // the server filed these along with the row acted on (the rest of its conversation): drop them everywhere
  const gone=new Set(pks), keep=x=>!gone.has(x.pk), was=state.cmsgs.length+(state.searchItems||[]).length+state.all.length;
  state.cmsgs=state.cmsgs.filter(keep); state.searchItems=(state.searchItems||[]).filter(keep); state.all=state.all.filter(keep);
  gone.forEach(p=>{ state.cmarked.delete(p); _draftReady.delete(p); _draftingPks.delete(p); });
  if(state.cmsgs.length+state.searchItems.length+state.all.length!==was){ renderClientList(); renderBrief(); }   // else leave the row's heal running
}
async function cremoveAction(pk,path,obj,verb,act){ if(!cfindAny(pk))return;   // a search hit may live outside the current folder's cmsgs
  const snap=state.cmsgs.slice(), ssnap=(state.searchItems||[]).slice(), wasSel=state.csel;
  cRemove(pk);
  try{ const r=await cpost(path,obj); if(!r.ok)throw new Error(r.msg||r.error||"err");
    if(r.pks)cForget(r.pks);
    const n=r.count||1;   // a conversation route reports how many messages it filed, and how many it couldn't
    const later=(r.status==="queued"||r.queued)?(readOnly()?" · waits for a full run":" · once the server is back"):"";
    toast((r.failed?`${verb} ${n} of ${n+r.failed} — the rest stayed`:n>1?`${verb} ${n} messages`:verb)+later);
    if(r.undoable)showUndoFloat(n>1?`${verb} ${n}`:verb);
    if(r.failed)loadCMessages(); }
  catch(e){ state.cmsgs=snap; state.searchItems=ssnap; state.csel=wasSel; renderClientList(); if(wasSel===pk)openClientMsg(pk,false); toast("Couldn’t "+act); } }
function convoTarget(pk){ return convoRow(cfind(pk)) || (!!state.thread && state.csel===pk); }   // a conversation row, or the conversation open in the reader
function carchive(pk){ return cremoveAction(pk,convoTarget(pk)?"/api/thread/archive":"/api/archive",{pk},"Archived","archive"); }   // a conversation files whole
function ctrash(pk){ return cremoveAction(pk,convoTarget(pk)?"/api/thread/trash":"/api/trash",{pk},"Trashed","delete"); }
function cjunk(pk){ return cremoveAction(pk,"/api/junk",{pk},"Marked junk","mark junk"); }

/* ---------------- compose / reply / forward ---------------- */
function composeIsOpen(){ return state.compose!==null; }
function rid(){ return Date.now().toString(36)+Math.random().toString(36).slice(2,10); }
function parseAddrs(s){ return (String(s||"").match(/[^\s<>,;"]+@[^\s<>,;"]+/g)||[]); }
function addrOf(s){ return parseAddrs(s)[0]||""; }
function rcptAddr(v){ const m=String(v||"").match(/<([^<>]+)>\s*$/);   // where a chip's mail goes: the <…> part, not an address in its name
  return ((m?m[1]:addrOf(v))||String(v||"")).trim().toLowerCase(); }
function rePrefix(subj,pre){ const s=(subj||"").trim();
  return /^(re|fwd?|fw)\s*:/i.test(s)?pre+" "+s.replace(/^(re|fwd?|fw)\s*:\s*/i,""):(s?pre+" "+s:pre); }
function looksLikeAddr(v){ const m=v.match(/<([^>]+)>/); const a=(m?m[1]:v).trim();
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(a); }
function fieldEl(f){ return $("#cmp"+f[0].toUpperCase()+f.slice(1)); }

function composeOpen(opts){
  opts=opts||{};
  clearTimeout(_draftDwellT);   // composing: a pending dwell kick would draft the very message being answered by hand
  const senders=(state.sendAccounts&&state.sendAccounts.length)?state.sendAccounts:(state.accountsList||[]);
  if(!senders.length){ toast("No sending account configured — add one in Settings"); return; }
  const from=(opts.from&&senders.includes(opts.from))?opts.from:(senders.includes(state.account)?state.account:senders[0]);
  if(state.compose)clearInterval(state.compose._autosaveIv);   // a composer replaced in place: its autosave stops with it
  state.compose={ mode:opts.mode||"new", from,
    to:(opts.to||[]).map(v=>({v})), cc:(opts.cc||[]).map(v=>({v})), bcc:(opts.bcc||[]).map(v=>({v})),
    subject:opts.subject||"", origPk:opts.origPk||null, quote:opts.quote||null, draftId:opts.draftId||null, version:opts.version??null,
    resume:opts.resume||null,   // a reopened draft: its own threading and the quote it already carries
    fwdAttach:opts.fwdAttach||0, attachments:[], idk:rid(), status:"idle", dirty:false,
    _reading:new Map(),   // files still being read: id → name
    autoCc:opts.autoCc?opts.autoCc.map(a=>String(a).toLowerCase()):null,   // a reply-all's copied-in people
    subject0:opts.subject0!=null?opts.subject0:(opts.subject||""),         // the subject as it opened
    _ack:{subject:!!(opts.ack&&opts.ack.subject),attach:!!(opts.ack&&opts.ack.attach),   // send-time checks already waved through
      fanout:(opts.ack&&+opts.ack.fanout)||0,unchecked:false},
    _guard:null,
    participants:new Set((opts.participants||[]).map(a=>String(a).toLowerCase())),   // the thread's people: never "new"
    _people:new Map(), _thin:false, _knownQ:new Set(), _newSay:new Set(),   // "new to you" lookups: addr → {known,self}
    _pendingAfter:0 };   // a Later time the check strip held back: the next Send schedules for it
  guardReset();
  // dirty flips true only on an actual subject/body edit (input listeners) — so opening a
  // prefilled reply and pressing Esc never silently litters Drafts.
  $("#cmpFrom").innerHTML=senders.map(e=>
    `<option value="${esc(e)}"${e===from?" selected":""}>${esc(nameOf(e))} — ${esc(e)}</option>`).join("");
  $("#cmpTitle").textContent={reply:"Reply",replyall:"Reply all",forward:"Forward"}[state.compose.mode]||"New message";
  $("#cmpAiSplit").hidden=aiOff()||!(state.compose.mode==="reply"||state.compose.mode==="replyall")||state.compose.origPk==null
    ||resumeHidesAi(state.compose);
  $("#cmpAiRow").hidden=aiOff();
  paintDraftBtn();
  $("#cmpSubject").value=state.compose.subject;
  // insert the From account's signature for FRESH composes only (not editDraft / failed-reopen,
  // which carry their own body already containing a signature). Lands above the server-appended quote.
  let body0="";
  if(!opts.draftId && !opts.noSig){ const sig=sigFor(from); if(sig){ const blk="\n\n-- \n"+sig; body0=blk; state.compose._sigBlock=blk; state.compose._sigFrom=from; } }
  $("#cmpBody").value=body0; $("#cmpBody").setSelectionRange(0,0);   // write above the signature, not after it
  $("#cmpSaved").textContent=""; $("#cmpRwUndo").hidden=true; $("#cmpNote").hidden=true; $("#cmpNote").textContent="";
  const showCc=state.compose.cc.length>0||state.compose.bcc.length>0;
  $("#cmpCcRow").hidden=!showCc; $("#cmpBccRow").hidden=!showCc;
  renderChips("to"); renderChips("cc"); renderChips("bcc"); renderAttach(); renderQuote(); setComposePill();
  $("#composeModal").classList.add("show"); $("#scrim").classList.add("on");
  setReturnFocus=document.activeElement;
  state.compose._autosaveIv=setInterval(autosaveTick, 8000);   // continuous draft autosave
  const opened=state.compose, firstField=()=>opened.mode==="new"?fieldEl("to").querySelector(".ci"):$("#cmpBody");
  firstField()?.focus();   // at once, so the first keys land here and not on the list's shortcuts
  setTimeout(()=>{ if(state.compose===opened&&!$("#composeModal").contains(document.activeElement))firstField()?.focus(); },40);   // a second try, never away from a field already chosen
  knownQueue([...state.compose.to,...state.compose.cc,...state.compose.bcc].map(x=>x.v),false);   // prefilled chips are marked silently
}
function sigFor(email){ return (state.signatures&&state.signatures[email])||state.signatureDefault||""; }
function adoptSig(text, acct){   // a composer reopened with its own text tracks the signature it ends with
  const cmp=state.compose, blk=resumeSig(text, sigFor(acct));
  if(cmp&&blk){ cmp._sigBlock=blk; cmp._sigFrom=acct; } }
// Every edit counts: a save clears `dirty` only if none landed while it was in flight.
function markEdited(cmp){ if(!cmp)return; cmp.dirty=true; cmp.edits=(cmp.edits||0)+1; }
function composeSnap(cmp){   // what it takes to open this composer again
  return {mode:cmp.mode,from:cmp.from,to:cmp.to,cc:cmp.cc,bcc:cmp.bcc,subject:cmp.subject,
    body:cmp.body,origPk:cmp.origPk,quote:cmp.quote,fwdAttach:cmp.fwdAttach,attachments:cmp.attachments,draftId:cmp.draftId,
    resume:cmp.resume,autoCc:cmp.autoCc,subject0:cmp.subject0,participants:[...cmp.participants],
    ack:{subject:cmp._ack.subject,attach:cmp._ack.attach,fanout:cmp._ack.fanout}};
}
function closeSaveFailed(cmp,snap){   // the composer has gone and nothing holds its words: keep them a click away
  state.failedSend={...snap,draftId:cmp.draftId};
  const m="Your message couldn’t be saved";
  showSendFloat("failed",ico("alert")+" "+m,true); announce(m);
}
let _closeSave=null;   // the saves composeClose started, until all settle: Edit waits for them
function composeClose(force){
  const cmp=state.compose;
  if(typeof acClose==="function")acClose();
  closeDraftMenu(); closeSendLaterMenu(); guardReset(); clearTimeout(_knownT);
  if(cmp&&cmp._autosaveIv)clearInterval(cmp._autosaveIv);   // stop autosave before nulling state
  const closingSave=cmp&&cmp.dirty&&!force;
  if(closingSave){   // auto-save on close
    const saved=composeSaveDraft(true), snap=composeSnap(cmp);   // the save read the fields: the snapshot holds them
    const p=_closeSave=Promise.all([_closeSave, saved.then(ok=>{
      if(ok){ toast("Saved to Drafts"); refreshDrafts(); } else if(ok===false)closeSaveFailed(cmp,snap); })]);
    p.finally(()=>{ if(_closeSave===p)_closeSave=null; }); }   // one still in flight from an earlier close counts too
  state.compose=null;
  $("#composeModal").classList.remove("show");
  _syncScrim();
  try{setReturnFocus&&setReturnFocus.focus();}catch(e){}
  if(cmp&&cmp.draftId&&!closingSave)refreshDrafts();   // its saves replaced the copy this list shows
}
function draftsOnScreen(){ return !state.allInbox&&(state.folders||[]).some(f=>f.name===state.folder&&f.role==="drafts"); }
function refreshDrafts(){
  if(draftsOnScreen()&&!state.compose&&!state.searching&&!state.asking&&state.cmarked.size===0)loadCMessages();
}
async function composeDiscard(){   // explicit Discard = throw away (delete the IMAP draft if any)
  const cmp=state.compose;
  if(cmp&&cmp._saving)await cmp._saving;   // a first save still in flight would leave its copy behind
  if(cmp&&cmp.draftId){ try{ await cpost("/api/draft",{draft_id:cmp.draftId,delete:true}); }catch(e){} loadFolders(); }
  if(cmp)cmp.dirty=false;
  composeClose(true);
}
async function replyTo(pk,all){
  const d=(state.lastBody&&state.lastBody.pk===pk)?state.lastBody:await api(`/api/message/${pk}/body`).catch(()=>null);
  if(!d||!d.ok){ toast("Couldn’t load the message"); return; }
  const toAddr=(d.reply_to&&addrOf(d.reply_to))||d.from_addr;
  const to=toAddr?[toAddr]:[]; let cc=[];
  if(all){ const seen=new Set([(toAddr||"").toLowerCase(),...state.myAddrs]);
    for(const a of [...parseAddrs(d.to),...parseAddrs(d.cc)]){ const k=a.toLowerCase();
      if(!seen.has(k)){ seen.add(k); cc.push(a); } } }
  composeOpen({mode:all?"replyall":"reply", from:d.account, to, cc, autoCc:cc.slice(),
    participants:[d.from_addr,...parseAddrs(d.to),...parseAddrs(d.cc)],   // not Reply-To: a redirect to a stranger gets marked
    subject:rePrefix(d.subject,"Re:"), origPk:pk,
    quote:{html:d.has_html?d.html:null, text:d.text, from:d.from_name||d.from_addr, date:d.date}});
}
async function forwardMsg(pk){
  const d=(state.lastBody&&state.lastBody.pk===pk)?state.lastBody:await api(`/api/message/${pk}/body`).catch(()=>null);
  if(!d||!d.ok){ toast("Couldn’t load the message"); return; }
  composeOpen({mode:"forward", from:d.account, to:[], subject:rePrefix(d.subject,"Fwd:"), origPk:pk,
    quote:{html:d.has_html?d.html:null, text:d.text, from:d.from_name||d.from_addr, date:d.date},
    fwdAttach:(d.attachments||[]).filter(a=>!(a.is_inline&&a.content_id)).length});
}
function renderChips(field){
  const cmp=state.compose; if(!cmp)return;
  fieldEl(field).innerHTML=cmp[field].map((a,i)=>chipHtml(field,a,i)).join("")
    +`<input class="ci" data-cf="${field}" placeholder="${field==="to"&&!cmp[field].length?"name@example.com":""}">`;
}
function chipHtml(field,x,i){ const cmp=state.compose, a=rcptAddr(x.v), nw=!x.bad&&knownIsNew(cmp,a);
  return `<span class="cm-chip${x.bad?" bad":""}${nw?" new":""}" data-addr="${esc(a)}"${nw?' title="No earlier mail with this address in MailMind"':""}>${esc(x.v)}`
    +(nw?'<span class="cm-new" aria-hidden="true">new</span><span class="visually-hidden">, new to you</span>':"")
    +`<button data-rmchip="${field}:${i}" aria-label="Remove ${esc(x.v)}${x.bad?", not a valid address":nw?", new to you":""}">×</button></span>`; }
function commitChip(field,raw){
  const cmp=state.compose; if(!cmp)return;
  const added=(raw||"").split(/[,;]/).map(s=>s.trim()).filter(Boolean);
  added.forEach(v=>{ cmp[field].push({v,bad:!looksLikeAddr(v)}); markEdited(cmp); });
  renderChips(field); const inp=fieldEl(field).querySelector(".ci"); if(inp)inp.focus();
  knownQueue(added,true); knownQueue([...cmp.to,...cmp.cc,...cmp.bcc].map(x=>x.v),false);   // a lookup that failed is asked again
  guardStale();
}
/* ---------- "new to you": recipients with no earlier mail either way (a soft mark, never a block) ---------- */
let _knownT=null; const _knownCache=new Map();   // page lifetime, known/self answers only
function knownIsNew(cmp,a){ const p=cmp&&cmp._people.get(a);
  return !!p&&!p.known&&!p.self&&!cmp._thin&&!state.myAddrs.has(a)&&!cmp.participants.has(a); }
function knownQueue(values,say){
  const cmp=state.compose; if(!cmp)return;
  for(const v of values){ const a=rcptAddr(v);
    if(!a||!looksLikeAddr(a)||state.myAddrs.has(a)||cmp.participants.has(a))continue;
    if(_knownCache.has(a)){ cmp._people.set(a,_knownCache.get(a)); continue; }
    if(cmp._people.has(a))continue;
    cmp._knownQ.add(a); if(say)cmp._newSay.add(a); }
  if(cmp._knownQ.size){ clearTimeout(_knownT); _knownT=setTimeout(()=>knownFlush(cmp),150); } }
async function knownFlush(cmp){   // one request for every queued address, never one per chip
  if(state.compose!==cmp)return;
  const addrs=[...cmp._knownQ].slice(0,100); addrs.forEach(a=>cmp._knownQ.delete(a));
  let d; try{ d=await cpost("/api/contacts/known",{addrs}); }catch(e){ return; }   // pending or failed: no mark at all
  if(state.compose!==cmp||!d||!d.ok)return;
  cmp._thin=!!d.thin;
  for(const [a,p] of Object.entries(d.items||{})){ const v={known:!!p.known,self:!!p.self};
    cmp._people.set(a,v); if(v.known||v.self)_knownCache.set(a,v); }
  knownPaint(cmp);
  const hits=[...cmp._newSay].filter(a=>knownIsNew(cmp,a)); cmp._newSay.clear();
  if(hits.length&&!guardOpen())guardSay(`New to you: ${guardList(hits)}.`);
  if(cmp._knownQ.size)knownFlush(cmp); }
function knownPaint(cmp){   // swap only the chips whose mark changed, so half-typed text and focus survive
  if(state.compose!==cmp)return;
  for(const el of $$("#composeModal .cm-chip[data-addr]")){
    const b=el.querySelector("[data-rmchip]"); if(!b||el.contains(document.activeElement))continue;
    const [f,i]=b.dataset.rmchip.split(":"), x=cmp[f]&&cmp[f][+i]; if(!x)continue;
    const nw=!x.bad&&knownIsNew(cmp,el.dataset.addr);
    if(nw!==el.classList.contains("new"))el.outerHTML=chipHtml(f,x,+i); } }
function removeChip(field,i){ state.compose[field].splice(i,1); markEdited(state.compose); renderChips(field); fieldEl(field).querySelector(".ci").focus(); guardStale(); }
/* ---------- recipient autocomplete (from your own mail history; all local) ---------- */
let _acItems=[], _acIdx=-1, _acField=null, _acTimer=null;
function acClose(){ const m=$("#acMenu"); if(m)m.remove(); _acItems=[]; _acIdx=-1; _acField=null; }
function acShow(input, field, items){
  acClose(); _acItems=items; _acField=field;
  const m=document.createElement("div"); m.className="ac-menu"; m.id="acMenu"; m.setAttribute("role","listbox");
  m.innerHTML=items.map((it,i)=>`<button type="button" role="option" class="ac-item" data-aci="${i}">
    <span class="ac-nm">${esc(it.name||it.addr)}</span><span class="ac-ad">${esc(it.addr)}</span></button>`).join("");
  document.body.appendChild(m);
  const r=input.getBoundingClientRect();
  m.style.left=r.left+"px"; m.style.top=(r.bottom+3)+"px"; m.style.width=Math.max(r.width,240)+"px";
  if(r.bottom+3+m.offsetHeight>window.innerHeight) m.style.top=(r.top-3-m.offsetHeight)+"px";
  m.addEventListener("mousedown",e=>{ const b=e.target.closest("[data-aci]"); if(b){ e.preventDefault(); acAccept(+b.dataset.aci); } });
}
function acMove(d){ if(!_acItems.length)return; _acIdx=(_acIdx+d+_acItems.length)%_acItems.length;
  $$("#acMenu .ac-item").forEach((el,i)=>{ el.classList.toggle("sel",i===_acIdx); if(i===_acIdx)el.scrollIntoView({block:"nearest"}); }); }
function acAccept(i){ const it=_acItems[i], f=_acField; acClose();
  if(it&&f){ const inp=fieldEl(f).querySelector(".ci"); commitChip(f,it.addr); if(inp)inp.value=""; } }
function addAttachments(files){
  const cmp=state.compose; if(!cmp)return;
  let total=cmp.attachments.reduce((s,a)=>s+a.size,0);
  [...files].forEach(f=>{
    if(total+f.size>25*1024*1024){ toast("Attachments too large (25 MB max)"); return; }
    total+=f.size; const r=new FileReader(), id=rid(); cmp._reading.set(id,f.name);
    r.onload=()=>{ cmp._reading.delete(id); cmp.attachments.push({filename:f.name, content_type:f.type||"application/octet-stream",
      size:f.size, b64:(r.result+"").split(",")[1]||""}); markEdited(cmp); if(state.compose===cmp){ renderAttach(); guardStale(); } };
    r.onerror=r.onabort=()=>{ cmp._reading.delete(id);
      if(state.compose===cmp){ toast(`Couldn’t add ${f.name}`); announce(`Couldn’t add ${f.name}.`); guardStale(); } };
    r.readAsDataURL(f);
  });
}
function removeAttachment(i){ state.compose.attachments.splice(i,1); markEdited(state.compose); renderAttach(); guardStale(); }
function renderAttach(){
  const cmp=state.compose, el=$("#cmpAttach"); if(!cmp){el.innerHTML="";return;}
  el.innerHTML=cmp.attachments.map((a,i)=>
    `<span class="cmp-att">${ico("clip")} ${esc(a.filename)} <span class="sz">${fmtSize(a.size)}</span><button data-rmatt="${i}" aria-label="Remove">×</button></span>`).join("");
}
function renderQuote(){
  const q=$("#cmpQuote"), cmp=state.compose;
  if(!cmp||!cmp.quote||(!cmp.quote.html&&!cmp.quote.text)){ q.hidden=true; q.innerHTML=""; return; }
  q.hidden=false; const lbl=qLabel(false);
  q.innerHTML=`<button class="cmp-qtoggle" id="cmpQToggle">${lbl}</button><div class="cmp-qbody" id="cmpQBody" hidden></div>`;
}
function qLabel(open){ const cmp=state.compose;
  return (open?"▾ ":"▸ ")+(cmp.mode==="forward"?"forwarded message":"quoted message")
    +(cmp.fwdAttach?` · ${cmp.fwdAttach} attachment${cmp.fwdAttach>1?"s":""}`:""); }
function toggleQuote(){
  const body=$("#cmpQBody"), cmp=state.compose; if(!body||!cmp)return;
  const open=body.hidden; body.hidden=!open; $("#cmpQToggle").textContent=qLabel(open);
  if(open&&!body.dataset.loaded){ body.dataset.loaded="1";
    if(cmp.quote.html){ body.innerHTML=`<iframe sandbox="" srcdoc="${esc(bodyDoc(cmp.quote.html,true))}"></iframe>`;
      const fr=body.querySelector("iframe"); fr.addEventListener("load",()=>{try{fr.style.height=Math.min((fr.contentWindow.document.body.scrollHeight||300)+24,900)+"px";}catch(e){}}); }
    else body.innerHTML=`<div class="cmp-qtext">${esc(cmp.quote.text||"(no preview)")}</div>`; }
}
function setComposePill(){ const p=$("#cmpPill"); const cmp=state.compose;
  if(!cmp||cmp.status==="idle"){ p.className="sendpill"; p.textContent=""; return; }
  p.className="sendpill show "+cmp.status; p.textContent={sending:"↑ Sending…"}[cmp.status]||""; }
/* ---------- send-time checks: one synchronous look before an irreversible send ----------
   The pure block below (between the markers) touches no DOM and no state; test_send_guard.py runs
   it in node. Lines are "confirm" (Send anyway after a short arm delay), "hard" (no way past it),
   or "soft" (shown only alongside the others, never stops a send). */
let _guardArmT=null, _gsT=null;
function guardOpen(){ return !!(state.compose&&state.compose._guard); }
function guardSnapshot(cmp,sendAfter){
  const n=x=>rcptAddr(x.v);
  return {mode:cmp.mode, subject:$("#cmpSubject").value, subject0:cmp.subject0, body:$("#cmpBody").value,
    sigBlock:cmp._sigBlock||"", nAttach:cmp.attachments.length, fwdAttach:cmp.fwdAttach||0,
    reading:[...cmp._reading.values()], sendAfter:sendAfter||0, now:Date.now()/1000,
    to:cmp.to.map(n), cc:cmp.cc.map(n), mine:[...state.myAddrs,...[...cmp._people].filter(([,p])=>p.self).map(([a])=>a)],
    autoCc:cmp.autoCc, ack:cmp._ack,
    fresh:[...new Set([...cmp.to,...cmp.cc,...cmp.bcc].filter(x=>!x.bad).map(n))].filter(a=>knownIsNew(cmp,a))}; }
function _guardFocusDesc(el){   // where to put focus back after Keep editing (chip inputs are re-rendered, so by field)
  if(!el||!$("#composeModal").contains(el)||$("#cmpGuard").contains(el))return null;
  return el.classList.contains("ci")?{field:el.dataset.cf}:{el}; }
function _guardFocusEl(d,sendAfter){
  if(d&&d.field)return fieldEl(d.field).querySelector(".ci");
  if(d&&d.el&&d.el.isConnected&&!d.el.closest("[hidden]"))return d.el;
  return sendAfter>0?$("#cmpSchedule"):$("#cmpBody"); }
function guardSay(msg){   // clear, then set: an identical repeat is read again
  const r=$("#srLive"); clearTimeout(_gsT); r.textContent=""; _gsT=setTimeout(()=>{r.textContent=msg;},150); }
function paintSendBtn(mode){
  const b=$("#cmpSend"), g=state.compose&&state.compose._guard, later=!!(g&&g.sendAfter), pa=state.compose&&state.compose._pendingAfter;
  if(mode==="plain"||mode==="blocked"){ b.textContent=pa?"Schedule ▸":"Send ▸";
    b.title=mode==="blocked"?"Fix the line above first":pa?`Schedule for ${fmtWhen(pa)} (⌘/Ctrl+Enter)`:"Send (⌘/Ctrl+Enter)";
    for(const x of [b,$("#cmpGuardGo")]){ x.removeAttribute("aria-disabled"); x.classList.remove("cg-arming"); }
    if(mode==="blocked"){ b.setAttribute("aria-disabled","true"); b.classList.add("cg-arming"); } return; }
  b.textContent=later?"Schedule anyway ▸":"Send anyway ▸";
  b.title=later?`Schedule for ${fmtWhen(g.sendAfter)} anyway (⌘/Ctrl+Enter)`:"Send anyway (⌘/Ctrl+Enter)";
  for(const x of [b,$("#cmpGuardGo")]){
    if(mode==="arming"){ x.setAttribute("aria-disabled","true"); x.classList.add("cg-arming"); }
    else { x.removeAttribute("aria-disabled"); x.classList.remove("cg-arming"); } } }
function showSendGuard(warns,sendAfter,back,lead){
  const cmp=state.compose; if(!cmp)return;
  warns.forEach(w=>{ if(w.key==="late")w.whenText=fmtWhen(w.when); });
  const hard=guardHard(warns), later=sendAfter>0;
  cmp._guard={keys:warns.filter(x=>x.block!=="soft").map(x=>x.key), fanN:(warns.find(x=>x.key==="fanout")||{}).n||0,
    sendAfter:sendAfter||0, back, hard, shownAt:performance.now()};
  $("#cmpGuardHd").textContent=later?"Not scheduled yet":"Not sent yet";
  const lines=warns.map(w=>({w,...guardLine(w)}));
  $("#cmpGuardList").innerHTML=lines.map(({w,text,fix})=>`<li class="cg-line${w.block==="soft"?" cg-soft":""}"><span>${esc(text)}</span>`
    +(fix?`<button type="button" class="cg-fix" data-gfix="${fix.kind}"${w.field?` data-gf="${w.field}"`:""}>${esc(fix.label)}</button>`:"")+`</li>`).join("");
  const go=$("#cmpGuardGo"); go.hidden=hard; go.textContent=later?"Schedule anyway":"Send anyway";
  $("#cmpGuardHint").textContent=hard?"Esc keeps editing":`⌘/Ctrl+Enter ${later?"schedules":"sends"} anyway · Esc keeps editing`;
  $("#cmpGuard").hidden=false;
  paintSendBtn(hard?"blocked":"arming"); clearTimeout(_guardArmT);
  if(!hard)_guardArmT=setTimeout(()=>{ if(guardOpen())paintSendBtn("armed"); },GUARD_ARM_MS);
  $("#cmpGuard").focus();
  const spoken=hard?"Press Escape to keep editing.":`Press Command or Control Enter to ${later?"schedule":"send"} anyway, or Escape to keep editing.`;
  cmp._guard.said=lines.map(l=>l.text).join(" ")+" "+spoken;
  guardSay((lead?lead+" ":"")+cmp._guard.said); }
function guardReset(){   // DOM only; safe with no composer open
  clearTimeout(_guardArmT); clearTimeout(_gsT);   // a line still queued for the screen reader goes with the strip
  const g=$("#cmpGuard"); g.hidden=true; $("#cmpGuardList").innerHTML=""; paintSendBtn("plain"); }
function hideSendGuard(){
  const had=$("#cmpGuard").contains(document.activeElement);
  if(state.compose)state.compose._guard=null;
  guardReset(); if(had)$("#cmpSend").focus(); }
function dismissSendGuard(){   // Esc, Keep editing, Back: return to where you were
  const g=state.compose&&state.compose._guard; if(!g)return;
  const a=document.activeElement, here=!a||a===document.body||$("#cmpGuard").contains(a);   // moved elsewhere meanwhile? stay there
  const t=here?_guardFocusEl(g.back,g.sendAfter):null; hideSendGuard(); if(t)t.focus(); }
function guardStale(){   // any real edit retires the strip; Send checks again from scratch
  const cmp=state.compose, g=cmp&&cmp._guard; if(!g)return;
  if(g.keys.includes("reading")&&cmp._reading.size){   // still reading: the strip stays, naming what's left
    const left=[...cmp._reading.values()], el=$("#cmpGuardList .cg-line span");
    if(el)el.textContent=guardLine({key:"reading",name:left[0],more:left.length-1}).text;
    g.said=(el?el.textContent:"")+" Press Escape to keep editing."; return; }
  hideSendGuard(); }
function _guardRun(cmp,sendAfter){
  try{ return guardCheck(guardSnapshot(cmp,sendAfter)); }
  catch(err){ console.error("[guard]",err); return cmp._ack.unchecked?[]:[{key:"unchecked",block:"confirm"}]; } }
function confirmSendGuard(src,e){
  const cmp=state.compose, g=cmp&&cmp._guard; if(!g)return;
  if(g.hard){ if(!(e&&e.repeat)){ $("#cmpGuard").focus(); guardSay(g.said); } return; }   // nothing to wave through: say why again
  if((e&&e.repeat)||performance.now()-g.shownAt<GUARD_ARM_MS)return;   // a double-click (the strip shifts the layout under it) or held keys never confirm
  const now=_guardRun(cmp,g.sendAfter), conf=now.filter(x=>x.block==="confirm"), fan=now.find(x=>x.key==="fanout");
  if(guardHard(now)||conf.some(x=>!g.keys.includes(x.key))||(fan&&fan.n>g.fanN)){   // the time passed, or an AI edit changed things
    showSendGuard(now,g.sendAfter,g.back,"Something changed."); return; }
  g.keys.forEach(k=>{ if(k==="fanout")cmp._ack.fanout=Math.max(cmp._ack.fanout,g.fanN); else if(k in cmp._ack)cmp._ack[k]=true; });
  const after=g.sendAfter; hideSendGuard(); composeSend(after); }
function guardTrimCc(){   // keep only the Cc you added yourself; it's a plain reply after that
  const cmp=state.compose; if(!cmp||!cmp.autoCc)return;
  const n=x=>rcptAddr(x.v), before=cmp.cc.length;
  cmp.cc=cmp.cc.filter(x=>!cmp.autoCc.includes(n(x))); const k=before-cmp.cc.length;
  renderChips("cc"); cmp.mode="reply"; $("#cmpTitle").textContent="Reply"; markEdited(cmp);
  hideSendGuard(); $("#cmpSend").focus();
  const m=guardFanout(cmp.to.map(n),cmp.cc.map(n),[...state.myAddrs]);
  guardSay(`Removed ${k} ${k===1?"person":"people"}. This reply goes to ${m} ${m===1?"person":"people"}.`); }
function guardFix(kind,btn){
  const cmp=state.compose; if(!cmp)return;
  if(kind==="trim"){ guardTrimCc(); return; }
  hideSendGuard();
  if(kind==="subject")$("#cmpSubject").focus();
  else if(kind==="attach"){ $("#cmpAttachBtn").focus(); $("#cmpFile").click(); }
  else if(kind==="review"){ $("#cmpCcRow").hidden=false; $("#cmpBccRow").hidden=false; const i=fieldEl("cc").querySelector(".ci"); if(i)i.focus(); }
  else if(kind==="time")openSendLaterMenu($("#cmpSchedule"));
  else if(kind==="to"){ const i=fieldEl("to").querySelector(".ci"); if(i)i.focus(); }
  else if(kind==="bad"){ const f=btn.dataset.gf, i=f?cmp[f].findIndex(x=>x.bad):-1; if(i<0)return;
    const v=cmp[f][i].v; cmp[f].splice(i,1); if(f!=="to"){ $("#cmpCcRow").hidden=false; $("#cmpBccRow").hidden=false; }
    renderChips(f); const inp=fieldEl(f).querySelector(".ci"); inp.value=v; inp.focus(); } }   // back in the input to fix
async function composeSend(sendAfter){
  const cmp=state.compose; if(!cmp||cmp.status==="sending")return;
  const back=_guardFocusDesc(document.activeElement);   // before the chip commit below moves focus
  cmp._pendingAfter=(typeof sendAfter==="number"&&sendAfter>0)?sendAfter:0;   // kept if a check holds this attempt
  ["to","cc","bcc"].forEach(f=>{ const inp=fieldEl(f).querySelector(".ci"); if(inp&&inp.value.trim()){ commitChip(f,inp.value); inp.value=""; } });
  cmp.subject=$("#cmpSubject").value; cmp.body=$("#cmpBody").value; cmp.from=$("#cmpFrom").value;
  if(!cmp.to.length&&!cmp.cc.length&&!cmp.bcc.length){ showSendGuard([{key:"norcpt",block:"hard"}],sendAfter,back); return; }
  const bad=["to","cc","bcc"].flatMap(f=>cmp[f].filter(x=>x.bad).map(x=>({f,v:x.v})));
  if(bad.length){ showSendGuard([{key:"bad",block:"hard",v:bad[0].v,more:bad.length-1,field:bad[0].f}],sendAfter,back); return; }
  const warns=_guardRun(cmp,sendAfter);
  if(guardBlocks(warns)){ showSendGuard(warns,sendAfter,back); return; }
  if(cmp._guard)hideSendGuard();
  const scheduled=typeof sendAfter==="number"&&sendAfter>0;
  const payload={ account:cmp.from, mode:cmp.mode, idempotency_key:cmp.idk, draft_id:cmp.draftId||null,
    to:cmp.to.map(x=>x.v), cc:cmp.cc.map(x=>x.v), bcc:cmp.bcc.map(x=>x.v),
    subject:cmp.subject, text:cmp.body, in_reply_to_pk:cmp.origPk, send_after:(scheduled?sendAfter:0),
    attachments:cmp.attachments.map(a=>({filename:a.filename,content_type:a.content_type,b64:a.b64})),
    ...resumeFields(cmp.resume) };
  const replyPk=(cmp.mode==="reply"||cmp.mode==="replyall")?cmp.origPk:null;
  const snap=composeSnap(cmp);   // Undo → resend unchanged isn't asked again
  const saving=cmp._saving;   // a save still in flight: the send must name the copy it leaves
  cmp.dirty=false; state.failedSend=null; composeClose(true);
  showSendFloat("sending", scheduled?(ico("clock")+" Scheduling for "+fmtWhen(sendAfter)+"…"):"↑ Sending…");
  try{
    if(saving){ await saving; payload.draft_id=cmp.draftId||null; namesItself(payload,cmp);
      Object.assign(snap,{draftId:cmp.draftId,resume:cmp.resume,attachments:cmp.attachments,fwdAttach:cmp.fwdAttach}); }
    const r=await cpost("/api/send",payload);
    if(!r.ok)throw new Error(r.error||"send failed");
    if(scheduled){ showSendFloat("sent",ico("clock")+" Scheduled · "+fmtWhen(sendAfter)); setTimeout(hideSendFloat,2600); }
    else startUndoCountdown(r.outbox_id, snap, r.recipients);   // grace window with Undo
    if(replyPk){ const it=cfind(replyPk); if(it)it.answered=1; _draftReady.delete(replyPk); _draftingPks.delete(replyPk);
      updateReaderDraftChip(replyPk);   // repaint the on-screen chip to plain Reply — the sent draft must not be re-offered
      renderClientList(); }
    loadOutbox();
    if(state.account===payload.account&&/sent/i.test(state.folder)) loadCMessages();
  }catch(e){
    state.failedSend={...snap,draftId:cmp.draftId};
    showSendFloat("failed",ico("alert")+" Send failed — "+esc(e.message||""),true);
  }
}
function fmtWhen(epoch){   // friendly local time for a scheduled send
  const d=new Date(epoch*1000), now=new Date();
  const t=d.toLocaleTimeString([], {hour:"numeric", minute:"2-digit"});
  if(d.toDateString()===now.toDateString()) return "today "+t;
  const tmw=new Date(now); tmw.setDate(now.getDate()+1);
  if(d.toDateString()===tmw.toDateString()) return "tomorrow "+t;
  return d.toLocaleDateString([], {weekday:"short", month:"short", day:"numeric"})+" "+t;
}
function sendLaterPresets(){
  const now=new Date(), mk=d=>Math.floor(d.getTime()/1000);
  const ev=new Date(now); ev.setHours(18,0,0,0); if(ev<=now)ev.setDate(ev.getDate()+1);
  const tmw=new Date(now); tmw.setDate(now.getDate()+1); tmw.setHours(8,0,0,0);
  const mon=new Date(now); mon.setDate(now.getDate()+(((8-now.getDay())%7)||7)); mon.setHours(8,0,0,0);
  return [["In 1 hour", mk(new Date(now.getTime()+3600e3))], ["This evening", mk(ev)],
          ["Tomorrow 8 AM", mk(tmw)], ["Monday 8 AM", mk(mon)]];
}
function openSendLaterMenu(anchor){
  closeSendLaterMenu();
  const m=document.createElement("div"); m.className="snooze-menu"; m.id="sendLaterMenu"; m.setAttribute("role","menu");
  m.innerHTML=(state.compose&&state.compose._pendingAfter?`<button role="menuitem" data-sl="0"><span class="snz-n">·</span> Send now instead</button>`:"")
    +sendLaterPresets().map(([l,ep],i)=>`<button role="menuitem" data-sl="${ep}"><span class="snz-n">${i+1}</span> ${l}</button>`).join("")
    +`<label class="snz-custom">Custom… <input type="datetime-local" id="slWhen"></label>`;
  document.body.appendChild(m);
  const r=anchor.getBoundingClientRect();
  m.style.top=(r.top-8-m.offsetHeight)+"px"; m.style.left=Math.min(r.left,window.innerWidth-220)+"px";
  if(parseFloat(m.style.top)<8) m.style.top=(r.bottom+4)+"px";
  const f=m.querySelector("button"); if(f)f.focus();
  m.addEventListener("click",e=>{ const b=e.target.closest("[data-sl]"); if(b){ closeSendLaterMenu(); composeSend(+b.dataset.sl); } e.stopPropagation(); });
  $("#slWhen").addEventListener("change",e=>{ const v=e.target.value; if(v){ const ep=Math.floor(new Date(v).getTime()/1000); if(ep>Date.now()/1000){ closeSendLaterMenu(); composeSend(ep); } else toast("Pick a future time"); } });
  setTimeout(()=>document.addEventListener("click",closeSendLaterMenu,{once:true}),0);
}
function closeSendLaterMenu(){ const m=$("#sendLaterMenu"); if(m)m.remove(); document.removeEventListener("click",closeSendLaterMenu); }   // a stale outside-click must not shut the next one
/* ---------- AI draft: a remembered tone, and an optional "what to say" ---------- */
const DRAFT_TONES={brief:"Brief", balanced:"Balanced", warm:"Warm", formal:"Formal"};   // draft_reply._TONES
function draftTone(){ const t=localStorage.getItem("mm_draft_tone"); return DRAFT_TONES[t]?t:"balanced"; }
function paintDraftBtn(){
  const t=draftTone(), b=$("#cmpAiDraft"); if(!b||b.disabled)return;
  b.querySelector(".ai-t").textContent=t==="balanced"?"Draft":"Draft · "+DRAFT_TONES[t].toLowerCase();
  b.title=`Draft a reply with AI, ${DRAFT_TONES[t].toLowerCase()} tone`;
}
async function aiDraftReply(opts){   // opts: {tone, instruction, unless, quiet}; a plain click drafts in the remembered tone
  opts=opts||{};
  const cmp=state.compose; if(!cmp)return;
  if(cmp.origPk==null){ toast("Open a reply to draft one"); return; }
  const btn=$("#cmpAiDraft"); if(!btn||btn.disabled)return;   // a generation is already in flight for this composer
  const t=btn.querySelector(".ai-t"); btn.disabled=$("#cmpAiMore").disabled=true; t.textContent="Drafting…";
  try{
    const d=await cpost("/api/ai/draft-reply",{pk:cmp.origPk, account:cmp.from, tone:opts.tone||draftTone(), instruction:opts.instruction||""});
    if(state.compose!==cmp)return;   // closed (or replaced by another reply) while drafting: not this composer's text
    if(!d.ok)throw new Error(d.retry?"AI is unavailable":(d.error||"draft failed"));
    if(opts.unless!=null&&$("#cmpBody").value!==opts.unless)return;   // a quick reply you've started editing: yours wins
    const ta=$("#cmpBody"), had=ta.value.trim()&&ta.value.trim()!==(cmp._sigBlock||"").trim();
    if(had){ cmp._rwUndo={value:ta.value, sel:[ta.selectionStart,ta.selectionEnd]}; $("#cmpRwUndo").hidden=false; }   // what you'd written is one Undo away
    const body=d.body+(cmp._sigBlock||"");   // keep the signature below the AI-written reply
    ta.value=body; cmp.body=body; markEdited(cmp); ta.focus(); guardStale();   // a scripted write fires no input event
    toast("Draft ready — review and edit before sending");
  }catch(e){ if(state.compose===cmp&&!opts.quiet)toast("Couldn’t draft: "+(e.message||"")); }
  finally{ btn.disabled=$("#cmpAiMore").disabled=false; paintDraftBtn(); }
}
/* ---------- quick replies: a one-line answer at once, and an AI version in its place when one comes ---------- */
const QUICK=[   // [key, chip, the line the reply starts as, what the AI is asked to write instead]
  ["yes","Yes, that works","Yes, that works for me.","Say yes: agree to or confirm what they asked."],
  ["no","Can’t, sorry","Sorry, I can’t make that work.","Politely say no to what they asked, without inventing a reason."],
  ["thanks","Thanks!","Thanks!","Just thank them, briefly and warmly."],
  ["time","Ask for a time","Could you send me a few times that work for you?","Ask them to suggest a few times that suit them; don’t propose any yourself."]];
function canQuickReply(addr,it){   // a person you could answer: not you, not a no-reply robot, not junk or a receipt
  const a=String(addr||"").toLowerCase();
  if(!a||(state.myAddrs&&state.myAddrs.has(a))||/^(no-?reply|do-?not-?reply|mailer-daemon|postmaster|notifications?|bounces?)\b/.test(a))return false;
  return !(it&&(it.is_junk||/^(receipts|automated|newsletters|spam|blocked)/.test(it.bucket||"")));
}
function quickRepliesHtml(pk){
  return `<div class="qr-row" role="group" aria-label="Quick replies"><span class="qr-k">${ico("reply")}Quick reply</span>${QUICK.map(([k,l])=>
    `<button class="qr" data-qr="${k}" data-qpk="${pk}">${esc(l)}</button>`).join("")}</div>`;
}
async function quickReply(pk,kind){
  const q=QUICK.find(x=>x[0]===kind); if(!q||composeIsOpen())return;
  await replyTo(pk,false);
  const cmp=state.compose; if(!cmp||cmp.origPk!==pk)return;
  const ta=$("#cmpBody"), start=q[2]+(cmp._sigBlock||"");
  ta.value=start; cmp.body=start; ta.focus(); ta.setSelectionRange(q[2].length,q[2].length);
  aiDraftReply({tone:"brief", instruction:q[3]+" One or two sentences.", unless:start, quiet:true});   // no AI → the line stays, silently
}
function openDraftMenu(anchor){
  closeDraftMenu();
  const t=draftTone(), m=document.createElement("div");
  m.className="snooze-menu draft-menu"; m.id="draftMenu"; m.setAttribute("role","dialog"); m.setAttribute("aria-label","Draft options");
  m.innerHTML=`<div class="dm-k" id="dmToneK">Tone</div>
    <div class="dm-tones" role="group" aria-labelledby="dmToneK">${Object.entries(DRAFT_TONES).map(([k,l])=>
      `<button data-tone="${k}" aria-pressed="${k===t}">${l}</button>`).join("")}</div>
    <label class="dm-k" for="dmSay">What should it say? <small>optional</small></label>
    <input id="dmSay" maxlength="400" autocomplete="off" placeholder="e.g. yes, but not before 3 pm">
    <button class="dm-go" id="dmGo">${ico("wind")}Draft</button>`;
  document.body.appendChild(m);
  const r=anchor.getBoundingClientRect();
  m.style.top=Math.max(8,r.top-8-m.offsetHeight)+"px"; m.style.left=Math.max(8,Math.min(r.left,window.innerWidth-m.offsetWidth-8))+"px";
  anchor.setAttribute("aria-expanded","true");
  const go=()=>{ const instruction=$("#dmSay").value.trim(); closeDraftMenu(); aiDraftReply({instruction}); };
  m.addEventListener("click",e=>{ e.stopPropagation(); const b=e.target.closest("[data-tone]");
    if(b){ localStorage.setItem("mm_draft_tone",b.dataset.tone); m.querySelectorAll("[data-tone]").forEach(x=>x.setAttribute("aria-pressed",String(x===b))); paintDraftBtn(); return; }
    if(e.target.closest("#dmGo"))go(); });
  $("#dmSay").addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); e.stopPropagation(); go(); } });   // ⌘Enter too: it drafts here, never reaching the composer's send
  $("#dmSay").focus();
  setTimeout(()=>{ if(m.isConnected)document.addEventListener("click",_draftMenuOutside); },0);
}
function _draftMenuOutside(e){ if(!e.target.closest("#draftMenu"))closeDraftMenu(); }
function closeDraftMenu(refocus){
  const m=$("#draftMenu"); if(!m)return;
  m.remove(); document.removeEventListener("click",_draftMenuOutside);
  const b=$("#cmpAiMore"); b.setAttribute("aria-expanded","false"); if(refocus)b.focus();
}
async function aiRewrite(op){
  const cmp=state.compose; if(!cmp)return;
  const ta=$("#cmpBody"), full=ta.value, s=ta.selectionStart, e=ta.selectionEnd, hasSel=e>s;
  const text=hasSel?full.slice(s,e):full;
  if(!text.trim()){ toast("Write something to rewrite first"); return; }
  let lang="English";
  if(op==="translate"){ lang=(prompt("Translate to which language?","Spanish")||"").trim(); if(!lang)return; }
  const btns=$$("#cmpAiRow .airw"); btns.forEach(b=>b.disabled=true);
  const row=$("#cmpAiRow"); const lbl=$("#cmpAiRow .air-t"); const prev=lbl.textContent; lbl.textContent="Rewriting…";
  try{
    const d=await cpost("/api/ai/rewrite",{text,op,lang});
    if(state.compose!==cmp)return;   // closed while rewriting: the textarea now belongs to another composer (or none)
    if(!d.ok)throw new Error(d.retry?"AI is unavailable":(d.error||"rewrite failed"));
    cmp._rwUndo={value:full, sel:[s,e]};                       // one-step undo of the prior text
    const nv=hasSel?(full.slice(0,s)+d.text+full.slice(e)):d.text;
    ta.value=nv; cmp.body=nv; markEdited(cmp); guardStale();
    if(hasSel){ ta.selectionStart=s; ta.selectionEnd=s+d.text.length; }
    $("#cmpRwUndo").hidden=false; ta.focus();
    toast((hasSel?"Selection":"Message")+" rewritten");
  }catch(err){ if(state.compose===cmp)toast("Couldn’t rewrite: "+(err.message||"")); }
  finally{ btns.forEach(b=>b.disabled=false); lbl.textContent=prev; }
}
function aiRewriteUndo(){
  const cmp=state.compose; if(!cmp||!cmp._rwUndo)return;
  const ta=$("#cmpBody"); ta.value=cmp._rwUndo.value; cmp.body=ta.value; guardStale();
  ta.selectionStart=cmp._rwUndo.sel[0]; ta.selectionEnd=cmp._rwUndo.sel[1];
  cmp._rwUndo=null; $("#cmpRwUndo").hidden=true; ta.focus(); toast("Back to what you had");
}
let _roomBusy=false;
async function makeRoom(open){   // open() replaces the open composer once its save lands → false if it stays
  const cmp=state.compose; if(cmp&&_roomBusy)return false;
  if(cmp&&cmp.dirty){ _roomBusy=true; let ok;
    try{ do{ ok=await composeSaveDraft(true); }while(ok&&state.compose===cmp&&cmp.dirty); }   // typed during the save: that too
    finally{ _roomBusy=false; }
    if(state.compose&&state.compose!==cmp)return false;   // another opened meanwhile (one closed meanwhile saved itself)
    if(ok===false&&state.compose){ const m="The message you’re writing isn’t saved, so it stays open";
      toast(m); announce(m); return false; } }
  if(state.compose)composeClose(true);   // saved, or nothing worth saving
  open(); return true;   // in the same task as the close: the Back trap never sees the gap
}
async function reopenFailed(){
  const f=state.failedSend; if(!f)return;
  await makeRoom(()=>{   // if the open message can't be saved, the float and its Edit stay for a second try
    if(state.failedSend===f)hideSendFloat();   // a send that failed during the save keeps its float
    composeOpen({mode:f.mode, from:f.from, to:f.to.map(x=>x.v), cc:f.cc.map(x=>x.v), bcc:f.bcc.map(x=>x.v),
      subject:f.subject, origPk:f.origPk, quote:f.quote, fwdAttach:f.fwdAttach, draftId:f.draftId, resume:f.resume, noSig:true,
      autoCc:f.autoCc, subject0:f.subject0, participants:f.participants, ack:f.ack});
    state.compose.attachments=f.attachments; $("#cmpBody").value=f.body; adoptSig(f.body, f.from); renderAttach();
    markEdited(state.compose);   // nothing holds this text but the composer: closing it saves
    if(f.bcc.length){ $("#cmpCcRow").hidden=false; $("#cmpBccRow").hidden=false; } });
}
function showSendFloat(cls,text,retry){ const el=$("#sendFloat"); el.hidden=false; el.className="send-float "+cls;
  el.innerHTML=`<span>${text}</span>`+(retry?`<button id="sfEdit">Edit</button><button id="sfDismiss">Dismiss</button>`:""); }
function hideSendFloat(){ $("#sendFloat").hidden=true; }
/* ---------- undo (archive / trash / junk / move / snooze) ---------- */
let _undoFloatT=null;
// one id per bulk batch -> one z reverses all. Unique across reloads: the server's undo stack outlives
// the page, and a counter restarting at ug1 merged a new batch into an older one still on top.
function undoGroup(){ return "ug-"+((self.crypto&&crypto.randomUUID)?crypto.randomUUID():rid()); }
function showUndoFloat(label){
  const el=$("#undoFloat"); if(!el)return; el.hidden=false; el.className="send-float undo-float";
  el.innerHTML=`<span>${esc(label)}</span><button id="ufUndo">Undo · <kbd>z</kbd></button>`;
  $("#ufUndo").onclick=()=>{ hideUndoFloat(); undo(); };
  clearTimeout(_undoFloatT); _undoFloatT=setTimeout(hideUndoFloat,8000);
}
function hideUndoFloat(){ const el=$("#undoFloat"); if(el)el.hidden=true; clearTimeout(_undoFloatT); }
async function undo(){
  let d; try{ d=await cpost("/api/undo",{}); }
  catch(e){ toast("Couldn’t undo"); return; }
  if(d&&d.empty){ toast("Nothing to undo"); return; }
  hideUndoFloat();
  if(!d||!d.ok){ toast((d&&d.error)||"Couldn’t undo"); announce&&announce("Undo failed"); }
  else if(d.failed){ const m=`${d.failed} couldn’t be restored — press z to try again`;
    toast(m); announce&&announce(m); }
  else { toast((d.label||"Action")+" undone"); announce&&announce((d.label||"Action")+" undone"); }
  if(state.allInbox)loadAllInboxes();   // refresh whichever view shows the restored item
  else if(state.searching)runSearch();
  else loadCMessages();
  loadFolders&&loadFolders();
}
let _undoIv=null;
function startUndoCountdown(oid, snap, rcpts){
  clearInterval(_undoIv); let left=10; const el=$("#sendFloat");
  const paint=()=>{ el.hidden=false; el.className="send-float sending";
    el.innerHTML=`<span>↑ Sending in ${left}s…</span><button id="sfUndo">Undo</button>`;
    $("#sfUndo").onclick=async()=>{ clearInterval(_undoIv);
      try{ const d=await cpost("/api/outbox/cancel",{id:oid});
        if(d.ok){ hideSendFloat(); if(snap)reopenSend(snap); toast(snap?"Send undone":"RSVP not sent"); loadOutbox(); }
        else { showSendFloat("sent","✓ Sent — too late to undo"); setTimeout(hideSendFloat,2200); } }
      catch(e){ showSendFloat("sent","✓ Sent"); setTimeout(hideSendFloat,2000); } }; };
  paint();
  _undoIv=setInterval(()=>{ left--; if(left<=0){ clearInterval(_undoIv);
      showSendFloat("sent","✓ Sent"+(rcpts?` · ${rcpts} recipient${rcpts>1?"s":""}`:"")); setTimeout(hideSendFloat,2400); loadOutbox(); }
    else paint(); },1000);
}
async function reopenSend(snap){
  const ok=await makeRoom(()=>{
    composeOpen({mode:snap.mode, from:snap.from, to:snap.to.map(x=>x.v), cc:snap.cc.map(x=>x.v), bcc:snap.bcc.map(x=>x.v),
      subject:snap.subject, origPk:snap.origPk, quote:snap.quote, fwdAttach:snap.fwdAttach, draftId:snap.draftId, resume:snap.resume, noSig:true,
      autoCc:snap.autoCc, subject0:snap.subject0, participants:snap.participants, ack:snap.ack});
    state.compose.attachments=snap.attachments; $("#cmpBody").value=snap.body; adoptSig(snap.body, snap.from); renderAttach();
    markEdited(state.compose);   // nothing holds this text but the composer: closing it saves
    if(snap.bcc.length){ $("#cmpCcRow").hidden=false; $("#cmpBccRow").hidden=false; } });
  if(!ok){ state.failedSend=snap; showSendFloat("failed",ico("alert")+" Send undone — not reopened yet",true); }   // the open message couldn't be saved: keep it, and this one a click away
}
let _outboxNudge=null;
function nudgeOutbox(){ clearTimeout(_outboxNudge); _outboxNudge=setTimeout(loadOutbox,120); }   // a bulk action queued offline tells this once per message
async function loadOutbox(){
  let d,p; try{ [d,p]=await Promise.all([api("/api/outbox?limit=50"),api("/api/pending")]); }catch(e){ return; }
  state.outbox=d.items||[]; state.pending=p; state.draftsWaiting=d.drafts||[];
  const active=state.outbox.filter(o=>o.state==="queued"||o.state==="sending").length+p.waiting+state.draftsWaiting.length;
  const failed=state.outbox.filter(o=>o.state==="error").length+p.stuck;
  const btn=$("#outboxBtn");
  if(active||failed){ btn.hidden=false; $("#outboxN").textContent=active+failed; btn.classList.toggle("has-fail",failed>0); }
  else btn.hidden=true;
  if(!$("#outboxFly").hidden) renderOutbox();
}
function toggleOutbox(){ const f=$("#outboxFly"); f.hidden=!f.hidden; if(!f.hidden){ renderOutbox(); loadOutbox(); } }
const PENDING_OP={trash:"Move to Trash",archive:"Archive",junk:"Mark as junk",move:"Move"};
const PENDING_FLAG={seen:["Mark unread","Mark read"],flagged:["Remove star","Star"],answered:["Mark unanswered","Mark answered"]};
const PENDING_WHY={refused:"the server refused it",no_folder:"no folder to move it to","account removed":"its account is gone"};
function pendingWhat(p){
  if(p.op==="flag"){ const [k,on]=Object.entries(p.flags||{})[0]||[]; return (PENDING_FLAG[k]||[])[on?1:0]||"Change"; }
  return p.op==="move"&&p.dest?`Move to ${p.dest}`:(PENDING_OP[p.op]||"Change");
}
function pendingRow(p){
  const stuck=p.state==="error";
  const why=stuck?(PENDING_WHY[p.error]||"couldn’t reach the server"):readOnly()?"waits for a full run":p.attempts?`tried ${p.attempts}×, trying again`:"once the server is back";
  return `<div class="ob-row"><span class="ob-st ${stuck?"error":p.state==="running"?"sending":"queued"}">${stuck?"stuck":p.state==="running"?"trying":"waiting"}</span>
    <span class="ob-sub">${esc(pendingWhat(p))}: ${esc(p.subject||"(no subject)")}<br><span class="ob-to ob-why">${esc(why)} · ${esc(p.account)}</span></span>
    ${stuck?`<button class="ob-retry" data-pending-retry="${p.id}">Retry</button>`:""}<button class="ob-retry" data-pending-discard="${p.id}">Discard</button></div>`;
}
function draftWaitRow(d){
  const goes=d.discarding?"leaves the server":"reaches the server";
  const why=readOnly()?`${goes} on a full run`:d.attempts?`tried ${d.attempts}×, trying again`:`${goes} once it’s back`;
  return `<div class="ob-row"><span class="ob-st queued">${d.discarding?"discarded":"draft"}</span>
    <span class="ob-sub">${esc(d.subject||"(no subject)")}<br><span class="ob-to ob-why">${esc(why)} · ${esc(d.account)}</span></span>
    ${d.discarding?"":`<button class="ob-retry" data-draft-edit="${d.id}">Edit</button><button class="ob-retry" data-draft-discard="${d.id}">Discard</button>`}</div>`;
}
async function discardWaitingDraft(id){
  try{ await cpost("/api/draft",{draft_id:id,delete:true}); toast("Draft discarded"); }
  catch(e){ toast("Couldn’t discard it"); }
  loadOutbox(); refreshDrafts();
}
async function pendingAct(what,id){
  try{ const r=await cpost(`/api/pending/${what}`,{id}); toast(!r.ok?"It has already settled":what==="retry"?"Trying again…":"Discarded: it stays as it was"); }
  catch(e){ toast(e.status===409?"It’s being tried now. Try again in a moment":"It has already settled"); }
  loadOutbox(); onChangedElsewhere();
}
function renderOutbox(){
  const f=$("#outboxFly"), held=(state.pending&&state.pending.items)||[];
  const waiting=state.draftsWaiting||[];
  const changes=(held.length?'<h4>Changes waiting</h4>'+held.map(pendingRow).join(""):"")+(waiting.length?'<h4>Drafts waiting</h4>'+waiting.map(draftWaitRow).join(""):"");
  if(!state.outbox.length){ f.innerHTML=changes||'<h4>Outbox</h4><div class="ob-row"><span class="ob-sub">Nothing queued.</span></div>'; return; }
  f.innerHTML=changes+'<h4>Outbox</h4>'+state.outbox.map(o=>{
    const to=(o.to||[]).join(", ")+(o.bcc_count?` +${o.bcc_count} bcc`:"");
    const stLabel=o.scheduled?"scheduled":o.retrying?"retrying":o.unsure?"unsure":o.state;
    const when=o.scheduled?` · ${esc(fmtWhen(o.next_attempt_at))}`:"";
    return `<div class="ob-row"><span class="ob-st ${o.scheduled?"scheduled":o.state}">${stLabel}</span>
      <span class="ob-sub">${esc(o.subject)}<br><span class="ob-to${o.error?" ob-why":""}">to ${esc(to)}${when}${o.error?" · "+esc(o.error):""}</span></span>
      ${o.scheduled||o.retrying?`<button class="ob-retry" data-sendnow="${o.id}">${o.retrying?"Try now":"Send now"}</button><button class="ob-retry" data-cancel="${o.id}">Cancel</button>`:""}
      ${o.state==="error"?`<button class="ob-retry" data-retry="${o.id}">${o.unsure?"Send again":"Retry"}</button>`:""}</div>`; }).join("");
}
async function sendNow(id){
  try{ await cpost("/api/outbox/sendnow",{id}); toast("Sending now…"); setTimeout(loadOutbox,500); }
  catch(e){ toast("Couldn’t send now"); }
}
async function cancelScheduled(id){
  try{ const r=await cpost("/api/outbox/cancel",{id}); toast(r.ok?"Send cancelled":"Too late to cancel"); setTimeout(loadOutbox,300); }
  catch(e){ toast("Cancel failed"); }
}
async function retrySend(id){
  try{ const r=await cpost("/api/outbox/retry",{id}); toast(r.gone?"It had gone out, so it wasn’t sent again":"Retrying…"); setTimeout(loadOutbox,500); }
  catch(e){ toast("Retry failed"); }
}

/* ---------- AI summarize (the wind mark) ---------- */
function aiTarget(){
  if(state.thread) return {account:state.thread.account, thread_id:state.thread.thread_id};
  if(state.csel!=null) return {pk:state.csel};
  return null;
}
let _aiInflight=false;
async function summarize(force){
  const req=aiTarget(); if(!req||_aiInflight)return;   // ignore re-entry (double i/click) -> no duplicate claude
  _aiInflight=true; showAiCard("loading");
  try{
    let d; try{ d=await cpost("/api/ai/summarize", force?{...req,force:true}:req); }
    catch(e){ showAiCard("error",{error:String(e.message||e)}); return; }
    if(!d.ok){ showAiCard(d.retry?"unavailable":"error", d); return; }
    renderAiCard(d);
  } finally { _aiInflight=false; }
}
function showAiCard(kind,d){
  const el=$("#aiCard"); if(!el)return; el.hidden=false; el.classList.remove("stored");
  if(kind==="loading") el.innerHTML=`<div class="ai-hd"><span class="ai-mark">${ico("wind")}Summary</span></div>
    <div class="rd-skel"><div class="skb"></div><div class="skb"></div><div class="skb short"></div></div>
    <div class="rd-skelhint"><span class="spin">${ico("wind")}</span> Reading the thread…</div>`;
  else if(kind==="unavailable") el.innerHTML=`<div class="ai-err">AI summary is unavailable right now (Claude not reachable). <button class="ai-rerun" data-airerun>Try again</button></div>`;
  else el.innerHTML=`<div class="ai-err">Couldn’t summarize${d&&d.error?" — "+esc(d.error):""}. <button class="ai-rerun" data-airerun>Retry</button></div>`;
}
function storedSummaryCard(it){   // the summary ingest already wrote — shown at once, no AI call; "Full summary" asks for the deeper one
  const el=$("#aiCard"); if(!el||!it)return;
  const sum=String(it.summary||"").trim(), act=String(it.action||"").trim(); if(!sum&&!act)return;
  el.hidden=false; el.classList.add("stored");
  el.innerHTML=`<div class="ai-hd"><span class="ai-mark">${ico("wind")}Summary</span></div>
    ${sum?`<div class="ai-tldr">${esc(sum)}</div>`:""}
    ${act?`<div class="ai-action">→ ${esc(act)}</div>`:""}
    <div class="ai-foot">Saved summary · <button class="ai-rerun" data-aifull>${ico("wind")} Full summary (i)</button></div>`;
}
function upNextHtml(canArchive){   // the foot of the reading pane: what comes next, and the one-step way there
  const list=state.searching?state.searchItems:state.cmsgs, i=list.findIndex(x=>x.pk===state.csel);
  if(i<0)return "";
  const nx=list[i+1], more=state.searching?state.searchHasMore:state.cHasMore;
  if(!nx)return `<div class="rd-next end">${more?"That’s the last one loaded — “Load more” at the end of the list keeps going.":"That’s the last one in this list."}</div>`;
  return `<div class="rd-next"><div class="rdn-t"><span class="rdn-k">Up next</span>
      <span class="rdn-who">${esc(nx.from_name||nx.from_addr||"")}</span><span class="rdn-sj">${esc(nx.subject||"(no subject)")}</span></div>
    ${canArchive?`<button class="cab" data-ca="archive" title="Archive this one and open the next (e)"><span>${ico("archive")}</span> Archive &amp; next</button>`:""}
    <button class="cab" data-nav="1" title="Next (j)"><span>↓</span> Next</button></div>`;
}
function renderAiCard(d){
  const el=$("#aiCard"); if(!el)return; el.hidden=false; el.classList.remove("stored");
  const pts=(d.points||[]).map(p=>`<li>${esc(p)}</li>`).join("");
  el.innerHTML=`<div class="ai-hd"><span class="ai-mark">${ico("wind")}Summary</span>${d.cached?' <span class="ai-cached">cached</span>':""}${d.stale?' <span class="ai-stale">thread grew — <button class="ai-rerun" data-airerun>re-run</button></span>':""}</div>
    <div class="ai-tldr">${esc(d.tldr||"")}</div>
    ${pts?`<ul class="ai-points">${pts}</ul>`:""}
    ${d.action?`<div class="ai-action">→ ${esc(d.action)}</div>`:""}
    <div class="ai-foot">${d.message_count||1} msg · ${esc(d.model||"")} <button class="ai-rerun" data-airerun>↻ re-run</button></div>`;
}

/* ---------- today's brief: the AI narrative and topic groups, computed once and cached server-side ---------- */
let _todayInflight=false, _briefData=null;
async function loadBrief(force,clicked){   // force: write it again from the mail as it is now (runs the AI)
  if(_todayInflight && !force)return;            // re-entry guard: never fork two claude calls
  _todayInflight=true;
  try{
    let d=force ? await cpost("/api/briefing/refresh",{}) : await api("/api/briefing");   // the GET only reads what is stored
    if(d&&d.missing&&!aiOff()){ renderBrief(); d=await cpost("/api/briefing",{},clicked?{}:{askOver:false}); }   // none yet: write it; only a click asks at the limit
    if(!d||!d.ok){ _briefErr=(d&&d.error)||"no answer"; return; }
    _briefErr=null; _briefData=d;
  }catch(e){ _briefErr=e.overLimit?BRIEF_LIMIT:e.aiOff?null:"couldn’t reach the server"; }   // AI off: the brief says so (aiOffHtml)
  finally{ _todayInflight=false; renderBrief(); }
}
/* ── Tidy: close the AI→action loop. The AI already scored every message; let it do triage work.
   Conservative + reversible: only low-importance INFO/OTHER noise (no to-do, never newsletters — those
   live in the Feed — never action mail). Preview via Review; one action files all to Archive; z undoes. */
function tidyCandidates(){ return (state.all||[]).filter(it=>it.safe_to_file); }
async function doTidy(){
  const cands=tidyCandidates(); if(!cands.length)return;
  if(!confirm(`File ${cands.length} low-priority message${cands.length>1?"s":""} to Archive?\nThey stay searchable and you can undo with z.`)) return;
  const grp=(typeof undoGroup==="function")?undoGroup():undefined;
  const pkset=new Set(cands.map(c=>c.pk));
  state.all=(state.all||[]).filter(x=>!pkset.has(x.pk));        // optimistic remove + re-render
  state.cmsgs=state.cmsgs.filter(x=>!pkset.has(x.pk)); state.searchItems=(state.searchItems||[]).filter(x=>!pkset.has(x.pk));
  if(pkset.has(state.csel))clearReader();
  renderClientList(); renderBrief();
  let ok=0;
  for(const c of cands){ try{ const r=await cpost("/api/archive",grp?{pk:c.pk,undo_group:grp}:{pk:c.pk}); if(r&&r.ok)ok++; }catch(e){} }
  toast(`Filed ${ok} to Archive`); if(ok)showUndoFloat(`Filed ${ok}`);
  refreshStats&&refreshStats(); loadFolders&&loadFolders();
}
/* ── Draft-ahead v2: the server keeps pre-written replies warm (dwell kick + background top-K tick +
   durable cache that survives restarts) and pushes "draft-ready" over SSE. The client only tracks
   pk markers — never draft bodies (a browser-held body would be an unrevalidatable stale copy);
   every open re-fetches through the revalidating GET. NEVER auto-sends. ── */
let _draftReady=new Set(), _draftingPks=new Set(), _draftDwellT=0;
function _draftEligibleClient(it){ return it && it.reply_worthy; }   // important mail only (server: _reply_worthy); a next step alone doesn't spend an AI call
function _viewingPk(){                 // pk whose draft chip is currently on screen, else null
  if(state.compose) return null;       // composing (any surface): never kick, poll, or announce
  return state.csel!=null ? state.csel : null;
}
async function draftPeek(pk){          // read-only (&peek=1): NEVER causes generation on the server
  let d; try{ d=await api(`/api/ai/draft-ahead?pk=${pk}&peek=1`); }catch(e){ return; }
  if(!d||!d.ok)return;
  if(d.status==="ready"){ _draftReady.add(pk); _draftingPks.delete(pk); }
  else if(d.status==="generating" && !_draftingPks.has(pk)){ _draftingPks.add(pk); checkDraftAhead(pk,0); }   // dedupe: one poll chain per pk
  updateReaderDraftChip(pk);
}
function armDraftAhead(pk, kickOk){
  clearTimeout(_draftDwellT);                          // cancels the previous message's dwell on j/k skims
  if(_draftReady.has(pk)){ updateReaderDraftChip(pk); return; }
  draftPeek(pk);                                       // instant: surfaces tick/pre-restart drafts on open
  if(kickOk) _draftDwellT=setTimeout(()=>{ if(_viewingPk()===pk && !_draftingPks.has(pk))checkDraftAhead(pk,0); }, 1200);   // dwell guard: don't draft on a fast skim; skip if a chain already runs
}
async function checkDraftAhead(pk, tries){
  if(_viewingPk()!==pk)return;                   // moved off the message → stop (don't leak poll chains)
  if(_draftReady.has(pk)){ updateReaderDraftChip(pk); return; }   // SSE won the race — no duplicate fetch
  let d; try{ d=await api(`/api/ai/draft-ahead?pk=${pk}`); }catch(e){ return; }
  if(!d||!d.ok)return;
  if(d.status==="ready" && d.body){ const first=!_draftReady.has(pk);   // two racing chains: only the first announces
    _draftReady.add(pk); _draftingPks.delete(pk);
    if(first && _viewingPk()===pk)announce("AI reply draft ready — press r to review"); }
  else if(d.status==="generating"){ _draftingPks.add(pk);
    const maxTries=state.sseLive?2:5, delay=state.sseLive?10000:8000;   // SSE live → poll is only a dropped-event net
    if((tries||0)<maxTries && _viewingPk()===pk) setTimeout(()=>checkDraftAhead(pk,(tries||0)+1),delay);
    else _draftingPks.delete(pk); }              // gave up / moved off → revert the chip, never strand "Drafting…"
  else _draftingPks.delete(pk);                  // "none"
  if(_viewingPk()===pk)updateReaderDraftChip(pk);      // flip the chip if still on this message
}
function onDraftReady(d){                        // SSE "draft-ready": ids only; always re-fetch, never trust payloads
  if(!d || d.pk==null || document.hidden)return; // hidden tab: no echo fetches — they'd hold the tick's vacation gate open
  _draftingPks.delete(d.pk);
  const sel=_viewingPk();
  if(sel===d.pk){ checkDraftAhead(d.pk,99); }    // one-shot revalidate → chip flips live
  else if(sel!=null && !_draftReady.has(sel) && !state.compose){ draftPeek(sel); }   // tick may have drafted a sibling pk of the open thread
  // off-screen: do NOTHING — no toast, no badge, no list repaint; discovered on open via peek
}
let _openingDraft=null;
async function openDraftAhead(pk){
  if(_openingDraft===pk || composeIsOpen())return;   // double-click / double-r during the slow body fetch → one flow only
  _openingDraft=pk;
  try{
    await replyTo(pk,false);                       // open the composer pre-threaded (origPk/recipients/subject/quote/sig)
    const cmp=state.compose; if(!cmp)return;
    let body=null;                                 // ALWAYS revalidate (server re-checks fingerprint + TTL + anchor) so a
    try{ const d=await api(`/api/ai/draft-ahead?pk=${pk}&peek=1`); if(d&&d.status==="ready"&&d.body)body=d.body; }catch(e){}  // stale draft is never injected after the thread moved on; peek → no double generation
    if(body){ const full=body+(cmp._sigBlock||""); $("#cmpBody").value=full; guardStale(); cmp.body=full; markEdited(cmp); $("#cmpBody").focus();
      toast("Draft ready — review and edit before sending"); }
    else { _draftReady.delete(pk); toast("Thread moved on — drafting a fresh reply…"); aiDraftReply(); }   // not fresh → on-demand path, and the chip explains itself
  }finally{ _openingDraft=null; }
}
function replyChipHtml(pk){            // reader-pane Reply button, three states (derived, so re-renders keep it)
  if(_draftReady.has(pk)) return `<button class="cab cab-draft" data-ca="draftready" title="AI pre-drafted a reply — opens in the composer for review; nothing sends until you do (r)"><span>${ico("wind")}</span> Draft ready</button>`;
  if(_draftingPks.has(pk)) return `<button class="cab cab-drafting" data-ca="reply" title="AI is drafting a reply in the background — click for a plain reply now"><span>${ico("wind")}</span> Drafting…</button>`;
  return `<button class="cab" data-ca="reply" title="Reply (r)"><span>${ico("reply")}</span> Reply</button>`;
}
function threadReplyChipHtml(pk){      // thread reader variant (acts via data-tca on state.thread.reply_pk)
  if(_draftReady.has(pk)) return `<button class="cab cab-draft" data-tca="draftready" title="AI pre-drafted a reply — opens in the composer for review; nothing sends until you do"><span>${ico("wind")}</span> Draft ready</button>`;
  return `<button class="cab" data-tca="reply"><span>${ico("reply")}</span> Reply</button>`;
}
function updateReaderDraftChip(pk){    // swap ONLY the button — a full reader re-render reloads the iframe (flicker/scroll loss)
  const b=$('#reader .cab[data-ca="reply"], #reader .cab[data-ca="draftready"]');
  if(b){ if(state.csel===pk)b.outerHTML=replyChipHtml(pk); return; }
  const tb=$('#reader .cab[data-tca="reply"], #reader .cab[data-tca="draftready"]');
  if(tb && state.thread && state.thread.reply_pk===pk)tb.outerHTML=threadReplyChipHtml(pk);
}
/* ── Commitments: the AI extracts a next-step `action` per message — surface them all as one
   checkable agenda (read-only over state.all; reuses the verified archive/snooze/open actions). ── */
function commitments(){ return (state.all||[]).filter(it=>it.commitment_worthy)   // meaningful next-steps only — not every low-priority nudge
  .sort((a,b)=>(b.importance||0)-(a.importance||0)); }
function openCommitments(){ renderCommitList(); $("#commitPanel").classList.add("show"); }
function closeCommitments(){ const o=$("#commitPanel"); if(o)o.classList.remove("show"); }

/* ── Weekly Review: a retrospective companion to the daily brief (AI narrative + this week's open items) ── */
let _reviewLoaded=false;
function openReview(){ $("#reviewPanel").classList.add("show"); if(!_reviewLoaded)loadReview(false); }
function closeReview(){ const o=$("#reviewPanel"); if(o)o.classList.remove("show"); }
async function loadReview(force){
  const body=$("#rvBody"); if(!body)return;
  body.innerHTML=`<div class="rv-load"><span class="spin">↻</span> Looking back over your week…</div>`; $("#rvRange").textContent="";
  let d; try{ d = force ? await cpost("/api/review/refresh",{}) : await api("/api/review");   // the GET only reads what is stored
    if(d&&d.missing) d=await cpost("/api/review",{}); }                                      // none yet: write it (you opened it)
  catch(e){ body.innerHTML=e.aiOff?`<div class="rv-err">AI is off, so there is no review: Claude writes it, and MailMind has no Anthropic API key.</div>`
      :`<div class="rv-err">${e.overLimit?"Today’s AI limit is reached, so your review isn’t written.":"Couldn’t build your review."} <button class="ai-rerun" data-rv-retry>${e.overLimit?"Write it":"Retry"}</button></div>`; return; }
  if(!d||!d.ok){ body.innerHTML=`<div class="rv-err">${esc((d&&d.error)||"Review unavailable")} <button class="ai-rerun" data-rv-retry>Retry</button></div>`; return; }
  _reviewLoaded=true; renderReview(d);
}
function renderReview(d){
  const body=$("#rvBody"); if(!body)return; const s=d.stats||{};
  $("#rvRange").textContent="· last 7 days";
  const stat=(n,l,hot)=>`<div class="rv-stat${hot?" hot":""}"><div class="n">${n}</div><div class="l">${esc(l)}</div></div>`;
  const stats=`<div class="rv-stats">${stat(s.sent||0,"sent")}${stat(s.received||0,"received")}${stat(s.needs_you||0,"need you",(s.needs_you||0)>0)}${stat(s.waiting_on||0,"awaiting reply")}${s.reply_later?stat(s.reply_later,"reply-later"):""}${s.cleaned?stat(s.cleaned,"cleaned up"):""}</div>`;
  const nextSec=(d.next||[]).length?`<div class="rv-sec next">${ico("wind")} Start next week with</div>`+d.next.map(t=>`<div class="rv-item"><span class="ic">→</span><div class="body"><div class="t">${esc(t)}</div></div></div>`).join(""):"";
  const needSec=(d.needs||[]).length?`<div class="rv-sec">Still needs you (${d.needs.length})</div>`+d.needs.map(n=>`<div class="rv-item need" data-rv-pk="${n.pk}" data-rv-acct="${esc(n.account)}" data-rv-tid="${esc(n.thread_id||"")}"><span class="ic">●</span><div class="body"><div class="t">${esc(n.subject)}</div><div class="m"><span class="who">${esc(n.who)}</span>${n.action?" · "+esc(n.action):""}</div></div></div>`).join(""):`<div class="rv-sec">Still needs you</div><div class="rv-empty">Nothing important is unanswered.</div>`;
  const waitSec=(d.waiting||[]).length?`<div class="rv-sec">You’re waiting on a reply</div>`+d.waiting.map(w=>`<div class="rv-item wait" data-rv-pk="${w.pk}" data-rv-acct="${esc(w.account)}" data-rv-tid="${esc(w.thread_id||"")}"><span class="ic">◷</span><div class="body"><div class="t">${esc(w.subject)}</div><div class="m">to ${esc(w.to||"—")}</div></div></div>`).join(""):"";
  body.innerHTML=`<div class="rv-eyebrow">${ico("wind")} Your week</div><p class="rv-narr">${esc(d.narrative||"")}</p>${stats}${nextSec}${needSec}${waitSec}<div class="rv-foot">${d.model==="fallback"?"summary from your data":esc(d.model||"claude")}${d.generated?" · "+esc(d.generated.slice(0,10)):""}${d.cached?" · cached":""}${d.stale?' · <span class="ai-stale">mail changed — <button class="ai-rerun" data-rv-retry>refresh</button></span>':""}</div>`;
}
function reviewOpen(pk, acct, tid){
  closeReview();
  if(acct && state.account!==acct){ state.account=acct; state.allInbox=false; renderMonos(); }
  if(tid){ openThread(tid, pk, true, acct); } else { openClientMsg(pk, true); }
}

/* ── Document Hub: one findable place for every attachment across accounts ── */
let _docs=null, _docKind="all", _docQ="", _docMatched=0, _docRead=0, _docAsked=0, _docTyping=null;
const DOC_PAGE=200;
const _DOC_ICON={pdf:"file",image:"image",doc:"file",sheet:"table",slide:"slides",archive:"archive",text:"file",other:"clip"};
const _DOC_LABEL={pdf:"PDFs",image:"Images",doc:"Docs",sheet:"Sheets",slide:"Slides",archive:"Archives",text:"Text",other:"Other"};
function openDocs(){ $("#docsPanel").classList.add("show"); if(_docs===null)loadDocs(); else renderDocs();
  setTimeout(()=>{ const s=$("#docSearch"); if(s)s.focus(); },40); }
function closeDocs(){ const o=$("#docsPanel"); if(o)o.classList.remove("show"); }
/* The server filters and pages, so a search reaches every file and not only those loaded. */
async function loadDocs(more){
  const list=$("#docList"), asked=++_docAsked;
  if(_docs===null&&list)list.innerHTML=`<div class="doc-empty"><span class="spin">↻</span> Gathering your files…</div>`;
  const p=new URLSearchParams({limit:DOC_PAGE, offset:more?_docRead:0});
  if(_docKind!=="all")p.set("kind",_docKind);
  if(_docQ.trim())p.set("q",_docQ.trim());
  let d; try{ d=await api(`/api/documents?${p}`); }catch(e){
    if(asked!==_docAsked)return;
    if(more){ toast("Couldn’t load more files."); renderDocs(); return; }
    if(list)list.innerHTML=`<div class="doc-empty">Couldn’t load documents.</div>`; return; }
  if(asked!==_docAsked)return;                                          // a newer search or filter took over
  const got=(d&&d.docs)||[], first=more?_docs.length:0;
  if(more){ const id=x=>`${x.pk}/${x.part_id}/${x.filename}`, have=new Set(_docs.map(id));   // new mail moves the rest along
    _docs=_docs.concat(got.filter(x=>!have.has(id(x)))); }
  else _docs=got;
  _docRead=(more?_docRead:0)+got.length;                                // how far through the server's list, not how many rows are held
  _docMatched=(d&&d.matched)||0; _docFacets=(d&&d.facets)||{}; _docTotal=(d&&d.total)||0; renderDocs();
  if(more){ const next=list&&list.querySelectorAll(".doc-row")[first]; if(next)next.querySelector("[data-doc-dl]").focus({preventScroll:true}); }
}
let _docFacets={}, _docTotal=0;
function renderDocs(){
  const list=$("#docList"), filt=$("#docFilters"); if(!list)return;
  const shown=_docs, left=_docMatched-_docRead;
  $("#docCount").textContent=`· ${shown.length}${left>0?" of "+_docMatched:""}`;
  // filter chips (All + each kind that exists), from the full-corpus facet counts
  const kinds=Object.keys(_DOC_LABEL).filter(k=>_docFacets[k]);
  filt.innerHTML=[`<button class="doc-fchip${_docKind==="all"?" on":""}" data-dk="all">All <span class="c">${_docTotal}</span></button>`]
    .concat(kinds.map(k=>`<button class="doc-fchip${_docKind===k?" on":""}" data-dk="${k}">${esc(_DOC_LABEL[k])} <span class="c">${_docFacets[k]}</span></button>`)).join("");
  if(!shown.length){ list.innerHTML=`<div class="doc-empty">${_docTotal?"No files match.":"No documents found in your synced mail."}</div>`; return; }
  list.innerHTML=shown.map(x=>`<div class="doc-row" data-doc-pk="${x.pk}" data-doc-acct="${esc(x.account)}">
    <div class="doc-ic">${ico(_DOC_ICON[x.kind]||"clip")}</div>
    <div class="doc-body"><div class="doc-fn">${esc(x.filename)}</div>
      <div class="doc-meta"><span class="who">${esc(x.from)}</span> · ${esc(x.subject)} · ${esc(x.when||"")}</div></div>
    <span class="doc-sz">${fmtSize(x.size)}</span>
    <a class="doc-dl" href="${esc(x.url)}" download="${esc(x.filename)}" title="Download" data-doc-dl>${ico("download")}</a></div>`).join("")
    +(left>0?`<button class="doc-more" data-doc-more>Show more · ${left} left</button>`:"");
}
function docOpen(pk, acct){
  closeDocs();
  const acctChanged=acct && state.account!==acct;
  if(acct){ state.account=acct; state.allInbox=false; }      // set the doc's account FIRST so the loads below target it
  renderMonos();
  if(acctChanged)loadFolders();   // switched account → refresh rail + list
  openClientMsg(pk, true);   // reader shows the doc's email; csel is set synchronously, so the async list-load won't clobber it (guarded in loadCMessages)
}
const COMMIT_CAP=60;
function renderCommitList(){
  const all=commitments(); const cs=all.slice(0,COMMIT_CAP);   // glanceable top slice (prioritized); the rest load as you clear
  const nEl=$("#commitN"); if(nEl)nEl.textContent=all.length>COMMIT_CAP?`${all.length} · top ${COMMIT_CAP}`:all.length;
  const el=$("#commitList"); if(!el)return;
  if(!cs.length){ el.innerHTML='<div class="commit-empty">No open commitments — you’re on top of it.</div>'; return; }
  el.innerHTML=cs.map(it=>{ const lvl=Math.max(1,Math.min(5,it.importance||1));
    return `<div class="commit-row" data-pk="${it.pk}">
      <button class="cm-check" data-cm="done" title="Done — archive" aria-label="Done">✓</button>
      <div class="cm-main" data-cm="open"><div class="cm-action">${esc(it.action)}</div>
        <div class="cm-meta"><span class="who">${esc(it.from_name||it.from_addr||"—")}</span> · ${esc((it.subject||"(no subject)").slice(0,64))} · p${lvl}</div></div>
      <div class="cm-side"><button data-cm="later" title="Later — snooze to tomorrow" aria-label="Later — snooze to tomorrow">${ico("clock")}</button>${(typeof it.link==="string"&&/^https:\/\//.test(it.link))?`<button data-cm="openmsg" title="Open original" aria-label="Open original">${ico("external")}</button>`:""}</div>
    </div>`; }).join("");
}
function _commitAfter(row){ row.remove();   // optimistic; the archive/snooze updates state.all async
  if(!$$("#commitList .commit-row").length) setTimeout(renderCommitList,350); }   // batch cleared → pull the next slice once settled

/* ---------- snooze ---------- */
const SNOOZE_PRESETS=[["later_today","Later today"],["tomorrow","Tomorrow 8am"],["weekend","This weekend"],["next_week","Next week"]];
let _snoozeOverride=null;     // set for bulk snooze ({pks:[...]})
function snoozeTarget(){
  if(_snoozeOverride) return _snoozeOverride;
  if(state.thread) return {thread_id:state.thread.thread_id, account:state.thread.account};
  if(state.csel!=null) return {pk:state.csel};
  return null;
}
function openSnoozeMenu(anchor, override){
  closeSnoozeMenu();                   // first: closing clears the override, so it must run before the override is set
  _snoozeOverride=override||null;
  const t=snoozeTarget(); if(!t){ _snoozeOverride=null; return; }
  const m=document.createElement("div"); m.className="snooze-menu"; m.id="snoozeMenu"; m.setAttribute("role","menu");
  const inSnoozed=state.folder==="__snoozed__" && !override;
  m.innerHTML=SNOOZE_PRESETS.map(([k,l],i)=>`<button role="menuitem" data-snz="${k}"><span class="snz-n">${i+1}</span> ${l}</button>`).join("")
    +`<label class="snz-custom">Custom… <input type="datetime-local" id="snzWhen"></label>`
    +(inSnoozed?`<button role="menuitem" data-snz="un">Unsnooze</button>`:"");
  document.body.appendChild(m);
  const r=visibleAnchor(anchor).getBoundingClientRect();
  m.style.top=(r.bottom+4)+"px"; m.style.left=Math.min(r.left,window.innerWidth-210)+"px";
  const f=m.querySelector("button"); if(f)f.focus();   // move focus in for keyboard/AT
  $("#snzWhen").addEventListener("change",e=>{ if(e.target.value)doSnoozeAt(e.target.value); });
  // a click inside acts here and stops; the next click outside closes the menu
  m.addEventListener("click",e=>{ e.stopPropagation(); const s=e.target.closest("[data-snz]"); if(s){ if(s.dataset.snz==="un")doUnsnooze(); else doSnooze(s.dataset.snz); } });
  setTimeout(()=>{ if(m.isConnected)document.addEventListener("click",_snoozeOutside); },0);   // after the opening click finishes
  window.addEventListener("blur",_snoozeBlur);   // a click in the mail body (see frameTookFocus)
}
function _snoozeOutside(e){ if(!e.target.closest("#snoozeMenu"))closeSnoozeMenu(); }
function _snoozeBlur(){ setTimeout(()=>{ if(frameTookFocus())closeSnoozeMenu(); },0); }
function closeSnoozeMenu(){ const m=$("#snoozeMenu"); if(m)m.remove(); _snoozeOverride=null; document.removeEventListener("click",_snoozeOutside); window.removeEventListener("blur",_snoozeBlur); }
function _snoozeAffected(t){ return t.pks ? t.pks.slice() : (t.pk!=null?[t.pk]:[]); }
async function doSnooze(preset){ return _snooze({preset}); }
async function doSnoozeAt(v){ return _snooze({until:v}); }
async function _snooze(extra){
  const t=snoozeTarget(); if(!t)return; const affected=_snoozeAffected(t); closeSnoozeMenu();
  try{ const r=await cpost("/api/snooze", {...t, ...extra}); if(!r.ok)throw new Error(r.error||"err");
    affected.forEach(pk=>{ if(cfind(pk)||(state.searchItems||[]).some(x=>x.pk===pk))cRemove(pk); else if(state.all.some(x=>x.pk===pk)) afterRemove(pk); });   // client/search rows heal; brief/summary advance
    if(!affected.length && t.thread_id){ /* thread snooze: refresh the folder */ loadCMessages(); }
    state.cmarked.clear(); renderBulk(); toast("Snoozed"); showUndoFloat("Snoozed"); loadFolders(); }
  catch(e){ toast("Couldn’t snooze: "+(e.message||"")); }
}
async function doUnsnooze(pk){
  const t = pk!=null?{pk}:snoozeTarget(); if(!t)return; closeSnoozeMenu();
  try{ await cpost("/api/unsnooze", t); const p=t.pk||pk; if(p!=null && (cfind(p)||(state.searchItems||[]).some(x=>x.pk===p))) cRemove(p); toast("Unsnoozed"); loadFolders(); }
  catch(e){ toast("Couldn’t unsnooze"); }
}
/* ---------- mute thread ---------- */
function muteTarget(extra){
  if(extra) return extra;
  if(state.thread) return {account:state.thread.account, thread_id:state.thread.thread_id};
  if(state.csel!=null){ const it=cfindAny(state.csel); return {pk:state.csel, thread_id:(it&&it.thread_id)||null}; }
  return null;
}
function _dropThread(tid){           // optimistic: remove every row of a thread from the list
  if(!tid)return;
  const nodes=[...document.querySelectorAll("#rows .row.cmsg")].filter(n=>n.dataset.tid===tid);
  state.cmsgs=state.cmsgs.filter(x=>x.thread_id!==tid);
  state.searchItems=(state.searchItems||[]).filter(x=>x.thread_id!==tid);   // muted thread leaves the results too
  Promise.all(nodes.map(healNode)).then(()=>renderClientList());
  if(state.csel!=null && !cfind(state.csel) && !(state.searchItems||[]).some(x=>x.pk===state.csel)) clearReader();
}
async function doMute(t){
  if(!t)return;
  try{ const r=await cpost("/api/mute", {...t}); if(!r.ok)throw new Error(r.error||"err");
    _dropThread(r.thread_id || t.thread_id);
    state.cmarked.clear(); renderBulk(); toast("Muted"); showUndoFloat("Muted"); loadFolders(); }
  catch(e){ toast("Couldn’t mute: "+(e.message||"")); }
}
async function doUnmute(t){
  if(!t)return;
  try{ const r=await cpost("/api/unmute", {...t}); if(!r.ok)throw new Error(r.error||"err");
    if(state.folder==="__muted__") _dropThread(r.thread_id || t.thread_id);
    toast("Unmuted"); loadFolders(); }
  catch(e){ toast("Couldn’t unmute"); }
}
async function doDismissFollowup(t){
  if(!t)return;
  try{ const r=await cpost("/api/followups/dismiss", {...t}); if(!r.ok)throw new Error(r.error||"err");
    _dropThread(r.thread_id || t.thread_id); toast("Dismissed"); loadFolders(); }
  catch(e){ toast("Couldn’t dismiss"); }
}
async function doReplyLater(t){
  if(!t)return;
  const cur=state.csel;
  try{ const r=await cpost("/api/replylater", {...t, on:true}); if(!r.ok)throw new Error(r.error||"err");
    toast("Saved to Reply later"); loadFolders(true);
    // deferred → keep triaging: advance the reader to the next message (the row STAYS — reply-later
    // pins the thread in a visible stack, it does not remove it from the inbox like snooze does).
    if(cur!=null && !state.thread){
      const list=state.searching?state.searchItems:state.cmsgs, i=list.findIndex(x=>x.pk===cur), nx=list[i+1]||list[i-1];
      if(nx&&nx.pk!==cur)openConversation(nx,true);
    }
  }
  catch(e){ toast("Couldn’t save: "+(e.message||"")); }
}
async function doReplyLaterDone(t){     // "Done" inside the Reply-later view: un-pin + drop the row
  if(!t)return;
  try{ const r=await cpost("/api/replylater", {...t, on:false}); if(!r.ok)throw new Error(r.error||"err");
    _dropThread(r.thread_id || t.thread_id); toast("Done"); loadFolders(true); }
  catch(e){ toast("Couldn’t update"); }
}
/* ---------- the Screener (first-contact gate) ---------- */
async function loadScreener(){
  const acct=state.account; if(!acct)return;
  let d; try{ d=await api(`/api/screener?account=${encodeURIComponent(acct)}`); }catch(e){ d=null; }
  if(acct!==state.account)return;               // superseded by an account switch (no stale overwrite)
  state.screener=d?(d.items||[]):[]; state.screenerCount=d?(d.total||0):0;
  renderFolders();
  if(state.folder==="__screener__")renderScreener();
}
function renderScreener(){
  state.csel=null; clearReader&&clearReader();
  $("#clientCtx").innerHTML=`<b>Screener</b> · ${state.screener.length} new sender${state.screener.length===1?"":"s"} awaiting a decision`;
  $("#count").textContent=`${state.screener.length} to screen`;
  renderBulk();
  const rows=$("#rows");
  if(!state.screener.length){ rows.innerHTML='<div class="nores allclear"><div class="ac-h">Screener clear</div><div class="ac-b">Every sender in this inbox has been allowed or screened out.</div></div>'; return; }
  rows.innerHTML=state.screener.map(s=>`<div class="scr-row" data-scr="${esc(s.from_addr)}">
    <span class="mono-dot" style="color:hsl(${hue(s.from_addr)} var(--mono-s) var(--mono-l))">${esc((s.from_name||s.from_addr||"?")[0].toUpperCase())}</span>
    <div class="scr-who"><div class="scr-nm">${esc(s.from_name)}</div><div class="scr-ad">${esc(s.from_addr)} · ${s.count} msg · ${esc(s.when)}</div></div>
    <div class="scr-acts"><button class="scr-allow" data-scr-allow="${esc(s.from_addr)}">${ico("check")} Allow</button><button class="scr-block" data-scr-block="${esc(s.from_addr)}">${ico("ban")} Screen out</button></div>
  </div>`).join("");
}
async function screenerDecide(addr, verdict){
  const node=[...document.querySelectorAll("#rows .scr-row")].find(n=>n.dataset.scr===addr);
  state.screener=state.screener.filter(s=>s.from_addr!==addr); state.screenerCount=state.screener.length;
  renderFolders(); healNode(node).then(()=>renderScreener());   // optimistic + heal
  try{ const d=await cpost("/api/rules",{token:addr,verdict}); if(!d.ok)throw new Error(d.msg||"err");
    onReclassified(d);
    toast(verdict==="keep"?("Allowed "+short(addr)):("Screened out "+short(addr)+(d.trashed?" · trashed":""))); }
  catch(e){ toast("Couldn’t apply"); loadScreener(); }
}
/* ---------- contact card (sender history) ---------- */
function closeContactCard(){ const m=$("#contactCard"); if(m)m.remove(); }
async function openContactCard(addr, anchor){
  if(!addr)return; closeContactCard();
  const m=document.createElement("div"); m.className="contact-card"; m.id="contactCard";
  m.innerHTML='<div class="cc-load">Loading…</div>'; document.body.appendChild(m);
  const r=anchor.getBoundingClientRect();
  m.style.left=Math.min(r.left, window.innerWidth-m.offsetWidth-12)+"px";
  m.style.top=Math.min(r.bottom+6, window.innerHeight-340)+"px";
  m.addEventListener("click",e=>{ e.stopPropagation(); _contactCardClick(e); });
  setTimeout(()=>document.addEventListener("click",closeContactCard,{once:true}),0);
  let d; try{ d=await api("/api/contact?addr="+encodeURIComponent(addr)); }catch(e){ closeContactCard(); return; }
  if(!d||!d.ok){ closeContactCard(); return; }
  const recent=(d.recent||[]).map(x=>`<button class="cc-thread" data-cc-open="${x.pk}" data-cc-acct="${esc(x.account)}" data-cc-tid="${esc(x.thread_id||"")}"><span>${esc(x.subject)}</span><span class="cc-when">${esc(x.when)}</span></button>`).join("");
  const stats=[`${d.count} email${d.count===1?"":"s"}`, `replied ${d.replied}×`,
               d.first_ago?`first ${d.first_ago} ago`:"", d.last_ago?`last ${d.last_ago} ago`:""].filter(Boolean).join(" · ");
  m.innerHTML=`<div class="cc-hd"><span class="cc-mono" style="color:hsl(${hue(d.addr)} var(--mono-s) var(--mono-l))">${esc((d.name||d.addr||"?")[0].toUpperCase())}</span>
      <div><div class="cc-name">${esc(d.name||d.addr)}</div><div class="cc-addr">${esc(d.addr)}</div></div></div>
    <div class="cc-stats">${esc(stats)}</div>
    ${(d.accounts||[]).length>1?`<div class="cc-accts">writes to ${d.accounts.map(a=>esc(nameOf(a))).join(", ")}</div>`:""}
    ${recent?`<div class="cc-recent">${recent}</div>`:""}
    <div class="cc-actions"><button data-cc-compose="${esc(d.addr)}">${ico("pen")} Compose</button><button data-cc-search="${esc(d.addr)}">${ico("search")} From this sender</button></div>`;
  m.style.top=Math.max(8, Math.min(parseFloat(m.style.top), window.innerHeight-m.offsetHeight-12))+"px";   // re-clamp with the real rendered height (the 340px guess undershoots the new card)
}
function _contactCardClick(e){
  const o=e.target.closest("[data-cc-open]");
  if(o){ const pk=+o.dataset.ccOpen, acct=o.dataset.ccAcct, tid=o.dataset.ccTid; closeContactCard();
    if(state.account!==acct){ state.account=acct; renderMonos(); }
    if(tid){ openThread(tid, pk, true, acct); } else { openClientMsg(pk, true); } return; }
  const cmp=e.target.closest("[data-cc-compose]");
  if(cmp){ const a=cmp.dataset.ccCompose; closeContactCard(); composeOpen({mode:"new", to:[a]}); return; }
  const srch=e.target.closest("[data-cc-search]");
  if(srch){ const a=srch.dataset.ccSearch; closeContactCard();
     state.viewId=null; state.q="from:"+a; $("#search").value=state.q; runSearch(); return; }
}

/* ---------- reopening a saved draft (POST /api/draft/resume) ----------
   The block between the markers touches no DOM and no state; test_draft_resume_ui.py runs it in node. */
/* ---------- drafts ---------- */
function adoptResume(cmp,r){   // a reply's or forward's first save: from now on it names its quote, threading and files itself, so the original's row can go
  if(!r.resume||cmp.resume)return;
  cmp.resume=r.resume;
  if(!cmp.fwdAttach)return;
  cmp.attachments=cmp.attachments.concat(r.files||[]); cmp.fwdAttach=0;   // the server no longer adds them: they are this composer's files now
  if(state.compose===cmp){ renderAttach(); renderQuote(); }
}
function namesItself(payload,cmp){   // a payload built before the save it waited for: that save may have switched the composer over
  if(!cmp.resume||payload.resume)return;
  Object.assign(payload,resumeFields(cmp.resume));
  payload.attachments=cmp.attachments.map(a=>({filename:a.filename,content_type:a.content_type,b64:a.b64}));
}
function composePayload(){
  const cmp=state.compose; if(!cmp)return null;
  ["to","cc","bcc"].forEach(f=>{ const inp=fieldEl(f).querySelector(".ci"); if(inp&&inp.value.trim()){ commitChip(f,inp.value); inp.value=""; } });
  cmp.subject=$("#cmpSubject").value; cmp.body=$("#cmpBody").value; cmp.from=$("#cmpFrom").value;
  return { account:cmp.from, mode:cmp.mode, idempotency_key:cmp.idk, draft_id:cmp.draftId||null,
    to:cmp.to.map(x=>x.v), cc:cmp.cc.map(x=>x.v), bcc:cmp.bcc.map(x=>x.v),
    subject:cmp.subject, text:cmp.body, in_reply_to_pk:cmp.origPk,
    attachments:cmp.attachments.map(a=>({filename:a.filename,content_type:a.content_type,b64:a.b64})),
    ...resumeFields(cmp.resume) };
}
async function composeSaveDraft(silent,autosave){   // → true once saved, false if it wasn't, null when there's nothing to save
  const cmp=state.compose; if(!cmp)return null; const payload=composePayload();
  // never litter Drafts with an empty / signature-only message (shared by manual save, autosave, close)
  const bodyTrim=(payload.text||"").replace(cmp._sigBlock||"","").trim();
  if(!payload.to.length&&!payload.cc.length&&!payload.bcc.length&&!(payload.subject||"").trim()&&!bodyTrim){
    if(!silent)toast("Nothing to save yet"); return null;
  }
  const mark=t=>{ if(state.compose===cmp)$("#cmpSaved").textContent=t; };   // a save that outlives its composer leaves the next one's footer alone
  const at=cmp.edits||0, prior=cmp._saving;
  const mine=(async()=>{
    if(prior)await prior;   // one composer's saves go in turn, so each names the copy the last one left
    payload.draft_id=cmp.draftId||null; namesItself(payload,cmp);
    if(autosave)payload.autosave=true;
    if(cmp.version!=null&&(silent||!cmp._conflict))payload.base_version=cmp.version;   // a save someone asked for overwrites
    try{ const r=await cpost("/api/draft", payload); if(!r.ok)throw new Error(r.error||"err");
      cmp.draftId=r.draft_id; cmp.version=r.version??cmp.version; cmp._conflict=false; adoptResume(cmp,r); if((cmp.edits||0)===at)cmp.dirty=false;   // an edit since this save began stays to save
      cmp._lastSaveHash=_draftHash(payload);
      mark("Saved "+new Date().toTimeString().slice(0,5)); if(!silent)toast("Draft saved");
      if(!autosave&&r.pushed===false)nudgeOutbox();   // it waits for its server: the tray says so
      return true; }
    catch(e){
      if(e.status===409){ cmp._conflict=true; mark("Changed in another window"); if(!silent)toast("This draft changed in another window. Save again to keep this text."); return false; }
      mark("Not saved"); if(!silent)toast("Couldn’t save draft: "+(e.message||""));   // never leave an older "Saved" up
      return false; }
  })();
  cmp._saving=mine; mine.finally(()=>{ if(cmp._saving===mine)cmp._saving=null; });
  return mine;
}
function _cheapHash(s){ let h=0; for(let i=0;i<s.length;i++)h=(h*31+s.charCodeAt(i))|0; return h; }
function _draftHash(p){ return _cheapHash(JSON.stringify([p.to,p.cc,p.bcc,p.subject,p.text,p.account,(p.attachments||[]).map(a=>a.filename)])); }
function autosaveTick(){
  const cmp=state.compose; if(!cmp||cmp.status==="sending"||!cmp.dirty||cmp._saving||cmp._conflict)return;   // the next tick saves what this one would
  const p=composePayload(); if(!p)return;
  // skip a compose that is effectively just the inserted signature (no recipients/subject)
  const bodyTrim=(p.text||"").replace(cmp._sigBlock||"","").trim();
  if(!p.to.length&&!p.cc.length&&!p.bcc.length&&!(p.subject||"").trim()&&!bodyTrim)return;
  const key=_draftHash(p); if(key===cmp._lastSaveHash)return;
  composeSaveDraft(true,true);
}
let _resumeTok=0;
async function editDraft(pk,draftId){   // by its row, or by its id for one the list can't show yet. Your text in the box, the quote folded below, files, Bcc and thread as saved
  const busy="Close the message you’re writing first";
  if(state.compose){ toast(busy); return; }
  const tok=++_resumeTok; let d;
  if(_closeSave){ await _closeSave; if(tok!==_resumeTok)return; }   // the copy it saves replaces the one this row shows
  try{ d=await cpost("/api/draft/resume",pk==null?{draft_id:draftId}:{pk}); if(!d.ok)throw new Error(d.error||"Couldn’t open this draft."); }
  catch(e){ if(tok!==_resumeTok)return; const m=e.message||"Couldn’t open this draft."; toast(m); announce(m);
    if((e.status===404||e.status===409)&&state.csel===pk)clearReader();   // that copy is gone: don't keep showing it
    if(state.allInbox)loadAllInboxes(); else if(state.searching)runSearch(); else loadCMessages();   // a stale row goes
    return; }
  if(tok!==_resumeTok)return;
  if(state.compose){ toast(busy); return; }   // one opened while this loaded is never replaced
  composeOpen(resumeOpts(d)); const cmp=state.compose; if(!cmp)return;
  cmp.attachments=(d.files||[]).slice(); renderAttach();
  const ta=$("#cmpBody"); ta.value=d.text||""; ta.setSelectionRange(0,0); cmp.dirty=false;   // nothing saves until an edit
  adoptSig(ta.value, d.account);   // the signature it was saved with, even if that From is gone
  const n=resumeNotice(d.notes);   // in the composer, not a toast under its backdrop: it stays until it closes
  if(n){ const el=$("#cmpNote"); el.textContent=n; el.hidden=false; announce(n); }
}

/* ---------- realtime (SSE) ---------- */
function onHello(d){   // the server moved on from what this tab loaded: offer a reload, never force one
  $("#buildFloat").hidden=d.build===$('meta[name="mailmind-build"]').content;
}
$("#buildReload").addEventListener("click",()=>location.reload());
function connectEvents(){
  let es; try{ es=new EventSource("/api/events"); }catch(e){ return; }
  es.onopen=()=>{ state.sseLive=true;
    const v=_viewingPk(); if(v!=null && !_draftReady.has(v))draftPeek(v); };   // resurface drafts completed during an SSE outage
  es.onerror=()=>{ state.sseLive=false; };   // EventSource auto-reconnects
  es.addEventListener("new-mail", e=>onNewMail(JSON.parse(e.data)));
  es.addEventListener("sync-done", onSyncDone);
  es.addEventListener("snooze-wake", e=>onSnoozeWake(JSON.parse(e.data)));
  es.addEventListener("mute", e=>onMute(JSON.parse(e.data)));
  es.addEventListener("reconcile", e=>onReconcile(JSON.parse(e.data)));
  es.addEventListener("flags-changed", e=>onReconcile(JSON.parse(e.data)));   // read or starred elsewhere: same refresh
  es.addEventListener("folder-synced", e=>onFolderSynced(JSON.parse(e.data)));   // a folder's first scan ended   // ghosts pruned server-side → refresh the open list
  es.addEventListener("followup", ()=>{ loadFolders(true);   // badge only — never disrupt the reader
    if(state.folder==="__waiting__"&&!state.compose&&!state.searching&&!state.asking&&state.cmarked.size===0)loadCMessages(); });
  es.addEventListener("outbox", nudgeOutbox);
  es.addEventListener("draft-ready", e=>onDraftReady(JSON.parse(e.data)));   // ids only; client re-fetches via the revalidating GET
  es.addEventListener("undo-done", onChangedElsewhere);         // an undo, here or in another tab
  es.addEventListener("commands-changed", ()=>{ onChangedElsewhere(); nudgeOutbox(); });  // a change queued offline reached the server, or won't
  es.addEventListener("resync", onResync);   // the stream lost our place (a restart, or away too long)
  es.addEventListener("hello", e=>onHello(JSON.parse(e.data)));   // the build the server has now
}
let _newPending=0, _digestDeb=null, _bgNew=0;
function bumpTitle(){ document.title = _bgNew>0 ? `(${_bgNew}) MailMind` : "MailMind"; }
function notify(d){
  if(window.Notification && Notification.permission==="granted"){
    try{ const n=new Notification("New mail · "+nameOf(d.account),
      {body:(d.count||1)+" new message"+((d.count||1)>1?"s":""), tag:"mailmind-"+d.account, silent:false});
      n.onclick=()=>{ window.focus(); n.close(); }; }catch(e){}
  }
}
function onNewMail(d){
  clearTimeout(_digestDeb); _digestDeb=setTimeout(()=>{ if(!document.hidden)loadDigest(true); }, 400);
  if(document.hidden){ _bgNew+=(d.count||1); bumpTitle(); notify(d); }   // backgrounded → badge + desktop notify
  const idle = !state.compose && !state.searching && !state.asking && state.cmarked.size===0 && !document.hidden;
  const sameView = state.account===d.account && !state.allInbox
    && state.folder.toUpperCase()===(d.folder||"INBOX").toUpperCase();
  if(state.allInbox && idle){ loadAllInboxes(); }
  else if(sameView && idle){ loadCMessages(); }        // safe to refresh; nothing in-flight
  else { if(!_pendingSince) _pendingSince=_deltaSince(); _newPending+=(d.count||1); showNewPill(); }
  if(!document.hidden){ const n=d.count||1; announce(`${n} new message${n===1?"":"s"} arrived${d.account?" in "+nameOf(d.account):""}`); }   // realtime → screen readers too
}
function onReconcile(d){
  // what the server holds changed for d.account, in any folder it has synced, or just d.folder: rows
  // pruned (mail gone from it), or flags changed elsewhere. Refresh badges, and if an open list for that account
  // may be showing them, re-pull it — but only when idle, so nothing in-flight (compose/search/selection) is disrupted.
  loadFolders && loadFolders(true);
  const idle = !state.compose && !state.searching && !state.asking && state.cmarked.size===0 && !document.hidden;
  if(!idle) return;
  if(d.folder&&state.allInbox){ if(d.folder==="INBOX")loadAllInboxes(); }
  else if(d.folder){ if(state.account===d.account&&state.folder===d.folder)loadCMessages(); }
  else if(state.allInbox){ loadAllInboxes(); }
  else if(state.account===d.account){ loadCMessages(); }
}
document.addEventListener("visibilitychange",()=>{ if(!document.hidden){ _bgNew=0; bumpTitle(); } });   // refocus alone ≠ "looked at new mail" (the inbox isn't reloaded) → don't advance the watermark here
function onFolderSynced(d){
  // a sync of d.folder that its opening started ended: its first scan, or a catch-up of a folder
  // read before. A list that answered while its first scan was still reading loads what it read;
  // one it failed for says so (opening it again tries again, so this never retries by itself). A
  // list that answered during a catch-up loads again only if the catch-up brought mail.
  if(d.new)loadFolders(true);
  if(state.allInbox||state.account!==d.account||state.folder!==d.folder)return;
  if(state.cSyncing){ if(!d.ok){ state.cSyncing=false; state.cSyncFailed=true; renderClientList(); return; } }
  else if(!(state.cCatching&&d.ok&&d.new))return;
  if(!state.searching&&!state.asking&&state.cmarked.size===0)loadCMessages();
}
function onSnoozeWake(d){
  if(d.accounts && d.accounts.includes(state.account)
     && !state.compose && !state.searching && !state.asking && state.cmarked.size===0){ loadCMessages(); }
  else toast((d.count||1)+" snoozed message"+((d.count||1)>1?"s":"")+" back");
}
function onChangedElsewhere(){   // mail moved without this tab doing it: badges now, the list only when idle
  loadFolders(true);
  if(state.compose||state.searching||state.asking||state.cmarked.size>0||document.hidden)return;
  if(state.allInbox)loadAllInboxes(); else loadCMessages();
}
function onResync(){   // events were missed and can't be replayed: fetch afresh all they would have changed
  loadDigest(true); loadOutbox(); onChangedElsewhere();
}
function onMute(d){   // a mute from another tab/refresh: drop the rows live (mirror snooze-wake)
  if(!state.compose && !state.searching && !state.asking && state.cmarked.size===0) loadCMessages();
  loadFolders(true);  // badge refresh only — don't reload the list under the user
}
/* ── "since you last looked": a per-account watermark in localStorage. We advance it only at real
   "I'm looking now" moments (inbox render / pill click / tab refocus), NEVER on background ticks. ── */
function getLastLook(acct){ try{ return (JSON.parse(localStorage.getItem("mm_lastlook")||"{}"))[acct]||0; }catch(e){ return 0; } }
function setLastLook(acct,ms){ let m={}; try{ m=JSON.parse(localStorage.getItem("mm_lastlook")||"{}"); }catch(e){} m[acct]=ms; try{ localStorage.setItem("mm_lastlook",JSON.stringify(m)); }catch(e){} }
function _allAccts(){ return state.sendAccounts||state.accountsList||[]; }   // same set loadAllInboxes fetches
function touchLastLook(){ const now=Date.now(); if(state.allInbox){ _allAccts().forEach(a=>setLastLook(a,now)); } else if(state.account){ setLastLook(state.account,now); } }
function _deltaSince(){   // the watermark to summarize FROM (frozen per pill lifecycle); first-run → last 6h
  const fb=Date.now()-6*3600*1000;
  if(state.allInbox){ const ws=_allAccts().map(a=>getLastLook(a)).filter(x=>x>0); return ws.length?Math.min(...ws):fb; }
  return getLastLook(state.account)||fb;
}
// the inbox is now showing current mail → the "N new" pill is stale; tear it down (called wherever the inbox renders)
function clearNewPill(){ _newPending=0; _pendingSince=0; _deltaDirty=false; clearTimeout(_deltaDeb); const p=$("#newPill"); if(p)p.remove(); }
let _deltaDeb=0, _deltaInflight=false, _deltaDirty=false, _pendingSince=0;
function showNewPill(){
  let p=$("#newPill");
  if(!p){ p=document.createElement("button"); p.id="newPill"; p.className="new-pill";
    p.addEventListener("click",()=>{ touchLastLook(); clearNewPill(); loadCMessages(); });
    $("#list").prepend(p); }
  const head=`▾ ${_newPending} new — click to refresh`;
  if(p.dataset.ai){ const h=p.querySelector(".np-head"); if(h)h.textContent=head; }   // AI line present → just update the count
  else p.textContent=head;
  scheduleDelta();
}
function scheduleDelta(){ clearTimeout(_deltaDeb); _deltaDeb=setTimeout(buildDelta,1500); }   // calmer than the 400ms refresh; coalesces a burst into one call
async function buildDelta(){
  if(_deltaInflight){ _deltaDirty=true; return; }   // a burst landed mid-fetch → re-run once when this finishes
  const p=$("#newPill"); if(!p||!_pendingSince||aiOff())return;   // AI off: the plain count pill is all there is
  _deltaInflight=true;
  try{
    const since=Math.floor(_pendingSince/1000), acct=state.allInbox?"":(state.account||"");
    let d=await api(`/api/briefing/since?since=${since}${acct?"&account="+encodeURIComponent(acct):""}`);   // the GET only reads
    if(d&&d.missing) d=await cpost("/api/briefing/since",{since,account:acct},{askOver:false});   // write it; never ask at the limit
    if(!d||!d.ok||d.empty||!d.headline)return;   // 0 new / error / timeout → leave the plain count pill (the floor)
    const p2=$("#newPill"); if(!p2)return;        // pill may have been consumed mid-fetch
    p2.dataset.ai="1"; p2.classList.add("new-pill--ai");
    p2.innerHTML=`<span class="np-head">▾ ${_newPending} new — click to refresh</span><span class="np-delta">${esc(d.headline)}</span>`;
  }catch(e){ /* transport/parse error → plain pill stands */ }
  finally{ _deltaInflight=false; if(_deltaDirty){ _deltaDirty=false; scheduleDelta(); } }   // guarantee the last burst gets a fresh headline
}

/* ---------- settings: manage connections ---------- */
let setReturnFocus=null;
function settingsOpen(o){
  const m=$("#settingsModal");
  if(o){ openDrawer(false); setReturnFocus=document.activeElement; m.classList.add("show"); $("#scrim").classList.add("on"); loadSettings(); }
  else { m.classList.remove("show"); _syncScrim(); try{setReturnFocus&&setReturnFocus.focus();}catch(e){} }
}
function settingsIsOpen(){ return $("#settingsModal").classList.contains("show"); }
async function loadSettings(){
  $("#setList").innerHTML='<div class="nores" style="padding:30px">Loading…</div>';
  let d; try{ d=await api("/api/accounts"); }catch(e){ $("#setList").innerHTML='<div class="nores" style="padding:30px">Couldn’t load your connections.</div>'; return; }
  state.setRows=d.accounts||[]; state.signatureDefault=d.signature_default||state.signatureDefault||""; renderSettings();
  try{ const h=await api("/api/account-health"); state.health={}; (h.accounts||[]).forEach(a=>state.health[a.account]=a); renderSettings(); }catch(e){}
}
function healthLine(email){
  const a=state.health&&state.health[email]; if(!a)return "";
  if(a.last_error&&a.last_error_kind==="TLS_PIN")   /* the loopback server's certificate isn't the one pinned */
    return `<div class="set-health err">${ico("alert")} certificate changed${a.last_error_ts?" · "+esc(relWhen(a.last_error_ts)):""}: the mail server on this computer showed a different certificate, so MailMind sent no password. If you reinstalled or reset Proton Bridge, trust the new one. <button class="set-mini" data-a="trust">Trust it</button></div>`;
  if(a.last_error&&a.last_error_kind)
    return `<div class="set-health err">${ico("alert")} sync error · ${esc(a.last_error_kind)}${a.last_error_ts?" · "+esc(relWhen(a.last_error_ts)):""} <button class="set-mini" data-a="retry">↻ Retry</button></div>`;
  if(a.last_ok_ts)
    return `<div class="set-health ok">✓ synced ${esc(relWhen(a.last_ok_ts))} · ${a.sync_count} sync${a.sync_count===1?"":"s"}</div>`;
  return "";
}
function relWhen(iso){ try{ const s=Math.max(0,(Date.now()-Date.parse(iso))/1000);
  if(s<90)return "just now"; if(s<5400)return Math.round(s/60)+"m ago"; if(s<172800)return Math.round(s/3600)+"h ago"; return Math.round(s/86400)+"d ago"; }catch(e){ return ""; } }
function statusFor(email){
  const h=(state.statusMap&&state.statusMap[email])?state.statusMap[email].health:"";
  let cnt=null,err=null;
  if(state.liveStats){ const a=(state.liveStats.accounts||[]).find(x=>x.account===email); if(a){cnt=a.error?null:a.in_inbox; err=a.error;} }
  return {h:err?"error":h, cnt, err};
}
function credMarker(c){
  if(!c||!c.set) return '<span class="set-cred">no password set</span>';
  if(c.style==="plain") return `<span class="set-cred"><span class="lock">${ico("lock")}</span> ••••••••</span>`;
  if(c.style==="op"){ const ref=Array.isArray(c.ref)?c.ref[0]:c.ref; return `<span class="set-cred">${ico("key")} ${esc(ref)}</span>`; }
  if(c.style==="env") return `<span class="set-cred">env $${esc(c.var)}</span>`;
  if(c.style==="proton_bridge") return '<span class="set-cred">Proton Bridge</span>';
  return '<span class="set-cred">—</span>';
}
function exportBlockHtml(){
  return `<div class="set-form" id="exportBlock">
    <div class="lbl">Backup &amp; Export</div>
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:8px">
      <span id="exportPlan" class="set-help" style="margin:0">Counting…</span>
      <a class="set-mini" href="/api/export" download title="Download a full local snapshot of your synced mail">${ico("download")} Backup (.db)</a></div>
    <div class="set-help">Portable per-message files: <code>python3 export.py --format eml --out DIR</code> (or <code>mbox</code>). All local · read-only · never opens IMAP.</div>
  </div>`;
}
async function loadExportPlan(){
  try{ const d=await api("/api/export/plan"); const el=$("#exportPlan"); if(el&&d&&d.ok)
    el.textContent=`${d.total.toLocaleString()} messages · ${d.bodies.toLocaleString()} with full bodies · ${d.groups.length} folders`; }
  catch(e){}
}
function renderSettings(){
  const rows=state.setRows||[];
  $("#setHint").textContent=`${rows.length} connection${rows.length===1?"":"s"}`;
  const head=exportBlockHtml();
  if(!rows.length){ $("#setList").innerHTML=head+'<div class="nores" style="padding:36px">No email connections yet.<br>Add one to get started.</div>'; loadExportPlan(); return; }
  $("#setList").innerHTML=head+rows.map(r=>{
    const st=statusFor(r.email), label=r.alias||short(r.email);
    const hostbit=r.type==="imap"?`<span>${esc(r.host)}:${r.port}${r.starttls?" · STARTTLS":""}</span>`:"";
    const right=st.cnt!=null?`<span style="color:var(--mute)">${st.cnt}</span>`:(st.err?`<span style="color:var(--err)">${esc(String(st.err).slice(0,18))}</span>`:"");
    return `<div class="set-row${r.disabled?" disabled":""}" data-email="${esc(r.email)}">
      <div class="chip-dot ${st.h}" style="color:hsl(${hue(r.email)} var(--mono-s) var(--mono-l))">${esc((label[0]||"?").toUpperCase())}<span class="led"></span></div>
      <div class="who"><div class="nm">${r.alias?esc(r.alias):'<span class="noalias">'+esc(short(r.email))+' · no alias</span>'}</div>
        <div class="em">${esc(r.email)}</div>
        <div class="meta2"><span class="set-tag">${esc(r.type)}</span>${hostbit}${credMarker(r.credential)}${r.disabled?'<span>· disabled</span>':''}</div>
        ${healthLine(r.email)}</div>
      <div class="acts">${right}<span class="set-status" data-st></span>
        <button class="set-mini" data-a="edit">Edit</button>
        <button class="set-mini" data-a="test">Test</button>
        <button class="set-mini danger" data-a="remove">Remove</button></div></div>`;
  }).join("");
  loadExportPlan();
}
function segVal(form,name){ const b=form.querySelector(`[data-seg="${name}"] .on`); return b?b.dataset.v:null; }
function fval(form,key){ const el=form.querySelector(`[data-f="${key}"]`); return el?el.value.trim():""; }
function segHtml(name,opts,cur){ return `<div class="seg" data-seg="${name}">`+opts.map(([v,l])=>`<button type="button" data-v="${esc(v)}" class="${v===cur?"on":""}">${l}</button>`).join("")+`</div>`; }
function credField(form){
  const type=segVal(form,"type"); const style=type==="proton"?"proton_bridge":segVal(form,"cred");
  const r=form._row, cur=r?r.credential:null;
  let html="";
  if(style==="plain") html=`<div class="pwwrap"><input type="password" data-f="credpw" placeholder="${r?"•••••••• unchanged — leave blank to keep":"paste the password / app password"}"><button type="button" class="eye" data-a="eye" aria-label="Show password">${ico("eye")}</button></div>${type==="gmail"?'<div class="set-help">Gmail needs an App Password (2-Step Verification → App passwords), not your login password.</div>':""}`;
  else if(style==="op") html=`<input type="text" data-f="credop" value="${cur&&cur.style==="op"?esc(Array.isArray(cur.ref)?cur.ref[0]:cur.ref):""}" placeholder="op://Vault/Item/Field"><div class="set-help">Read from the 1Password CLI at runtime.</div>`;
  else if(style==="env") html=`<input type="text" data-f="credenv" value="${cur&&cur.style==="env"?esc(cur.var):""}" placeholder="ENV_VAR_NAME"><div class="set-help">Read from an environment variable.</div>`;
  else if(style==="proton_bridge") html=`<div class="set-help">Uses the local Proton Bridge password (env MAILMIND_PROTON_BRIDGE_PASS / op / ~/.config/mailmind).</div>`;
  form.querySelector("[data-credfield]").innerHTML=html;
}
function refreshConditionals(form){
  const type=segVal(form,"type");
  form.querySelector("[data-imaponly]").style.display = type==="imap" ? "" : "none";
  const styles = type==="proton" ? [["proton_bridge","Proton Bridge"]] : [["plain","Password"],["op","1Password"],["env","Env var"]];
  const r=form._row; let cur = type==="proton"?"proton_bridge":(r&&r.credential.style&&["plain","op","env"].includes(r.credential.style)?r.credential.style:"plain");
  form.querySelector("[data-credsegwrap]").innerHTML=segHtml("cred",styles,cur);
  credField(form);
}
function openForm(email){
  const r=email?(state.setRows||[]).find(x=>x.email===email):null;
  const f=document.createElement("div"); f.className="set-form"; f.dataset.edit=email||""; f._row=r;
  f.innerHTML=`
    <div class="lbl">Alias (optional)</div>
    <input type="text" data-f="alias" value="${r&&r.alias?esc(r.alias):""}" placeholder="Friendly name shown on the dots & rows">
    <div class="lbl">Signature (optional)</div>
    <textarea data-f="signature" rows="3" class="set-sig" placeholder="Appended to new mail and above quoted replies${state.signatureDefault?" — blank uses the global default":""}">${r&&r.signature?esc(r.signature):""}</textarea>
    <div class="lbl">Email</div>
    <input type="email" data-f="email" value="${r?esc(r.email):""}" ${r?"readonly":""} placeholder="you@example.com">
    ${r?'<div class="set-help">Identity is fixed — remove and re-add to change the address.</div>':""}
    <div class="lbl">Type</div>
    ${segHtml("type",[["gmail","Gmail"],["imap","IMAP"],["proton","Proton"]], r?r.type:"gmail")}
    <div data-imaponly>
      <div class="twocol"><div><div class="lbl">Host</div><input type="text" data-f="host" value="${r&&r.host?esc(r.host):""}" placeholder="mail.yourhost.com"></div>
        <div><div class="lbl">Port</div><input type="number" data-f="port" value="${r?r.port:993}"></div></div>
      ${segHtml("sec",[["tls","TLS"],["starttls","STARTTLS"]], r&&r.starttls?"starttls":"tls")}
    </div>
    <div class="lbl">Credential</div>
    <div data-credsegwrap></div>
    <div data-credfield></div>
    <div class="err" data-err></div>
    <div class="formbar">
      <button class="set-mini" data-a="testdraft">Test connection</button><span class="set-status" data-st></span>
      <button class="abtn" data-a="cancel" style="margin-left:auto">Cancel</button>
      <button class="abtn prim" data-a="save">Save connection</button></div>`;
  $("#setList").innerHTML=""; $("#setList").appendChild(f);
  refreshConditionals(f);
  const al=f.querySelector('[data-f="alias"]'); al&&al.focus();
}
function collectPayload(form){
  const r=form._row, type=segVal(form,"type");
  const p={ alias: fval(form,"alias"), signature: fval(form,"signature"), type };
  p.email = r ? r.email : fval(form,"email");
  if(type==="imap"){ p.host=fval(form,"host"); p.port=+fval(form,"port")||993; p.starttls = segVal(form,"sec")==="starttls"; }
  const style = type==="proton" ? "proton_bridge" : segVal(form,"cred");
  let credential=null;
  if(style==="plain"){ const v=fval(form,"credpw"); if(v) credential={style:"plain",value:v}; }
  else if(style==="op"){ const ref=fval(form,"credop"); if(ref) credential={style:"op",ref}; }
  else if(style==="env"){ const v=fval(form,"credenv"); if(v) credential={style:"env",var:v}; }
  else if(style==="proton_bridge"){ credential={style:"proton_bridge"}; }
  if(credential) p.credential=credential;
  return {p, isEdit:!!r, credential};
}
async function saveForm(form){
  const err=form.querySelector("[data-err]"); err.textContent="";
  const {p,isEdit,credential}=collectPayload(form);
  if(!isEdit && (!p.email||!p.email.includes("@"))){ err.textContent="A valid email is required."; return; }
  if(p.type==="imap" && !p.host){ err.textContent="A host is required for a generic IMAP account."; return; }
  if(!isEdit && !credential){ err.textContent="Set a password (or 1Password ref / env var)."; return; }
  const btn=form.querySelector('[data-a="save"]'); btn.textContent="Saving…"; btn.disabled=true;
  try{
    const d=await api("/api/accounts/"+(isEdit?"edit":"add"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(p)});
    if(!d.ok){ err.textContent=d.error||"Could not save."; btn.textContent="Save connection"; btn.disabled=false; return; }
    toast((isEdit?"Updated ":"Added ")+short(p.email));
    await loadSettings(); await loadDigest(false); refreshStats();
    if(!isEdit) sync();   // a brand-new connection has no mail yet — pull it now instead of waiting for the next auto-sync
  }catch(e){ err.textContent="Save failed."; btn.textContent="Save connection"; btn.disabled=false; }
}
async function testConn(payload,statusEl){
  statusEl.className="set-status run"; statusEl.textContent="Testing… (approve Touch ID if asked)";
  try{ const d=await api("/api/accounts/test",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    if(d.ok&&d.connect){ statusEl.className="set-status ok"; statusEl.textContent="✓ Connected"; }
    else { statusEl.className="set-status err"; statusEl.textContent="✕ "+(d.error||"failed"); }
  }catch(e){ statusEl.className="set-status err"; statusEl.textContent="✕ test failed"; }
}
let pendingRemove=null;
$("#setList").addEventListener("click",e=>{
  const form=e.target.closest(".set-form");
  if(form){
    const seg=e.target.closest("[data-seg] button");
    if(seg){ const wrap=seg.closest("[data-seg]"); wrap.querySelectorAll("button").forEach(x=>x.classList.remove("on")); seg.classList.add("on");
      const name=wrap.dataset.seg; if(name==="type") refreshConditionals(form); else if(name==="cred") credField(form); return; }
    const a=(e.target.closest("[data-a]")||{}).dataset?.a;
    if(a==="cancel") renderSettings();
    else if(a==="save") saveForm(form);
    else if(a==="eye"){ const pw=form.querySelector('[data-f="credpw"]'); if(pw) pw.type=pw.type==="password"?"text":"password"; }
    else if(a==="testdraft"){ const {p,credential}=collectPayload(form); const st=form.querySelector("[data-st]");
      if(credential) testConn({draft:{email:p.email,type:p.type,host:p.host,port:p.port,starttls:p.starttls,credential:credential}},st);
      else if(form._row) testConn({email:form._row.email},st);
      else { st.className="set-status err"; st.textContent="enter a password to test"; } }
    return;
  }
  const row=e.target.closest(".set-row"); if(!row)return; const email=row.dataset.email;
  const a=(e.target.closest("[data-a]")||{}).dataset?.a; if(!a)return;
  if(a==="edit") openForm(email);
  else if(a==="retry"){ const btn=e.target.closest("[data-a]"); if(btn){btn.textContent="↻ Syncing…";btn.disabled=true;}
    cpost("/api/sync-retry-account",{account:email}).then(()=>{ toast("Re-syncing "+short(email)); setTimeout(loadSettings,2500); }).catch(()=>{ toast("Couldn’t retry"); loadSettings(); }); }
  else if(a==="trust"){ const btn=e.target.closest("[data-a]"); if(btn){btn.textContent="Trusting…";btn.disabled=true;}
    cpost("/api/accounts/trust-cert",{email}).then(()=>{ toast("Trusted · re-syncing "+short(email)); setTimeout(loadSettings,2500); }).catch(()=>{ toast("Couldn’t trust it"); loadSettings(); }); }
  else if(a==="test") testConn({email}, row.querySelector("[data-st]"));
  else if(a==="remove"){
    const btn=e.target.closest("[data-a]");
    if(pendingRemove===email){ pendingRemove=null; doRemove(email); }
    else { pendingRemove=email; btn.textContent="Confirm?"; btn.classList.add("ok"); setTimeout(()=>{if(pendingRemove===email){pendingRemove=null;btn.textContent="Remove";btn.classList.remove("ok");}},3000); }
  }
});
async function doRemove(email){
  try{ const d=await api("/api/accounts/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email})});
    if(d.ok){ toast("Removed "+short(email)); await loadSettings(); await loadDigest(false); refreshStats(); } else toast(d.error||"Couldn’t remove"); }
  catch(e){ toast("Remove failed"); }
}

/* ---------- events ---------- */
$("#search").addEventListener("input",e=>{state.q=e.target.value; onClientSearch();});
$("#search").addEventListener("blur",()=>{ if(!$("#search").value.trim())closeSearchBar(); });   // an empty bar folds back; a live query stays shown
$("#searchTog").addEventListener("click",openSearchBar);
$("#searchX").addEventListener("mousedown",e=>e.preventDefault());   // keep focus in the field so blur doesn't fold the bar under the tap
$("#searchX").addEventListener("click",cancelSearchBar);
_compactTop.addEventListener("change",e=>{ if(!e.matches)closeSearchBar(); });
/* ── mobile: swipe a mailbox row — left = archive, right = snooze to tomorrow (both reversible via z) ── */
let _sw=null;
function _swStart(e){
  if(e.touches.length!==1)return; const row=e.target.closest&&e.target.closest("#rows .row.cmsg"); if(!row)return;
  if(e.target.closest("[data-mark]"))return;                 // leave the select checkbox alone
  const t=e.touches[0]; _sw={row, pk:+row.dataset.pk, x0:t.clientX, y0:t.clientY, dx:0, lock:0};
}
function _swMove(e){
  if(!_sw)return; const t=e.touches[0]; const dx=t.clientX-_sw.x0, dy=t.clientY-_sw.y0;
  if(!_sw.lock){ if(Math.abs(dx)<10&&Math.abs(dy)<10)return;
    if(Math.abs(dy)>=Math.abs(dx)){ _sw=null; return; }       // vertical → let the list scroll
    _sw.lock=1; _sw.row.style.transition="none"; }
  e.preventDefault();                                          // committed horizontal → own the gesture
  _sw.dx=dx; _sw.row.style.transform="translateX("+dx+"px)";
  const b=$("#swipeBadge"); if(!b)return;
  if(Math.abs(dx)>14){ const left=dx<0; const r=_sw.row.getBoundingClientRect();
    const k=left?"archive":"snooze"; b.className="show "+k;
    if(b.dataset.k!==k){ b.dataset.k=k; b.innerHTML=left?ico("archive")+" Archive":ico("clock")+" Tomorrow"; }   // rebuild only when the direction flips
    b.style.top=(r.top+r.height/2-17)+"px"; b.style.left=left?"auto":"14px"; b.style.right=left?"14px":"auto";
    b.style.opacity=Math.min(1,(Math.abs(dx)-14)/70).toFixed(2);
  } else b.className="";
}
function _swEnd(){
  if(!_sw)return; const {row,pk,dx,lock}=_sw; const b=$("#swipeBadge"); if(b)b.className=""; _sw=null;
  if(!lock)return;
  if(Math.abs(dx)>80){ row.style.transition=""; row.style.transform="";
    if(dx<0) carchive(pk);                                    // ← archive (heals + undo float)
    else { _snoozeOverride={pk}; doSnooze("tomorrow"); }      // → snooze to tomorrow 8am (heals + undo)
  } else { row.style.transition="transform .2s var(--ease,ease)"; row.style.transform=""; }
}
$("#rows").addEventListener("touchstart",_swStart,{passive:true});
$("#rows").addEventListener("touchmove",_swMove,{passive:false});
$("#rows").addEventListener("touchend",_swEnd);
$("#rows").addEventListener("touchcancel",_swEnd);
$("#doPanel").addEventListener("click",e=>{
  if(e.target===$("#doPanel")){ doDismiss(); return; }                 // backdrop dismiss
  if(e.target.closest("[data-do-retry]")){ renderDoUI("loading",{cmd:state._doCmd||""}); runDo(state._doCmd||"", ++state.doSeq); return; }
  const dcf=e.target.closest("[data-do-confirm]");
  if(dcf){ const p=state._doPlan; if(!p)return;
    if((p.verb==="trash"||p.verb==="junk") && !state._doArmed){        // destructive → arm, require a 2nd click
      state._doArmed=true; dcf.textContent="Click again to confirm"; dcf.classList.add("armed"); return; }
    doConfirm(p); return; }
  const sct=e.target.closest("[data-cleanup-scope]");                  // sender ⇄ whole-domain toggle
  if(sct){ if(state._cleanupPlan){ state._cleanupPlan.scope=sct.dataset.cleanupScope; state._doArmed=false; renderCleanupPlan(); } return; }
  const ccf=e.target.closest("[data-cleanup-confirm]");                // arm-to-confirm (like destructive bulk)
  if(ccf){ if(!state._cleanupPlan)return;
    if(!state._doArmed){ state._doArmed=true; ccf.textContent="Click again to confirm"; ccf.classList.add("armed"); return; }
    cleanupConfirm(); return; }
  if(e.target.closest("[data-do-cancel]")){ doDismiss(); return; }
  if(e.target.closest("[data-do-search]")){ const q=(state._doPlan&&state._doPlan.filter)||""; state._doPlan=null; state.doSeq++; closeDoPanel(); state.searching=true; $("#search").value=q; state.q=q; $("#clientCtx").innerHTML=`${ico("search")} <b>Search</b>`; runSearch(); return; }
});
$("#doPanel").addEventListener("change",e=>{                          // the cleanup's unsubscribe option
  if(e.target.id==="doUnsubChk"&&state._cleanupPlan) state._cleanupPlan.do_unsub=e.target.checked;
});
function doDismiss(){ state._doPlan=null; state._cleanupPlan=null; state.doSeq++; closeDoPanel(); const s=$("#search");
  if(s&&s.value.startsWith("!")){ clearSearch(false); state.doSeq++; loadCMessages(); }   // restore the mailbox (don't leave a stale/ghost search under the closed modal)
  $("#clientCtx").innerHTML=""; }
$("#reviewPanel").addEventListener("click",e=>{
  if(e.target===$("#reviewPanel")){ closeReview(); return; }            // backdrop dismiss
  if(e.target.closest("#rvRerun,[data-rv-retry]")){ _reviewLoaded=false; loadReview(true); return; }
  const it=e.target.closest("[data-rv-pk]"); if(it)reviewOpen(+it.dataset.rvPk, it.dataset.rvAcct, it.dataset.rvTid);
});
$("#docsPanel").addEventListener("click",e=>{
  if(e.target===$("#docsPanel")){ closeDocs(); return; }                // backdrop dismiss
  if(e.target.closest("[data-doc-dl]")) return;                          // let the download link do its thing
  const fc=e.target.closest("[data-dk]"); if(fc){ _docKind=fc.dataset.dk; clearTimeout(_docTyping); loadDocs(); return; }
  const more=e.target.closest("[data-doc-more]"); if(more){ more.disabled=true; more.textContent="Loading…"; loadDocs(true); return; }
  const row=e.target.closest("[data-doc-pk]"); if(row)docOpen(+row.dataset.docPk, row.dataset.docAcct);
});
$("#docSearch").addEventListener("input",e=>{ _docQ=e.target.value; clearTimeout(_docTyping); _docTyping=setTimeout(loadDocs,120); });
$("#commitPanel").addEventListener("click",e=>{
  if(e.target===$("#commitPanel")){ closeCommitments(); return; }      // backdrop dismiss
  const row=e.target.closest(".commit-row"); if(!row)return; const pk=+row.dataset.pk;
  const a=e.target.closest("[data-cm]"); const cm=a?a.dataset.cm:"open";
  if(cm==="done"){ archiveSel(pk); _commitAfter(row); }               // fulfilled → archive (reversible via z)
  else if(cm==="later"){ _snoozeOverride={pk}; doSnooze("tomorrow"); _commitAfter(row); }
  else if(cm==="openmsg"){ openIt(pk); }
  else { closeCommitments();
    openFromBrief(pk); }   // in the reader, where every verb is
});
$("#folderrail").addEventListener("click",e=>{
  const del=e.target.closest("[data-vdel]"); if(del){ e.stopPropagation(); deleteView(del.dataset.vdel); return; }
  const f=e.target.closest(".frow"); if(!f)return;
  if(f.dataset.view!=null)selectView(f.dataset.view); else selectFolder(f.dataset.folder);
  closeRail();                       // mobile: picking a folder closes the drawer
});
$("#railToggle").addEventListener("click",toggleRail);
// splitter: drag, or focus it and use ←/→ (Home/End for the limits) — the keyboard path is the WCAG 2.5.7 alternative to dragging
$("#splitter").addEventListener("pointerdown",e=>{
  const sp=e.currentTarget; e.preventDefault(); sp.setPointerCapture(e.pointerId); sp.classList.add("drag");
  const x0=e.clientX, w0=_listW;
  const mv=ev=>applyListW(w0+ev.clientX-x0);
  const up=()=>{ sp.classList.remove("drag"); sp.removeEventListener("pointermove",mv); sp.removeEventListener("pointerup",up); sp.removeEventListener("pointercancel",up); applyListW(_listW,true); };
  sp.addEventListener("pointermove",mv); sp.addEventListener("pointerup",up); sp.addEventListener("pointercancel",up);
});
$("#splitter").addEventListener("keydown",e=>{
  const w={ArrowLeft:_listW-24, ArrowRight:_listW+24, Home:300, End:560}[e.key];
  if(w!=null){ e.preventDefault(); applyListW(w,true); }
});
$("#splitter").addEventListener("dblclick",()=>applyListW(LIST_W0,true));
$("#rows").addEventListener("dblclick",e=>{ if(e.target.closest(".row.cmsg") && !e.target.closest("[data-mark],[data-racts]")) setReadFocus(true); });
$("#clientCtx").addEventListener("click",e=>{ if(e.target.id==="ctxSaveView")saveCurrentSearch(); else if(e.target.closest("[data-dg-back]"))leaveDigestList(); });
$("#monos").addEventListener("click",e=>{ if(!e.target.closest("#acctSw"))return; e.stopPropagation(); if($("#acctMenu"))closeAcctMenu(); else openAcctMenu(); });
$("#rows").addEventListener("click",e=>{
  if(e.target.closest("[data-brief-row]")){ showBrief(true); return; }
  if(e.target.closest("[data-firstrun]")){ settingsOpen(true); return; }
  const al=e.target.closest("[data-scr-allow]"); if(al){ screenerDecide(al.dataset.scrAllow,"keep"); return; }
  const bl=e.target.closest("[data-scr-block]"); if(bl){ screenerDecide(bl.dataset.scrBlock,"block"); return; }
  const fa=e.target.closest("[data-fr-arch]"); if(fa){ e.stopPropagation(); feedArchive(+fa.dataset.frArch); return; }
  const fc=e.target.closest(".fr-card"); if(fc){ $$("#rows .fr-card.reading").forEach(n=>n.classList.remove("reading")); fc.classList.add("reading"); openClientMsg(+fc.dataset.pk,false); return; }
  const pt=e.target.closest(".pt-row"); if(pt){ $$("#rows .pt-row.reading").forEach(n=>n.classList.remove("reading")); pt.classList.add("reading"); openClientMsg(+pt.dataset.pk,false); return; }
  if(e.target.closest("[data-askrerun]")){ runAsk(); return; }
  const asrc=e.target.closest("[data-asksrc]"); if(asrc){ $$("#rows .ask-src.reading").forEach(n=>n.classList.remove("reading")); asrc.classList.add("reading"); openClientMsg(+asrc.dataset.asksrc,false); return; }
  if(e.target.closest("[data-resync]")){ loadCMessages(); return; }   // a folder whose first scan failed: open it again
  const lm=e.target.closest("[data-loadmore]");
  if(lm){ lm.textContent="Loading…"; (lm.dataset.loadmore==="search"?loadMoreSearch():loadMoreClient()); return; }
  const cb=e.target.closest("[data-mark]");
  if(cb){ cmarkToggle(+cb.dataset.mark); e.stopPropagation(); return; }
  const ra=e.target.closest("[data-racts]");
  if(ra){ e.stopPropagation(); const pk=+ra.closest(".row").dataset.pk, it=cfindAny(pk), a=ra.dataset.racts;
    if(a==="archive")carchive(pk); else if(a==="trash")ctrash(pk);
    else if(it)openSnoozeMenu(ra, (it.count||1)>1&&it.thread_id?{thread_id:it.thread_id, account:it.account||state.account}:{pk});
    return; }
  const r=e.target.closest(".row");if(!r)return;
  const it=(state.searching?state.searchItems:state.cmsgs).find(x=>x.pk===+r.dataset.pk); if(it)openConversation(it,false); });
$("#clientCtx").addEventListener("click",e=>{ if(e.target.closest("#scopeTog"))toggleSearchScope(); });
$("#threadToggle").addEventListener("click",()=>setThreadView(!state.threadView));
$("#lhTabs").addEventListener("click",e=>{ const b=e.target.closest("[data-tab]"); if(b)setInboxTab(b.dataset.tab); });
$("#lhTabs").addEventListener("keydown",e=>{   // a tablist: ←/→ move between the tabs
  if(e.key!=="ArrowLeft"&&e.key!=="ArrowRight")return; e.preventDefault(); e.stopPropagation();
  const i=INBOX_TABS.findIndex(x=>x[0]===state.inboxTab), d=e.key==="ArrowRight"?1:INBOX_TABS.length-1;
  setInboxTab(INBOX_TABS[(i+d)%INBOX_TABS.length][0]); });
$("#rows").addEventListener("click",e=>{ const b=e.target.closest(".nores [data-tab]"); if(b)setInboxTab(b.dataset.tab); });
$("#bulkbar").addEventListener("click",e=>{const b=e.target.closest("[data-bulk]");if(b)cbulk(b.dataset.bulk,b);});

/* compose wiring */
$("#composeBtn").addEventListener("click",()=>composeOpen({mode:"new"}));
$("#cmpClose").addEventListener("click",()=>composeClose());
$("#cmpDiscard").addEventListener("click",composeDiscard);
$("#cmpSaveDraft").addEventListener("click",()=>composeSaveDraft());   // not silent: the click event isn't a flag
$("#cmpSend").addEventListener("click",e=>guardOpen()?confirmSendGuard("footer",e):composeSend(state.compose&&state.compose._pendingAfter));
$("#cmpSchedule").addEventListener("click",e=>{ e.stopPropagation(); openSendLaterMenu(e.currentTarget); });
$("#cmpFrom").addEventListener("change",e=>{   // live-swap the signature block when the From account changes
  const cmp=state.compose; if(!cmp)return; const from=e.target.value; cmp.from=from; markEdited(cmp);
  const newBlk=sigFor(from)?"\n\n-- \n"+sigFor(from):"";
  const ta=$("#cmpBody");
  if(cmp._sigBlock){                                       // a sig is currently inserted
    if(ta.value.endsWith(cmp._sigBlock)){ const keep=ta.value.length-cmp._sigBlock.length;   // the caret stays in what you wrote
      const a=Math.min(ta.selectionStart,keep), b=Math.min(ta.selectionEnd,keep);
      ta.value=ta.value.slice(0,keep)+newBlk; ta.setSelectionRange(a,b); cmp.body=ta.value; }
    // else: user edited inside the sig region — leave the body untouched
  } else if(newBlk){                                       // none present (e.g. came from a no-sig account) -> append the new one
    const a=ta.selectionStart, b=ta.selectionEnd; ta.value=ta.value+newBlk; ta.setSelectionRange(a,b); cmp.body=ta.value;
  }
  cmp._sigBlock=newBlk; cmp._sigFrom=from; guardStale();
});
$("#cmpAiDraft").addEventListener("click",()=>aiDraftReply());
$("#cmpAiMore").addEventListener("click",e=>{ e.stopPropagation(); if($("#draftMenu"))closeDraftMenu(true); else openDraftMenu(e.currentTarget); });
$("#cmpAiRow").addEventListener("click",e=>{ const b=e.target.closest("[data-rw]"); if(b){ aiRewrite(b.dataset.rw); return; } if(e.target.closest("#cmpRwUndo"))aiRewriteUndo(); });
$("#cmpCcToggle").addEventListener("click",()=>{ $("#cmpCcRow").hidden=false; $("#cmpBccRow").hidden=false; const i=fieldEl("cc").querySelector(".ci"); if(i)i.focus(); });
$("#cmpAttachBtn").addEventListener("click",()=>$("#cmpFile").click());
$("#cmpFile").addEventListener("change",e=>{ addAttachments(e.target.files); e.target.value=""; });
$("#composeModal").addEventListener("click",e=>{
  const gf=e.target.closest("[data-gfix]"); if(gf){ guardFix(gf.dataset.gfix,gf); return; }
  if(e.target.closest("#cmpGuardBack")){ dismissSendGuard(); return; }
  if(e.target.closest("#cmpGuardGo")){ confirmSendGuard("strip",e); return; }
  const rm=e.target.closest("[data-rmchip]"); if(rm){ const [f,i]=rm.dataset.rmchip.split(":"); removeChip(f,+i); return; }
  const ra=e.target.closest("[data-rmatt]"); if(ra){ removeAttachment(+ra.dataset.rmatt); return; }
  if(e.target.closest("#cmpQToggle")){ toggleQuote(); return; }
  if(e.target.closest(".ci")){ const ci=e.target.closest(".ci"); ci.focus(); }
});
$("#composeModal").addEventListener("keydown",e=>{
  const ci=e.target.closest(".ci"); if(!ci)return; const f=ci.dataset.cf;
  if($("#acMenu")){   // autocomplete open: arrows navigate, Enter/Tab accept the highlighted contact
    if(e.key==="ArrowDown"){ e.preventDefault(); acMove(1); return; }
    if(e.key==="ArrowUp"){ e.preventDefault(); acMove(-1); return; }
    if(e.key==="Escape"){ e.preventDefault(); acClose(); return; }
    if((e.key==="Enter"||e.key==="Tab")&&_acIdx>=0){ e.preventDefault(); acAccept(_acIdx); return; }
  }
  if(e.key==="Enter"||e.key===","||e.key===";"||(e.key==="Tab"&&ci.value.trim())){
    if(ci.value.trim()){ e.preventDefault(); commitChip(f,ci.value); ci.value=""; acClose(); } }
  else if(e.key==="Backspace"&&!ci.value&&state.compose[f].length){ removeChip(f,state.compose[f].length-1); }
});
$("#composeModal").addEventListener("input",e=>{
  const ci=e.target.closest(".ci"); if(!ci)return; const f=ci.dataset.cf;
  markEdited(state.compose);
  const v=ci.value.trim(); clearTimeout(_acTimer);
  if(v.length<1){ acClose(); return; }
  _acTimer=setTimeout(async()=>{
    if(document.activeElement!==ci)return;
    let d; try{ d=await api("/api/contacts?q="+encodeURIComponent(v)); }catch(err){ return; }
    const have=new Set((state.compose[f]||[]).map(x=>(x.v||"").toLowerCase()));
    const items=(d.items||[]).filter(it=>!have.has((it.addr||"").toLowerCase()));
    if(items.length && document.activeElement===ci) acShow(ci,f,items); else acClose();
  },140);
});
$("#cmpBody").addEventListener("input",()=>markEdited(state.compose));
$("#composeModal").addEventListener("input",guardStale);   // any real edit retires an open send-time check
$("#cmpSubject").addEventListener("input",()=>markEdited(state.compose));
$("#outboxBtn").addEventListener("click",toggleOutbox);
$("#outboxFly").addEventListener("click",e=>{
  const pr=e.target.closest("[data-pending-retry]"); if(pr){ pendingAct("retry",+pr.dataset.pendingRetry); return; }
  const pd=e.target.closest("[data-pending-discard]"); if(pd){ pendingAct("discard",+pd.dataset.pendingDiscard); return; }
  const de=e.target.closest("[data-draft-edit]"); if(de){ $("#outboxFly").hidden=true; editDraft(null,+de.dataset.draftEdit); return; }
  const dd=e.target.closest("[data-draft-discard]"); if(dd){ discardWaitingDraft(+dd.dataset.draftDiscard); return; }
  const r=e.target.closest("[data-retry]"); if(r){ retrySend(+r.dataset.retry); return; }
  const sn=e.target.closest("[data-sendnow]"); if(sn){ sendNow(+sn.dataset.sendnow); return; }
  const cx=e.target.closest("[data-cancel]"); if(cx){ cancelScheduled(+cx.dataset.cancel); return; }
});
$("#sendFloat").addEventListener("click",e=>{ if(e.target.id==="sfEdit")reopenFailed(); else if(e.target.id==="sfDismiss")hideSendFloat(); });
$("#reader").addEventListener("click",e=>{const li=e.target.closest("[data-loadimg]");if(li){loadImagesFor(+li.dataset.loadimg);return;}
  const ct=e.target.closest("[data-contact]"); if(ct){ e.stopPropagation(); openContactCard(ct.dataset.contact, ct); return; }
  const rb=e.target.closest("[data-rsvp]"); if(rb){ const row=rb.closest(".inv-rsvp"); inviteRsvp(+row.dataset.rsvpPk, rb.dataset.rsvp, row); return; }
  if(e.target.closest("[data-back]")){ backToList(); return; }
  const nv=e.target.closest("[data-nav]"); if(nv){ cmove(+nv.dataset.nav); return; }
  if(e.target.closest("[data-readfocus]")){ setReadFocus(!readFocusOn()); return; }
  const bf=e.target.closest("[data-brief]"); if(bf){ briefAct(bf.dataset.brief); return; }
  const tp=e.target.closest("[data-brief-topic]"); if(tp){ const g=briefTopics()[+tp.dataset.briefTopic]; if(g)openDigestList("__dg_topic__",g.label,g.items); return; }
  const qr=e.target.closest("[data-qr]"); if(qr){ quickReply(+qr.dataset.qpk, qr.dataset.qr); return; }
  const ba=e.target.closest("[data-bact]"); if(ba){ e.stopPropagation(); briefCardAct(ba.dataset.bact,+ba.closest("[data-pk]").dataset.pk,ba); return; }
  const bo=e.target.closest("[data-bopen]"); if(bo){ openFromBrief(+bo.dataset.bopen); return; }
  const mo=e.target.closest("[data-more]"); if(mo){ e.stopPropagation(); if($("#moreMenu"))closeMoreMenu(); else openMoreMenu(mo); return; }
  const rs=e.target.closest("[data-readset]"); if(rs){ e.stopPropagation(); if($("#readMenu"))closeReadMenu(); else openReadMenu(rs); return; }
  if(e.target.closest("[data-airerun]")){ summarize(true); return; }
  if(e.target.closest("[data-aifull]")){ summarize(false); return; }
  const texp=e.target.closest("[data-texp]"); if(texp){ expandMember(+texp.dataset.texp); return; }
  const tca=e.target.closest("[data-tca]");   // thread-level actions act on the latest message
  if(tca){ const a=tca.dataset.tca;
    if(a==="summarize")return summarize(false);
    if(a==="snooze")return openSnoozeMenu(tca);
    if(a==="mute")return doMute(muteTarget());
    if(a==="unmute")return doUnmute(muteTarget());
    if(a==="dismiss")return doDismissFollowup(muteTarget());
    if(a==="replylater")return doReplyLater(muteTarget());
    if(a==="rldone")return doReplyLaterDone(muteTarget());
    if(a==="archive"||a==="trash"){ if(state.csel!=null)(a==="archive"?carchive:ctrash)(state.csel); return; }   // the whole conversation
    const pk=state.thread&&state.thread.reply_pk; if(pk==null)return;
    if(a==="reply")replyTo(pk,false); else if(a==="draftready"){ _draftReady.has(pk)?openDraftAhead(pk):replyTo(pk,false); }
    else if(a==="replyall")replyTo(pk,true); else if(a==="forward")forwardMsg(pk); return; }
  const ca=e.target.closest("[data-ca]");
  if(ca){ const pk=state.csel; if(pk==null)return; const a=ca.dataset.ca;
    if(a==="star")cstar(pk); else if(a==="unread")cunread(pk); else if(a==="archive")carchive(pk); else if(a==="trash")ctrash(pk);
    else if(a==="junk")cjunk(pk);
    else if(a==="reply")replyTo(pk,false); else if(a==="draftready"){ _draftReady.has(pk)?openDraftAhead(pk):replyTo(pk,false); }
    else if(a==="replyall")replyTo(pk,true); else if(a==="forward")forwardMsg(pk);
    else if(a==="summarize")summarize(false); else if(a==="snooze")openSnoozeMenu(ca);
    else if(a==="mute")doMute(muteTarget()); else if(a==="unmute")doUnmute(muteTarget());
    else if(a==="dismiss")doDismissFollowup(muteTarget());
    else if(a==="replylater")doReplyLater(muteTarget()); else if(a==="rldone")doReplyLaterDone(muteTarget());
    else if(a==="edit")editDraft(pk); return; }
});
$("#statusBtn").addEventListener("click",e=>{ e.stopPropagation(); if($("#statusMenu"))closeStatusMenu(); else openStatusMenu(); });
$("#drawerX").addEventListener("click",()=>openDrawer(false));
$("#scrim").addEventListener("click",()=>{ if(settingsIsOpen())settingsOpen(false); openDrawer(false); closeRail(); });
$("#settingsBtn").addEventListener("click",()=>settingsOpen(!settingsIsOpen()));
$("#setClose").addEventListener("click",()=>settingsOpen(false));
$("#setDone").addEventListener("click",()=>settingsOpen(false));
$("#setAdd").addEventListener("click",()=>openForm(null));

$("#railAccounts").addEventListener("click",e=>{const b=e.target.closest(".sweep");if(b)armSweep(b.dataset.acct);});
$("#floodList").addEventListener("click",e=>{const r=e.target.closest("[data-flood],[data-flood-go]");if(r)openCleanupPreview(r.dataset.floodGo||r.dataset.flood);});
$("#keepList").addEventListener("click",rmChip); $("#blockList").addEventListener("click",rmChip);
function rmChip(e){const c=e.target.closest(".rchip");if(c)postRule(c.dataset.token,"remove").then(()=>toast("Removed "+c.dataset.token));}
$("#console").addEventListener("click",e=>{const l=e.target.closest(".ln"), a=l&&l.dataset.acct;   // an audit line opens its account's mailbox
  if(!a||!(state.accountsList||[]).includes(a))return;   // a removed account has no folders to open
  clearSearch(false); state.allInbox=false; state.account=a; renderMonos(); loadFolders(); openDrawer(false);});
$("#ruleInput").addEventListener("keydown",e=>{if(e.key!=="Enter")return;const t=e.target.value.trim();if(!t)return;postRule(t,e.shiftKey?"block":"keep").then(()=>{toast((e.shiftKey?"Blocked ":"Keeping ")+t);e.target.value="";});});

/* ═══════ Overlay navigation + focus trap (shared) ═══════
   One observer watches every dismissible overlay's open-class + the body class
   (reading / rail-open). On change it (a) makes the background `inert` and keeps
   Tab inside the top modal, and (b) arms a history entry so the phone Back gesture
   unwinds reader→list and closes modals instead of leaving the app. */
const _TRAP_KEEP=new Set(["toast","srLive","swipeBadge","sendFloat","undoFloat","scrim","outboxFly"]);
function _focusables(root){
  return [...root.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')]
    .filter(el=>el.offsetWidth+el.offsetHeight>0 && getComputedStyle(el).visibility!=="hidden");
}
function _activeModal(){                 // topmost open full-screen overlay, ordered by real z-index
  const q=[["palette","show"],["legend","show"],["composeModal","show"],["settingsModal","show"],
           ["doPanel","show"],["commitPanel","show"],["reviewPanel","show"],["docsPanel","show"],["drawer","open"]];
  for(const [id,cls] of q){ const el=document.getElementById(id); if(el&&el.classList.contains(cls))return el; }
  return null;
}
let _trapReturn=null, _trapCur=null;
function _applyInert(active){
  for(const ch of document.body.children) ch.inert = !!active && ch!==active && !_TRAP_KEEP.has(ch.id);
}
function _syncTrap(){
  const active=_activeModal();
  if(active===_trapCur)return;
  if(!_trapCur&&active)_trapReturn=document.activeElement;      // none → open: remember where to return
  _applyInert(active);
  if(active){                                                   // move focus in unless the modal already did
    requestAnimationFrame(()=>{ if(_trapCur===active&&!active.contains(document.activeElement)){ const f=_focusables(active); const t=f[0]||active; if(t.focus)t.focus(); } });
  } else { try{ _trapReturn&&_trapReturn.focus(); }catch(e){} _trapReturn=null; }
  _trapCur=active;
}
document.addEventListener("keydown",e=>{                        // Tab wraps within the active modal
  if(e.key!=="Tab")return; const m=_activeModal(); if(!m)return;
  const f=_focusables(m); if(!f.length){ e.preventDefault(); return; }
  const first=f[0], last=f[f.length-1], a=document.activeElement;
  if(e.shiftKey&&(a===first||!m.contains(a))){ e.preventDefault(); last.focus(); }
  else if(!e.shiftKey&&(a===last||!m.contains(a))){ e.preventDefault(); first.focus(); }
},true);
function _hasDismissable(){ return !!_activeModal()||document.body.classList.contains("rail-open")||(isMobile()&&document.body.classList.contains("reading")); }
function _dismissTop(){   // close the single topmost dismissible layer; true if one closed
  const m=_activeModal();
  if(m){
    if(m.id==="composeModal"){ if(composeIsOpen()){ if($("#draftMenu"))closeDraftMenu(true); else if($("#sendLaterMenu"))closeSendLaterMenu(); else if(guardOpen())dismissSendGuard(); else composeClose(); } }
    else if(m.id==="palette")closePalette();
    else if(m.id==="settingsModal")settingsOpen(false);
    else if(m.id==="doPanel")doDismiss();
    else if(m.id==="commitPanel")closeCommitments();
    else if(m.id==="reviewPanel")closeReview();
    else if(m.id==="docsPanel")closeDocs();
    else if(m.id==="legend")$("#legend").classList.remove("show");
    else if(m.id==="drawer")openDrawer(false);
    return true;
  }
  if(document.body.classList.contains("rail-open")){ closeRail(); return true; }
  if(isMobile()&&document.body.classList.contains("reading")){ backToList(); return true; }
  return false;
}
let _backArmed=false, _selfPop=false;
function _syncBack(){
  const open=_hasDismissable();
  if(open&&!_backArmed){ _backArmed=true; try{history.pushState({mmTrap:1},"");}catch(e){} }
  // last layer closed by any means OTHER than Back (Esc / X / backdrop) → consume our stale trap
  // entry so the NEXT Back gesture isn't silently swallowed unwinding a dead entry.
  else if(!open&&_backArmed){ _backArmed=false; _selfPop=true; try{history.back();}catch(e){ _selfPop=false; } }
}
window.addEventListener("popstate",()=>{
  if(_selfPop){ _selfPop=false; return; }                       // our own history.back() consuming a trap entry
  if(_dismissTop()){                                            // real Back closed a layer
    if(_hasDismissable()){ try{history.pushState({mmTrap:1},"");}catch(e){} }   // more remain → re-arm
    else _backArmed=false;
  } else _backArmed=false;
});
function _overlaySync(){ try{_syncTrap();}catch(e){} try{_syncBack();}catch(e){} }
(function _bootOverlaySync(){
  const obs=new MutationObserver(_overlaySync);
  ["composeModal","settingsModal","palette","legend","doPanel","commitPanel","reviewPanel","docsPanel","drawer"]
    .forEach(id=>{ const el=document.getElementById(id); if(el)obs.observe(el,{attributes:true,attributeFilter:["class"]}); });
  obs.observe(document.body,{attributes:true,attributeFilter:["class"]});   // reading / rail-open
})();

document.addEventListener("keydown",e=>{
  // Command palette: ⌘/Ctrl-K toggles it (suppressed while composing so it never
  // fights the compose chords / recipient autocomplete). Placed first so it beats every guard below.
  if((e.metaKey||e.ctrlKey)&&!e.altKey&&!e.shiftKey&&(e.key==="k"||e.key==="K")){ e.preventDefault(); if(!composeIsOpen())togglePalette(); return; }
  if(paletteIsOpen()){ paletteKey(e); return; }
  // compose modal: send with ⌘/Ctrl+Enter (even from the textarea), Esc closes; otherwise type freely
  if(composeIsOpen()){
    if(e.key==="Escape"&&e.defaultPrevented)return;   // the recipient autocomplete already took this Esc
    if($("#draftMenu")&&(e.key==="Escape"||((e.metaKey||e.ctrlKey)&&e.key==="Enter"))){   // Esc closes the draft options first; ⌘Enter never sends from them
      e.preventDefault(); if(e.key==="Escape")closeDraftMenu(true); return; }
    if($("#sendLaterMenu")&&(e.key==="Escape"||((e.metaKey||e.ctrlKey)&&e.key==="Enter"))){   // same for the Later menu
      const inside=$("#sendLaterMenu").contains(document.activeElement);
      if(e.key==="Escape"){ e.preventDefault(); closeSendLaterMenu(); if(inside)$("#cmpSchedule").focus(); return; }
      if(inside){ e.preventDefault(); return; }
      closeSendLaterMenu(); }   // ⌘Enter from the composer with the menu left open behind: drop it and go on
    if(guardOpen()&&e.key==="Escape"){ e.preventDefault(); dismissSendGuard(); return; }   // Esc keeps editing; a second Esc closes
    if((e.metaKey||e.ctrlKey)&&e.key==="Enter"){ e.preventDefault(); if(e.repeat)return;   // a held chord acts once
      if(guardOpen())confirmSendGuard("key",e); else composeSend(state.compose._pendingAfter); return; }
    if((e.metaKey||e.ctrlKey)&&(e.key==="s"||e.key==="S")){ e.preventDefault(); composeSaveDraft(); return; }
    if(e.key==="Escape"){ e.preventDefault(); composeClose(); return; }
    return;
  }
  // the Do confirm modal takes Esc even when the search box has focus (dismiss the mandatory gate first)
  if(e.key==="Escape" && $("#doPanel").classList.contains("show")){ doDismiss(); e.preventDefault(); return; }
  if(e.key==="Escape" && $("#docsPanel").classList.contains("show")){ closeDocs(); e.preventDefault(); return; }   // above the typing guard so Esc closes even from the search box
  const typing=e.target.tagName==="INPUT"||e.target.tagName==="SELECT"||e.target.tagName==="TEXTAREA"||e.target.isContentEditable;
  if(typing){ if(e.key==="Escape")e.target.blur(); return; }
  // commitments agenda overlay: Esc closes it before anything else
  if(e.key==="Escape" && $("#reviewPanel").classList.contains("show")){ closeReview(); e.preventDefault(); return; }   // reviewPanel paints above commitPanel (same z, later in DOM) → close it first
  if(e.key==="Escape" && $("#commitPanel").classList.contains("show")){ closeCommitments(); e.preventDefault(); return; }
  // folder drawer (<=1100px): Esc closes it before anything else
  if(e.key==="Escape" && document.body.classList.contains("rail-open")){ closeRail(); e.preventDefault(); return; }
  // ⋯ More open: ↑/↓ move, Esc closes back to ⋯; swallow the rest
  if($("#moreMenu")){ if(e.key==="Escape")closeMoreMenu(true);
    else if(e.key==="ArrowDown"||e.key==="ArrowUp"){ const bs=$$("#moreMenu button"), i=bs.indexOf(document.activeElement); bs[(i+(e.key==="ArrowDown"?1:bs.length-1))%bs.length].focus(); }
    if(e.key!=="Tab"&&e.key!=="Enter"&&e.key!==" ")e.preventDefault(); return; }
  // account menu open: Esc closes back to the switcher; ↑/↓ move; swallow the rest
  if($("#acctMenu")){ if(e.key==="Escape")closeAcctMenu(true);
    else if(e.key==="ArrowDown"||e.key==="ArrowUp"){ const bs=$$("#acctMenu button"), i=bs.indexOf(document.activeElement); bs[(i+(e.key==="ArrowDown"?1:bs.length-1))%bs.length].focus(); }
    if(e.key!=="Tab"&&e.key!=="Enter"&&e.key!==" ")e.preventDefault(); return; }
  // sync status open: Esc closes back to the pill; ↑/↓ move between its actions; swallow the rest
  if($("#statusMenu")){ if(e.key==="Escape")closeStatusMenu(true);
    else if(e.key==="ArrowDown"||e.key==="ArrowUp"){ const bs=$$("#statusMenu .sm-acts button:not(:disabled)"), i=bs.indexOf(document.activeElement); if(bs.length)bs[(i+(e.key==="ArrowDown"?1:bs.length-1))%bs.length].focus(); }
    if(e.key!=="Tab"&&e.key!=="Enter"&&e.key!==" ")e.preventDefault(); return; }
  // reading settings open: Esc closes back to Aa; Tab/Enter/Space/arrows work its buttons; swallow the rest
  if($("#readMenu")){ if(e.key==="Escape")closeReadMenu(true); if(e.key!=="Tab"&&e.key!=="Enter"&&e.key!==" "&&!e.key.startsWith("Arrow"))e.preventDefault(); return; }
  // snooze popover open: digits 1-4 pick a preset, Esc closes; swallow everything else
  if($("#snoozeMenu")){ if(/^[1-4]$/.test(e.key)){ doSnooze(SNOOZE_PRESETS[+e.key-1][0]); e.preventDefault(); } else if(e.key==="Escape")closeSnoozeMenu(); else if(e.key!=="Tab"&&e.key!=="Enter"&&!e.key.startsWith("Arrow"))e.preventDefault(); return; }
  // settings modal: ? shows help, , / Esc close; nothing else
  if(settingsIsOpen()){ if(e.key==="Escape"){ if(pendingRemove){pendingRemove=null;renderSettings();} else if($("#setList").querySelector(".set-form[data-edit]")){renderSettings();} else settingsOpen(false); e.preventDefault(); } else if(e.key===","||e.key==="g"){ settingsOpen(false); e.preventDefault(); } else if(e.key==="?"){ $("#legend").classList.toggle("show"); e.preventDefault(); } return; }
  if(e.metaKey||e.ctrlKey||e.altKey)return;        // never hijack browser/OS chords (Cmd+R, Cmd+S, …)
  if(state.armed){ if(e.key==="Enter")confirmSweep(); else if(e.key==="Escape")abortSweep(); e.preventDefault(); return; }
  if(e.key==="Escape" && $("#drawer").classList.contains("open") && !$("#legend").classList.contains("show")){ openDrawer(false); e.preventDefault(); return; }   // Curate drawer (an armed sweep, or help above it, takes Esc first)
  if(state.noKeys && e.key!=="Escape" && e.key!=="?")return;   // WCAG 2.1.4 opt-out (set in the help overlay)
  const k=e.key.length===1?e.key.toLowerCase():e.key;          // Shift+E etc. still match
  // global
  if(k===","){ settingsOpen(true); e.preventDefault(); return; }
  if(k==="?"){ toggleLegend(); e.preventDefault(); return; }
  // go-to-folder chord (Mailbox): g then i/s/a/t/k/d/z
  if(state.gotoArmed){ clearTimeout(state.gotoT); state.gotoArmed=false;
    const role={i:"inbox",s:"sent",a:"archive",t:"trash",k:"junk",d:"drafts"}[k];
    if(k==="z"){ selectFolder("__snoozed__"); }
    else if(k==="w"){ selectFolder("__waiting__"); }
    else if(k==="l"){ selectFolder("__replylater__"); }
    else if(role){ const fs=state.folders||[], f=fs.find(x=>x.role===role)||(role==="archive"&&fs.find(x=>x.role==="all"));   // Gmail archives into All Mail
      if(f)selectFolder(f.name); else toast("No "+role+" folder on this account"); }
    e.preventDefault(); return; }
  const sel=state.csel;
  const marked=state.cmarked.size>0;   // with a selection set, the verb keys act on the whole set
  if(k==="j")cmove(1); else if(k==="k")cmove(-1);
  else if(k==="n")cmoveUnread(1); else if(k==="p")cmoveUnread(-1);
  else if(k==="e"){ marked?cbulk("archive"):(sel!=null&&carchive(sel)); }
  else if((k==="#"||k==="d"||k==="Delete"||k==="Backspace")){ marked?cbulk("trash"):(sel!=null&&ctrash(sel)); }
  else if(k==="s"){ marked?cbulk("star"):(sel!=null&&cstar(sel)); }
  else if(k==="u"){ marked?cbulk("unread"):(sel!=null&&cunread(sel)); }
  else if(k==="x"&&sel!=null)cmarkToggle(sel);
  else if(k==="b"){ marked?cbulk("junk"):(sel!=null&&cjunk(sel)); }
  else if(k==="r"&&sel!=null){ _draftReady.has(sel)?openDraftAhead(sel):replyTo(sel,false); }
  else if(k==="a"&&sel!=null)replyTo(sel,true);
  else if(k==="f"&&sel!=null)forwardMsg(sel);
  else if(k==="c")composeOpen({mode:"new"});
  else if(k==="i"&&sel!=null)summarize(false);
  else if(k==="h"&&sel!=null){ const btn=$('#reader [data-ca="snooze"],#reader [data-tca="snooze"]'); openSnoozeMenu(btn||$("#reader")); }
  else if(k==="w"){ if(marked)cbulk("mute"); else if(sel!=null){ if(state.folder==="__muted__")doUnmute(muteTarget()); else doMute(muteTarget()); } }
  else if(k==="l"&&sel!=null){ if(state.folder==="__replylater__")doReplyLaterDone(muteTarget()); else doReplyLater(muteTarget()); }
  else if(k==="Enter"&&e.target.closest("button,a[href],[role=separator]"))return;   // ↵ on a focused control activates that control, nothing more
  else if(k==="Enter"&&sel!=null&&!isMobile())setReadFocus(!readFocusOn());   // desktop: the open message is already in the pane → ↵ toggles focus reading
  else if((k==="Enter"||k==="o")&&sel!=null){ const it=cfindAny(sel); if(it)openConversation(it,true); document.body.classList.add("reading"); }
  else if(k==="[")toggleRail();
  else if(k==="g"){ state.gotoArmed=true; toast("Go to folder:  i·s·a·t·k·d·z·w·l"); state.gotoT=setTimeout(()=>state.gotoArmed=false,1800); }
  else if(k==="z")undo();
  else if(k==="v"&&state.searching&&!state.viewId)saveCurrentSearch();
  else if(k==="/")openSearchBar();
  else if(k==="Escape"){ if($("#legend").classList.contains("show"))$("#legend").classList.remove("show");
    else if(readFocusOn())setReadFocus(false);
    else if(state.cmarked.size)cmarkClear();
    else if(isMobile()&&document.body.classList.contains("reading"))document.body.classList.remove("reading");   // phone: reader → back to the (possibly search) list; a second Esc then clears
    else if(state.searching){ if((state.viewId||"").startsWith("__dg_"))leaveDigestList(); else { clearSearch(false); loadCMessages(); } }
    else document.body.classList.remove("reading"); } else return;
  if(["j","k","n","p","e","#","d","Delete","Backspace","s","u","x","b","r","a","f","c","i","h","w","o","g","z","v","/","["].includes(k))e.preventDefault();
});
let _legendOpener=null;
/* ---------- command palette (⌘K) ---------- */
let _palOpener=null, _palSel=0, _palEntries=[];
function _cap(s){ return s?s[0].toUpperCase()+s.slice(1):s; }
function selectAccountThenFolder(a, name){
  if(state.account!==a){ state.allInbox=false; state.account=a; renderMonos(); loadFolders().then(()=>selectFolder(name)); }
  else selectFolder(name);
}
function buildPaletteEntries(){
  const E=[]; const sel=()=>state.csel!=null||state.thread;
  // NAVIGATION
  E.push({group:"Navigation", label:"All inboxes", fn:()=>{ clearSearch(false); enterAllInbox(); }});
  E.push({group:"Navigation", label:"Commitments — your to-dos", fn:()=>openCommitments()});
  E.push({group:"Navigation", label:"Weekly review — how your week went", fn:()=>openReview()});
  E.push({group:"Navigation", label:"Documents — find any attachment", fn:()=>openDocs()});
  E.push({group:"Navigation", label:"Do… — natural-language bulk action", fn:()=>{ const s=$("#search"); if(s){ openSearchBar(); s.value="!"; state.q="!"; try{s.setSelectionRange(1,1);}catch(_){} onClientSearch(); } }});
  (state.accountsList||[]).forEach(a=>{
    E.push({group:"Navigation", label:"Account · "+nameOf(a), fn:()=>{ clearSearch(false); state.allInbox=false; state.account=a; renderMonos(); loadFolders(); }});
    (state.foldersByAcct&&state.foldersByAcct[a]||[]).forEach(f=>{
      const lbl=nameOf(a)+" / "+roleLabel(f);
      E.push({group:"Navigation", label:lbl, fn:()=>selectAccountThenFolder(a, f.name)});
    });
    E.push({group:"Navigation", label:nameOf(a)+" / Snoozed", fn:()=>selectAccountThenFolder(a,"__snoozed__")});
    E.push({group:"Navigation", label:nameOf(a)+" / Waiting", fn:()=>selectAccountThenFolder(a,"__waiting__")});
  });
  (state.views||[]).forEach(v=>E.push({group:"Views", label:v.name+(v.id.startsWith("__lane_")?" (lane)":""), fn:()=>{ selectView(v.id); }}));
  // ACTIONS on the current selection (only when one exists)
  const pk=()=>state.csel;
  E.push({group:"Actions", label:"Archive", key:"e", enabled:sel, fn:()=>carchive(pk())});
  E.push({group:"Actions", label:"Delete", key:"d", enabled:sel, fn:()=>ctrash(pk())});
  E.push({group:"Actions", label:"Mark junk", key:"b", enabled:sel, fn:()=>cjunk(pk())});
  E.push({group:"Actions", label:"Star", key:"s", enabled:sel, fn:()=>cstar(pk())});
  E.push({group:"Actions", label:"Snooze…", key:"h", enabled:sel, fn:()=>openSnoozeMenu($("#reader"))});
  E.push({group:"Actions", label:state.folder==="__muted__"?"Unmute thread":"Mute thread", key:"w", enabled:sel,
          fn:()=>{ if(state.folder==="__muted__")doUnmute(muteTarget()); else doMute(muteTarget()); }});
  E.push({group:"Actions", label:"Dismiss follow-up", enabled:()=>sel()&&state.folder==="__waiting__", fn:()=>doDismissFollowup(muteTarget())});
  E.push({group:"Actions", label:state.folder==="__replylater__"?"Reply later — Done":"Reply later", key:"l", enabled:sel,
          fn:()=>{ if(state.folder==="__replylater__")doReplyLaterDone(muteTarget()); else doReplyLater(muteTarget()); }});
  E.push({group:"Actions", label:"Reply", key:"r", enabled:sel, fn:()=>replyTo(pk(),false)});
  E.push({group:"Actions", label:"Reply all", key:"a", enabled:sel, fn:()=>replyTo(pk(),true)});
  E.push({group:"Actions", label:"Forward", key:"f", enabled:sel, fn:()=>forwardMsg(pk())});
  E.push({group:"Actions", label:"AI summarize", key:"i", enabled:sel, fn:()=>summarize(false)});
  // COMMANDS (always available)
  E.push({group:"Commands", label:"Compose new message", key:"c", fn:()=>composeOpen({mode:"new"})});
  E.push({group:"Commands", label:"Undo last action", key:"z", fn:()=>undo()});
  E.push({group:"Commands", label:"Sync now", fn:()=>sync()});
  E.push({group:"Commands", label:"Toggle conversations / messages", fn:()=>setThreadView(!state.threadView)});
  E.push({group:"Commands", label:readFocusOn()?"Leave focus reading":"Focus reading — hide folders and list", key:"↵", enabled:()=>!isMobile(), fn:()=>setReadFocus(!readFocusOn())});
  E.push({group:"Commands", label:"Today’s brief", fn:()=>{ if(briefHere())showBrief(true); else if(state.allInbox)loadAllInboxes(); else selectFolder("INBOX"); }});
  E.push({group:"Commands", label:"Folders: icons ⇄ names", key:"[", fn:()=>toggleRail()});
  E.push({group:"Commands", label:"Reading settings — width, text, mail colors", enabled:()=>!!$("#reader [data-readset]"), fn:()=>setTimeout(()=>openReadMenu($("#reader [data-readset]")),0)});
  E.push({group:"Commands", label:"Reconcile (prune ghosts)", fn:()=>reconcile()});
  E.push({group:"Commands", label:"Clean up senders (unsubscribe)", fn:()=>{ openDrawer(true); }});
  THEMES.filter(t=>t!==themeName()).forEach(t=>E.push({group:"Commands", label:"Theme → "+THEME_NAME[t], fn:()=>{ setTheme(t); toast(THEME_NAME[t]+" theme"); }}));
  E.push({group:"Commands", label:"Settings", key:",", fn:()=>settingsOpen(true)});
  E.push({group:"Commands", label:"Keyboard help", key:"?", fn:()=>toggleLegend(true)});
  return E.filter(e=>e.enabled?e.enabled():true);
}
function fuzzyScore(needle, hay){
  needle=(needle||"").toLowerCase(); hay=(hay||"").toLowerCase();
  if(!needle)return 0;
  let h=0, score=0, run=0;
  for(let n=0;n<needle.length;n++){
    const ch=needle[n]; let found=-1;
    for(let i=h;i<hay.length;i++){ if(hay[i]===ch){ found=i; break; } }
    if(found<0)return -Infinity;                       // not a subsequence -> filter out
    if(found===h)run++; else run=0;
    score += 2 + run*2 + ((found===0||" /·-".includes(hay[found-1]))?3:0) - (found-h);
    h=found+1;
  }
  return score - hay.length*0.01;                       // tiny length tiebreak: shorter wins
}
function rankEntries(q, entries){
  if(!q)return entries;                                 // empty query: keep registry order (grouped)
  return entries.map(e=>[fuzzyScore(q,e.label),e]).filter(([s])=>s>-Infinity)
    .sort((a,b)=>b[0]-a[0]).map(([,e])=>e);
}
function paletteIsOpen(){ const p=$("#palette"); return p&&p.classList.contains("show"); }
function togglePalette(){ paletteIsOpen()?closePalette():openPalette(); }
function openPalette(){
  _palOpener=document.activeElement; _palSel=0;
  const el=$("#palette"); el.classList.add("show"); $("#pq").value=""; renderPalette(); $("#pq").focus();
}
function closePalette(){ const el=$("#palette"); if(el)el.classList.remove("show"); try{ _palOpener&&_palOpener.focus(); }catch(e){} _palOpener=null; }
function renderPalette(){
  const q=$("#pq").value.trim();
  _palEntries=rankEntries(q, buildPaletteEntries());
  if(_palSel>=_palEntries.length)_palSel=Math.max(0,_palEntries.length-1);
  const list=$("#plist");
  if(!_palEntries.length){ list.innerHTML='<div class="pnone">No matches.</div>'; return; }
  let html="", lastGrp=null;
  _palEntries.forEach((e,i)=>{
    if(!q && e.group!==lastGrp){ html+=`<div class="pgrp">${esc(e.group)}</div>`; lastGrp=e.group; }
    html+=`<div class="pi${i===_palSel?" sel":""}" role="option" id="pi-${i}" data-idx="${i}">`
      +`<span class="pi-l">${esc(e.label)}</span>${e.key?`<kbd>${esc(e.key)}</kbd>`:""}</div>`;
  });
  list.innerHTML=html;
  const selEl=$("#pi-"+_palSel); if(selEl)selEl.scrollIntoView({block:"nearest"});
  $("#pq").setAttribute("aria-activedescendant","pi-"+_palSel);
}
function paletteKey(e){
  if(e.key==="ArrowDown"||(e.ctrlKey&&e.key==="n")){ e.preventDefault(); _palSel=(_palSel+1)%Math.max(1,_palEntries.length); renderPalette(); }
  else if(e.key==="ArrowUp"||(e.ctrlKey&&e.key==="p")){ e.preventDefault(); _palSel=(_palSel-1+_palEntries.length)%Math.max(1,_palEntries.length); renderPalette(); }
  else if(e.key==="Enter"){ e.preventDefault(); paletteExec(_palSel); }
  else if(e.key==="Escape"){ e.preventDefault(); closePalette(); }
  else if(e.key==="Tab"){ e.preventDefault(); }            // trap focus inside the palette
  // any other key reaches the #pq input; its 'input' listener re-renders
}
function paletteExec(i){
  const e=_palEntries[i]; closePalette();                  // close first so modal-opening cmds get focus
  if(e){ try{ e.fn(); }catch(err){ toast("Couldn’t run that"); } }
}
function toggleLegend(force){
  const el=$("#legend"), on=force===undefined?!el.classList.contains("show"):force;
  el.classList.toggle("show",on);
  if(on){ _legendOpener=document.activeElement; const box=el.querySelector(".box"); if(box)box.focus(); }
  else { try{ _legendOpener&&_legendOpener.focus(); }catch(e){} }
}
function announce(msg){ clearTimeout(_gsT); const r=$("#srLive"); if(r)r.textContent=msg; }   // newer news wins over a queued strip line
$("#legend").addEventListener("click",e=>{if(e.target.id==="legend")toggleLegend(false);});
$("#pq").addEventListener("input",()=>{ _palSel=0; renderPalette(); });
$("#plist").addEventListener("click",e=>{ const r=e.target.closest("[data-idx]"); if(r)paletteExec(+r.dataset.idx); });
$("#plist").addEventListener("mousemove",e=>{ const r=e.target.closest("[data-idx]"); if(r&&+r.dataset.idx!==_palSel){ _palSel=+r.dataset.idx; renderPalette(); } });
$("#palette").addEventListener("click",e=>{ if(e.target.id==="palette")closePalette(); });
$("#helpBtn").addEventListener("click",()=>toggleLegend());
$("#themeBtn").addEventListener("click",()=>toggleTheme());
$("#legNoKeys").addEventListener("change",e=>{ state.noKeys=e.target.checked;
  try{localStorage.setItem("mm_nokeys",state.noKeys?"1":"0");}catch(e){} });
$("#legTheme").addEventListener("click",e=>{ const b=e.target.closest("[data-theme-set]"); if(b)setTheme(b.dataset.themeSet); });

/* ---------- boot ---------- */
(async()=>{ await loadDigest(false); loadStats(); loadOutbox(); connectEvents();
  try{ if(localStorage.getItem("mm_threadview")==="0"){ state.threadView=false; const b=$("#threadToggle"); if(b){b.classList.remove("on");b.innerHTML=ico("list")+" Messages";} } }catch(e){}
  try{ const sc=localStorage.getItem("mm_searchscope"); if(sc)state.searchScope=sc; }catch(e){}
  try{ if(localStorage.getItem("mm_nokeys")==="1"){ state.noKeys=true; const c=$("#legNoKeys"); if(c)c.checked=true; } }catch(e){}
  try{ paintThemeControls(); }catch(e){}   // theme already applied by the early inline script
  try{ localStorage.removeItem("mm_mode"); localStorage.removeItem("mm_brief_view"); }catch(e){}   // Briefing's keys: it retired into Mailbox
  try{ let r=localStorage.getItem("mm_rail");
    if(r==null){ r=localStorage.getItem("mm_mb_view")==="classic"?"wide":"narrow"; localStorage.removeItem("mm_mb_view"); }   // the retired Classic view showed folder names
    applyRailWide(r==="wide"); }catch(e){}
  try{ applyListW(+localStorage.getItem("mm_list_w")||LIST_W0); }catch(e){}
  applyReadWidth();
  loadViews();   // app-global saved views/lanes — load immediately, not gated on a slow folder list
  // prefetch every account's folder list in the background so the ⌘K palette can offer
  // cross-account folder navigation instantly (loadDigest already populated accountsList;
  // the open account's list is already on its way through loadFolders)
  try{ state.foldersByAcct=state.foldersByAcct||{};
    (state.accountsList||[]).forEach(a=>{ if(a!==state.account&&!state.foldersByAcct[a]) api("/api/folders?account="+encodeURIComponent(a))
      .then(d=>{ state.foldersByAcct[a]=d.folders||[]; }).catch(()=>{}); }); }catch(e){} })();
// polls are the fallback when SSE is down; when live, events drive refreshes instead
setInterval(()=>{ if(document.hidden||state.armed||reconciling||state.sseLive)return; loadDigest(true); },45000);   // the brief's counts, when SSE is down
setInterval(()=>{ if(document.hidden||state.armed)return; loadStats(); },120000);
setInterval(()=>{ if(document.hidden||state.sseLive)return; loadOutbox(); },20000);
setInterval(checkSoon,30000); checkSoon();   // runs while hidden too: that's when the desktop notification matters
$("#soonOpen").addEventListener("click",()=>{ const pk=+$("#soonPill").dataset.pk; if(pk)openClientMsg(pk,false); });
$("#soonX").addEventListener("click",()=>{ _soonGone.add($("#soonPill").dataset.k); $("#soonPill").hidden=true; $("#statusBtn").focus(); checkSoon(); });
