#!/usr/bin/env bash
# End-to-end pipeline check. Phase 1 (M1.7): one request reaches both services' logs.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

base_url="${BASE_URL:-http://localhost:8000}"
wait_seconds=15

fail() { printf 'smoke: FAIL — %s\n' "$*" >&2; exit 1; }
random_hex() { od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'; }

trace_id="$(random_hex 16)"          # 32 hex chars
span_id="$(random_hex 8)"            # 16 hex chars
traceparent="00-${trace_id}-${span_id}-01"

status="$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "traceparent: ${traceparent}" "${base_url}/orders")" \
  || fail "phase 1: GET ${base_url}/orders did not answer"
[[ "${status}" == "200" ]] \
  || fail "phase 1: GET ${base_url}/orders returned ${status}, expected 200"

gateway_seen=false
orders_seen=false
deadline=$((SECONDS + wait_seconds))
while (( SECONDS < deadline )); do
  if [[ "${gateway_seen}" != true ]] \
    && docker compose logs gateway 2>/dev/null | grep -q -- "${trace_id}"; then
    gateway_seen=true
  fi
  if [[ "${orders_seen}" != true ]] \
    && docker compose logs orders 2>/dev/null | grep -q -- "${trace_id}"; then
    orders_seen=true
  fi
  [[ "${gateway_seen}" == true && "${orders_seen}" == true ]] && break
  sleep 1
done

missing=()
[[ "${gateway_seen}" == true ]] || missing+=("gateway")
[[ "${orders_seen}" == true ]] || missing+=("orders")
(( ${#missing[@]} == 0 )) \
  || fail "phase 1: trace ${trace_id} missing from logs of: ${missing[*]}"

printf 'smoke: PASS — phase 1 (trace %s in gateway and orders logs)\n' "${trace_id}"
