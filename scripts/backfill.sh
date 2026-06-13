#!/usr/bin/env bash
# Resilient summarization backfill: keeps calling summarize.py until every
# non-junk message has a summary, backing off when the Claude CLI rate-limits.
# Run from anywhere: scripts/backfill.sh   (optionally wrap with `caffeinate -i`)
set -euo pipefail
cd "$(dirname "$0")/.."

pend() { python3 -c "import store;c=store.conn();print(c.execute('SELECT COUNT(*) FROM messages m LEFT JOIN summaries s ON s.message_pk=m.pk WHERE m.is_junk=0 AND s.message_pk IS NULL').fetchone()[0])"; }

stall=0
for round in $(seq 1 60); do
  p=$(pend)
  echo "[round $round] pending=$p  $(date +%H:%M:%S)"
  if [ "$p" -eq 0 ]; then echo "ALL DONE $(date)"; break; fi
  python3 summarize.py 2000 3
  q=$(pend)
  if [ "$q" -ge "$p" ]; then
    stall=$((stall+1)); echo "  no progress (stall $stall) — backing off"; sleep $((120 * stall))
  else
    stall=0; sleep 30
  fi
done
echo "backfill exited $(date) final pending=$(pend)"
