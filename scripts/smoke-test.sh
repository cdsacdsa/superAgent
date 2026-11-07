#!/usr/bin/env bash
# End-to-end smoke test against a running SuperAgent (local or Azure, mock ITSM).
# Usage: ./scripts/smoke-test.sh [base-url] [console-api-key]
set -euo pipefail
BASE="${1:-http://localhost:8000}"
KEY="${2:-${SA_CONSOLE_API_KEY:-}}"
H=(-H "Content-Type: application/json")
[ -n "$KEY" ] && H+=(-H "X-API-Key: $KEY")

echo ">> health"; curl -fsS "$BASE/healthz"; echo
echo ">> creating test ticket"
TID=$(curl -fsS "${H[@]}" -X POST "$BASE/api/dev/tickets" -d '{
  "title": "Outlook keeps asking for my password since yesterday",
  "description": "After my password change yesterday Outlook desktop prompts for credentials every few minutes. Webmail works fine.",
  "category": "email", "impact": 3, "urgency": 2, "requester": "Smoke Test"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['ticket_id'])")
echo "ticket: $TID"
for i in $(seq 1 60); do
  ST=$(curl -fsS "${H[@]}" "$BASE/api/cases/$TID" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['status'], '|', '->'.join(d['tiers_used']), '| $%.4f' % sum(u['cost_usd'] for u in d['usage'].values()))")
  echo "[$i] $ST"
  case "$ST" in resolved*|handed_over*|proposed*|waiting_human*|waiting_requester*) break;; esac
  sleep 5
done
echo ">> timeline"
curl -fsS "${H[@]}" "$BASE/api/cases/$TID/events" | python3 -c "import sys,json;[print(e['at'][11:19], e['type'], '-', e['message'][:140]) for e in json.load(sys.stdin)]"
echo ">> metrics"; curl -fsS "${H[@]}" "$BASE/api/metrics"; echo
