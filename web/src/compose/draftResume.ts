// Reopening a saved draft: what the composer opens with and sends back. Pure, like sendGuard.ts, and written
// into assets/legacy/pure.js the same way.
export interface ResumeFields { in_reply_to?: string | null; references?: string | null; tail_text?: string | null }
export interface ResumeNotes { inline?: number; bcc_dropped?: number; plain?: boolean }
export interface ReopenedDraft extends ResumeFields {
  draft_id?: number; mode?: string; account?: string; subject?: string;
  to?: string[]; cc?: string[]; bcc?: string[]; orig_pk?: number | null; participants?: string[];
}
export interface ResumeOptions {
  mode: string; from: string | undefined; to: string[]; cc: string[]; bcc: string[];
  subject: string; subject0: string; origPk: number | null; draftId: number | undefined;
  participants: string[]; quote: { html: null; text: string } | null; fwdAttach: number; noSig: boolean;
  resume: { in_reply_to: string; references: string; tail_text: string };
}

export function resumeOpts(d:ReopenedDraft):ResumeOptions{ const t=d.tail_text||"";   // composeOpen options for a draft the server reopened
  return {mode:d.mode||"new", from:d.account, to:(d.to||[]).slice(), cc:(d.cc||[]).slice(), bcc:(d.bcc||[]).slice(),
    subject:d.subject||"", subject0:d.subject||"", origPk:d.orig_pk!=null?d.orig_pk:null, draftId:d.draft_id,
    participants:(d.participants||[]).slice(), quote:t?{html:null,text:t.replace(/^\n+/,"")}:null, fwdAttach:0, noSig:true,
    resume:{in_reply_to:d.in_reply_to||"", references:d.references||"", tail_text:t}}; }
export function resumeFields(r:ResumeFields|null|undefined):{resume?:ResumeFields}{ return r?{resume:{in_reply_to:r.in_reply_to||"", references:r.references||"", tail_text:r.tail_text||""}}:{}; }
export function resumeHidesAi(cmp:{resume?:unknown,quote?:unknown}|null|undefined):boolean{ return !!(cmp&&cmp.resume&&!cmp.quote); }   // the quote is in the box: an AI draft would replace it
export function resumeSig(text:unknown, sig:string):string{ const blk=sig?"\n\n-- \n"+sig:"";   // the signature block a reopened text still ends with, or ""
  return blk&&String(text||"").endsWith(blk)?blk:""; }
export function resumeNotice(n:ResumeNotes|null|undefined):string{ n=n||{}; const o:string[]=[];
  if((n.inline??0)>0)o.push(n.inline===1?"1 picture in the text is now an attachment.":`${n.inline} pictures in the text are now attachments.`);
  if((n.bcc_dropped??0)>0)o.push("The Bcc wasn’t restored, because this draft changed since MailMind saved it.");
  if(n.plain)o.push("This draft opens as plain text, so its formatting won’t be kept when you save or send it.");
  return o.join(" "); }
