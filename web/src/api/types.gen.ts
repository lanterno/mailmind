// Written by `just gen-api` from what the routes answer in the tests: don't edit. Only shapes, no values.

export interface GetAnswers {
  "/api/account-health": {
    accounts: ({
      account: string;
      healthy: boolean;
      last_error: string | null;
      last_error_kind: string | null;
      last_error_ts: string | null;
      last_ok_ts: string | null;
      live: string;
      sync_count: number;
    })[];
    generated: string;
    ok: boolean;
  };
  "/api/accounts": {
    account?: {
      alias: string;
      credential: {
        set: boolean;
        style: string;
      };
      defaults_applied: {
        host: boolean;
        port: boolean;
        smtp_host: boolean;
        smtp_port: boolean;
        smtp_starttls: boolean;
        starttls: boolean;
        trash: boolean;
      };
      disabled: boolean;
      email: string;
      host: string;
      port: number;
      signature: string;
      smtp_host: string;
      smtp_port: number;
      smtp_starttls: boolean;
      starttls: boolean;
      trash: string;
      type: string;
    };
    accounts?: {
      alias: string;
      credential: {
        set: boolean;
        style: string;
      };
      defaults_applied: {
        host: boolean;
        port: boolean;
        smtp_host: boolean;
        smtp_port: boolean;
        smtp_starttls: boolean;
        starttls: boolean;
        trash: boolean;
      };
      disabled: boolean;
      email: string;
      host: string;
      port: number;
      signature: string;
      smtp_host: string;
      smtp_port: number;
      smtp_starttls: boolean;
      starttls: boolean;
      trash: string;
      type: string;
    }[];
    generated?: string;
    ok: boolean;
    signature_default?: string;
  };
  "/api/ai/draft-ahead": {
    body?: string;
    ok: boolean;
    status: string;
    tone?: string;
  };
  "/api/audit": {
    events: {
      account?: string;
      bucket?: string;
      from?: string;
      kind: string;
      live?: number;
      pruned?: number;
      reason?: string;
      status?: string;
      subject?: string;
      swept?: number;
      token?: string;
      ts: string;
      uid?: string;
    }[];
    has_more: boolean;
  };
  "/api/briefing": {
    cached: boolean;
    generated?: string;
    groups: {
      count: number;
      label: string;
      pks: number[];
    }[];
    missing?: boolean;
    model?: string;
    narrative: string;
    ok: boolean;
    stale: boolean;
  };
  "/api/briefing/since": {
    cached?: boolean;
    count?: number;
    empty?: boolean;
    headline?: string;
    items?: {
      gist: string;
      importance: number;
      who: string;
    }[];
    missing?: boolean;
    ok: boolean;
  };
  "/api/cleanup/sender": {
    accounts?: string[];
    confirm_token?: string;
    count?: number;
    domain?: string;
    error?: string;
    from_addr?: string;
    ok: boolean;
    reversible?: {
      block: boolean;
      sweep: boolean;
      unsubscribe: boolean;
    };
    sweep_capped?: boolean;
    sweep_count?: number;
    unseen?: number;
    unsub?: {
      https_host?: string;
      mailto_to?: string;
      method: string;
    };
  };
  "/api/cleanup/senders": {
    generated: string;
    ok: boolean;
    senders: {
      accounts: {
        count: number;
        email: string;
      }[];
      bucket: string;
      count: number;
      domain: string;
      from_addr: string;
      from_name: string;
      last_seen: string;
      lu_likely: boolean;
      noise_score: number;
      sample_subjects: string[];
      unseen: number;
      unseen_ratio: number;
    }[];
    totals: {
      candidate_messages: number;
      candidate_senders: number;
    };
  };
  "/api/contact": {
    accounts: string[];
    addr: string;
    count: number;
    first_ago: string | null;
    known: boolean;
    last_ago: string | null;
    name: string;
    ok: boolean;
    recent: {
      account: string;
      pk: number;
      seen: boolean;
      subject: string;
      thread_id: string;
      when: string;
    }[];
    replied: number;
  };
  "/api/contacts": {
    items: {
      addr: string;
      n: number;
      name: string;
    }[];
    ok: boolean;
  };
  "/api/debug/ai": {
    batch: {
      estimate_micros: number;
      state: null;
      waiting: number;
    };
    days: {
      calls: number;
      cost_micros: number;
      day: string;
      features: {
        ask?: {
          calls: number;
          cost_micros: number;
        };
        rewrite?: {
          calls: number;
          cost_micros: number;
        };
      };
    }[];
    generated: string;
    governor: {
      breaker: string[];
      interactive_running: number;
      limit_micros: number;
      reserved_micros: number;
      since_interactive: number | null;
      since_seen: number;
      spent_today_micros: number;
    };
    ok: boolean;
  };
  "/api/debug/jobs": {
    generated: string;
    jobs: ({
      every: number;
      failures: number;
      last_error: string | null;
      last_started: number;
      last_took: number;
      name: string;
      next_at: null;
      running: boolean;
      runs: number;
    })[];
    ok: boolean;
    profile: string;
  };
  "/api/debug/pool": {
    counters: {
      created: number;
      discard_dirty: number;
      reused: number;
      validate_fail: number;
    };
    enabled: boolean;
    ok: boolean;
    slots: Record<string, never>;
    target: string;
  };
  "/api/digest": {
    accounts: string[];
    ai: {
      engine: string;
      key_from: string;
      last_error: string;
      last_ok: boolean | null;
      last_ts: number;
      limit_micros: number;
      model: string;
      spent_today_micros: number;
    };
    aliases: Record<string, string | null>;
    generated: string;
    items: {
      account: string;
      action: string;
      alias: string;
      bucket: string;
      category: string;
      commitment_worthy: boolean;
      confidence: string;
      flagged: boolean;
      folder: string;
      from_addr: string;
      from_name: string;
      has_attach: boolean;
      importance: number;
      key: number;
      kind: string;
      last_seen: null;
      link: string;
      needs_you: boolean;
      pk: number;
      reason: string;
      reply_worthy: boolean;
      safe_to_file: boolean;
      seen: boolean;
      subject: string;
      summary: string;
      thread_id: string;
      ts: number;
      verified: boolean;
      when: string;
    }[];
    profile: string;
    send_accounts: string[];
    signature_default: string;
    signatures: Record<string, string>;
    stats: {
      kept: number;
      needs: number;
      summarized: number;
      total: number;
      verified: number;
    };
    status: Record<string, never>;
    sync: {
      last: null;
      result: string;
      running: boolean;
    };
  };
  "/api/documents": {
    docs: {
      account: string;
      filename: string;
      from: string;
      kind: string;
      part_id: string;
      pk: number;
      size: number;
      subject: string;
      url: string;
      when: string;
    }[];
    facets: {
      image?: number;
      pdf?: number;
    };
    matched: number;
    ok: boolean;
    shown: number;
    total: number;
  };
  "/api/export/plan": {
    bodies: number;
    groups: {
      account: string;
      bodies: number;
      folder: string;
      messages: number;
    }[];
    ok: boolean;
    total: number;
  };
  "/api/folders": {
    account: string;
    folders: {
      name: string;
      role: string;
      total: number;
      unseen: number;
    }[];
    muted: number;
    ok: boolean;
    replylater: number;
    snoozed: number;
    waiting: number;
  };
  "/api/message/{pk}/body": {
    account?: string;
    attachments?: ({
      content_id: string | null;
      content_type: string;
      filename: string;
      is_inline: number;
      part_id: string;
      size: number;
    })[];
    blocked_css_urls?: number;
    blocked_images?: number;
    busy?: boolean;
    cc?: string;
    date?: string;
    draft_id?: number;
    draft_mode?: string;
    error?: string;
    from_addr?: string;
    from_name?: string;
    has_html?: boolean;
    html?: string | null;
    in_reply_to?: string;
    invite?: {
      all_day: boolean;
      attendees: {
        email: string;
        name: string;
      }[];
      description: string;
      end_iso: string;
      exdates: unknown[];
      location: string;
      method: string;
      organizer: {
        email: string;
        name: string;
      };
      recurrence: string;
      recurrence_id: string;
      rrule: string;
      start_iso: string;
      status: string;
      summary: string;
      tzid: string;
      uid: string;
      when: string;
    } | null;
    invite_part?: string | null;
    is_draft?: boolean;
    key?: number;
    message_id?: string;
    ok: boolean;
    pk?: number;
    prefetched?: boolean;
    references?: string;
    reply_to?: string;
    subject?: string;
    text?: string | null;
    thread_id?: string;
    to?: string;
  };
  "/api/messages": {
    account: string;
    catching_up?: boolean;
    folder: string;
    grouped?: boolean;
    has_more: boolean;
    items: ({
      account?: string;
      action?: string;
      bucket: string;
      category?: string | null;
      commitment_worthy?: boolean;
      count?: number;
      flagged: boolean;
      folder?: string;
      from_addr: string;
      from_name: string;
      has_attach: boolean;
      importance?: number | null;
      is_junk: boolean;
      key: number;
      kind?: string;
      muted?: boolean;
      needs_you?: boolean;
      pk: number;
      reply_worthy?: boolean;
      replylater?: boolean;
      safe_to_file?: boolean;
      seen: boolean;
      snippet: string;
      snoozed_until?: number;
      subject: string;
      summary?: string;
      thread_id: string;
      to?: string;
      ts?: number;
      waiting?: boolean;
      waiting_label?: string;
      when: string;
    })[];
    muted?: boolean;
    offset: number;
    ok: boolean;
    replylater?: boolean;
    snoozed?: boolean;
    sync_failed?: boolean;
    syncing?: boolean;
    total: number;
    waiting?: boolean;
  };
  "/api/outbox": {
    items: ({
      account: string;
      attempts: number;
      bcc_count: number;
      cc: unknown[];
      created_at: string;
      error: string | null;
      id: number;
      next_attempt_at: number;
      scheduled: boolean;
      sent_at: string | null;
      state: string;
      subject: string;
      to: string[];
      unsure: boolean;
    })[];
    ok: boolean;
  };
  "/api/pending": {
    items: ({
      account: string;
      attempts: number;
      created_at: string;
      dest: string | null;
      error: string | null;
      flags: {
        flagged?: boolean;
        seen?: boolean;
      } | null;
      folder: string;
      id: number;
      next_attempt_at: number;
      op: string;
      state: string;
      subject: string;
    })[];
    stuck: number;
    waiting: number;
  };
  "/api/review": {
    cached?: boolean;
    generated?: string;
    missing?: boolean;
    model?: string;
    narrative?: string;
    needs?: {
      account: string;
      action: string;
      folder: string;
      importance: number;
      key: number;
      pk: number;
      subject: string;
      thread_id: string;
      who: string;
    }[];
    next?: string[];
    ok: boolean;
    stale?: boolean;
    stats?: {
      by_bucket: Record<string, number>;
      cleaned: number;
      needs_you: number;
      received: number;
      reply_later: number;
      sent: number;
      waiting_on: number;
    };
    waiting?: ({
      account: string;
      folder: string | null;
      key: number | null;
      pk: number | null;
      sent_ts: number;
      subject: string;
      thread_id: string;
      to: string;
    })[];
    week_start?: string;
  };
  "/api/rules": {
    rules: {
      block_domains: unknown[];
      block_senders: unknown[];
      expire_hours: Record<string, never>;
      keep_domains: unknown[];
      keep_senders: string[];
    };
  };
  "/api/screener": {
    account: string | null;
    items: {
      count: number;
      domain: string;
      from_addr: string;
      from_name: string;
      when: string;
    }[];
    ok: boolean;
    total: number;
  };
  "/api/search": {
    error?: string;
    generated?: string;
    has_more?: boolean;
    items?: ({
      account: string;
      alias: string;
      bucket: string;
      commitment_worthy: boolean;
      date: string;
      flagged: boolean;
      folder: string;
      from_addr: string;
      from_name: string;
      has_attach: boolean;
      importance: number | null;
      is_junk: boolean;
      key: number;
      needs_you: boolean;
      pk: number;
      reply_worthy: boolean;
      safe_to_file: boolean;
      seen: boolean;
      snippet: string;
      subject: string;
      when: string;
    })[];
    offset?: number;
    ok: boolean;
    query?: string;
    scope?: string;
    total?: number;
  };
  "/api/stats": {
    accounts: {
      account: string;
      alias: string;
      by_bucket: Record<string, number>;
      error: null;
      in_inbox: number;
      junk: number;
      keep: number;
      kind: string;
      top_senders: Record<string, string[]>;
    }[];
    buckets: {
      is_junk: boolean;
      name: string;
    }[];
    generated: string;
    totals: {
      in_inbox: number;
      junk: number;
      keep: number;
    };
  };
  "/api/thread": {
    account: string;
    count: number;
    items: {
      answered: boolean;
      flagged: boolean;
      folder: string;
      from_addr: string;
      from_name: string;
      has_attach: boolean;
      is_junk: boolean;
      key: number;
      pk: number;
      seen: boolean;
      snippet: string;
      subject: string;
      when: string;
    }[];
    ok: boolean;
    reply_pk: number | null;
    thread_id: string;
  };
  "/api/upcoming": {
    items: {
      account: string;
      all_day: boolean;
      end: string;
      invite: boolean;
      key: number;
      location: string;
      organizer: string;
      pk: number;
      recurrence: string;
      start: string;
      summary: string;
      thread_id: string;
    }[];
    ok: boolean;
  };
  "/api/views": {
    ok: boolean;
    views: {
      builtin: boolean;
      dynamic?: string;
      id: string;
      name: string;
      q: string;
    }[];
  };
}

export interface PostAnswers {
  "/api/accounts/add": {
    account: {
      alias: string | null;
      credential: {
        set: boolean;
        style: string;
      };
      defaults_applied: {
        host: boolean;
        port: boolean;
        smtp_host: boolean;
        smtp_port: boolean;
        smtp_starttls: boolean;
        starttls: boolean;
        trash: boolean;
      };
      disabled: boolean;
      email: string;
      host: string;
      port: number;
      signature: string;
      smtp_host: string;
      smtp_port: number;
      smtp_starttls: boolean;
      starttls: boolean;
      trash: string;
      type: string;
    };
    ok: boolean;
  };
  "/api/accounts/delete": {
    error?: string;
    ok: boolean;
    removed?: string;
  };
  "/api/accounts/edit": {
    account?: {
      alias: string;
      credential: {
        set: boolean;
        style: string;
      };
      defaults_applied: {
        host: boolean;
        port: boolean;
        smtp_host: boolean;
        smtp_port: boolean;
        smtp_starttls: boolean;
        starttls: boolean;
        trash: boolean;
      };
      disabled: boolean;
      email: string;
      host: string;
      port: number;
      signature: string;
      smtp_host: string;
      smtp_port: number;
      smtp_starttls: boolean;
      starttls: boolean;
      trash: string;
      type: string;
    };
    error?: string;
    ok: boolean;
  };
  "/api/accounts/signature-default": {
    ok: boolean;
    signature_default: string;
  };
  "/api/accounts/test": {
    connect?: boolean;
    detail?: string;
    email?: string;
    error?: string;
    ms?: number;
    ok: boolean;
  };
  "/api/accounts/trust-cert": {
    account: string;
    ok: boolean;
  };
  "/api/ai/ask": {
    account: string;
    answer: string;
    cited_pks: number[];
    generated: string;
    model: string;
    ok: boolean;
    question: string;
    sources: {
      account: string;
      date: string;
      from: string;
      pk: number;
      snippet: string;
      subject: string;
    }[];
  };
  "/api/ai/do-confirm": {
    count: number;
    failed: number;
    gone: number;
    ok: boolean;
    queued: number;
    undoable: boolean;
    verb: string;
  };
  "/api/ai/do-plan": {
    cap?: number;
    capped?: boolean;
    count?: number;
    empty?: boolean;
    filter: string;
    intent: string;
    ok: boolean;
    plan?: string;
    sample?: {
      from: string;
      subject: string;
      when: string;
    }[];
    snooze_preset?: string;
    verb?: string;
  };
  "/api/ai/draft-reply": {
    body: string;
    model: string;
    ok: boolean;
    tone: string;
  };
  "/api/ai/rewrite": {
    ok: boolean;
    op: string;
    text: string;
  };
  "/api/ai/summarize": {
    account: string;
    action: string;
    cached: boolean;
    created_at: string;
    message_count: number;
    model: string;
    ok: boolean;
    participants: string[];
    points: string[];
    scope: string;
    stale: boolean;
    thread_id: string;
    tldr: string;
  };
  "/api/archive": {
    msg: string;
    ok: boolean;
    status?: string;
    undoable: boolean;
  };
  "/api/briefing": {
    cached: boolean;
    generated: string;
    groups: {
      count: number;
      label: string;
      pks: number[];
    }[];
    model: string;
    narrative: string;
    ok: boolean;
    stale: boolean;
  };
  "/api/briefing/refresh": {
    cached: boolean;
    generated: string;
    groups: {
      count: number;
      label: string;
      pks: number[];
    }[];
    model: string;
    narrative: string;
    ok: boolean;
    stale: boolean;
  };
  "/api/briefing/since": {
    cached?: boolean;
    count?: number;
    empty?: boolean;
    headline?: string;
    items?: {
      gist: string;
      importance: number;
      who: string;
    }[];
    ok: boolean;
  };
  "/api/cleanup/execute": {
    block_scope: string;
    blocked_token: string;
    browser_url: string | null;
    http_status: number | null;
    method: string;
    ok: boolean;
    reversible: {
      block: boolean;
      sweep: boolean;
      unsubscribe: boolean;
    };
    status: string;
    sweep_capped: boolean;
    sweep_failed: number;
    sweep_queued: number;
    swept: number;
    undo_group: string;
  };
  "/api/contacts/known": {
    bad: string[];
    items: Record<string, {
      known: boolean;
      self: boolean;
    }>;
    ok: boolean;
    thin: boolean;
  };
  "/api/draft": {
    deleted?: boolean;
    draft_id?: number | null;
    empty?: boolean;
    files?: {
      b64: string;
      content_type: string;
      filename: string;
      size: number;
    }[];
    message_id?: string;
    ok: boolean;
    resume?: {
      in_reply_to: string;
      references: string;
      tail_text: string;
    };
    uid?: number | null;
  };
  "/api/draft/resume": {
    account: string;
    bcc: unknown[];
    cc: unknown[];
    draft_id: number;
    files: {
      b64: string;
      content_type: string;
      filename: string;
      size: number;
    }[];
    in_reply_to: string;
    mode: string;
    notes: {
      bcc_dropped: number;
      inline: number;
      plain: boolean;
    };
    ok: boolean;
    orig_pk: number | null;
    participants: string[];
    references: string;
    split: string;
    subject: string;
    tail_text: string;
    text: string;
    to: string[];
  };
  "/api/flag": {
    msg: string;
    ok: boolean;
    status?: string;
    undoable?: boolean;
  };
  "/api/followups/dismiss": {
    ok: boolean;
    thread_id: string;
  };
  "/api/invite/rsvp": {
    decision: string;
    ok: boolean;
    organizer: string;
    outbox_id: number;
    queued: boolean;
    summary: string;
  };
  "/api/junk": {
    msg: string;
    ok: boolean;
    status?: string;
    undoable: boolean;
  };
  "/api/move": {
    msg: string;
    ok: boolean;
    status?: string;
    undoable: boolean;
  };
  "/api/mute": {
    ok: boolean;
    thread_id: string;
  };
  "/api/outbox/cancel": {
    ok: boolean;
  };
  "/api/outbox/retry": {
    ok: boolean;
  };
  "/api/outbox/sendnow": {
    ok: boolean;
  };
  "/api/pending/discard": {
    ok: boolean;
  };
  "/api/pending/retry": {
    ok: boolean;
  };
  "/api/reconcile": {
    dry_run: boolean;
    generated: string;
    ok: boolean;
    results: ({
      account: string;
      error: string | null;
      folders: number;
      ghosts: number;
      live: number;
      pruned: number;
      stored: number;
    })[];
    total_ghosts: number;
    total_pruned: number;
  };
  "/api/refresh": {
    msg: string;
    ok: boolean;
  };
  "/api/replylater": {
    ok: boolean;
    on: boolean;
    thread_id: string;
  };
  "/api/review": {
    cached: boolean;
    generated: string;
    model: string;
    narrative: string;
    needs: {
      account: string;
      action: string;
      folder: string;
      importance: number;
      key: number;
      pk: number;
      subject: string;
      thread_id: string;
      who: string;
    }[];
    next: string[];
    ok: boolean;
    stale: boolean;
    stats: {
      by_bucket: Record<string, number>;
      cleaned: number;
      needs_you: number;
      received: number;
      reply_later: number;
      sent: number;
      waiting_on: number;
    };
    waiting: ({
      account: string;
      folder: string | null;
      key: number | null;
      pk: number | null;
      sent_ts: number;
      subject: string;
      thread_id: string;
      to: string;
    })[];
    week_start: string;
  };
  "/api/review/refresh": {
    cached: boolean;
    generated: string;
    model: string;
    narrative: string;
    needs: {
      account: string;
      action: string;
      folder: string;
      importance: number;
      key: number;
      pk: number;
      subject: string;
      thread_id: string;
      who: string;
    }[];
    next: string[];
    ok: boolean;
    stale: boolean;
    stats: {
      by_bucket: Record<string, number>;
      cleaned: number;
      needs_you: number;
      received: number;
      reply_later: number;
      sent: number;
      waiting_on: number;
    };
    waiting: unknown[];
    week_start: string;
  };
  "/api/rules": {
    msg?: string;
    ok: boolean;
    placed_in?: string | null;
    reclassified?: number;
    rules?: {
      block_domains: string[];
      block_senders: string[];
      expire_hours: Record<string, never>;
      keep_domains: unknown[];
      keep_senders: string[];
    };
    token?: string;
    trashed?: boolean;
  };
  "/api/seen": {
    msg: string;
    ok: boolean;
    status?: string;
    undoable: boolean;
  };
  "/api/send": {
    ok: boolean;
    outbox_id: number;
    queued: boolean;
    recipients: number;
    scheduled: boolean;
  };
  "/api/snooze": {
    count: number;
    ok: boolean;
    until: number;
  };
  "/api/sweep": {
    dry_run: boolean;
    generated: string;
    ok: boolean;
    results: {
      account: string;
      by_bucket: Record<string, number>;
      error: null;
      moved: number;
      planned: number;
      senders: number;
      stragglers: unknown[];
    }[];
    total_moved: number;
    total_planned: number;
  };
  "/api/sync-retry-account": {
    account: string;
    ok: boolean;
  };
  "/api/thread/archive": {
    count?: number;
    failed?: number;
    msg: string;
    ok: boolean;
    pks?: number[];
    queued?: number;
    undoable?: boolean;
  };
  "/api/thread/trash": {
    count: number;
    failed: number;
    msg: string;
    ok: boolean;
    pks: number[];
    queued: number;
    undoable: boolean;
  };
  "/api/trash": {
    msg: string;
    ok: boolean;
    status?: string;
    undoable: boolean;
  };
  "/api/undo": {
    account?: string | null;
    empty?: boolean;
    error?: string;
    failed?: number;
    folder?: string | null;
    label?: string;
    ok: boolean;
    remaining?: number;
    reversed?: number;
  };
  "/api/unmute": {
    ok: boolean;
    thread_id: string;
  };
  "/api/unsnooze": {
    count: number;
    ok: boolean;
  };
  "/api/views": {
    msg?: string;
    ok: boolean;
    views: {
      builtin: boolean;
      dynamic?: string;
      id: string;
      name: string;
      q: string;
    }[];
  };
}
