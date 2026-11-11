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

# --- Phase 2 (M1.9): the same trace must reach Loki for both services. ---
loki_url="${LOKI_URL:-http://localhost:3100}"
loki_timeout=30
logql="{service_name=~\"gateway|orders\"} |= \"${trace_id}\""

loki_gateway=false
loki_orders=false
loki_deadline=$((SECONDS + loki_timeout))
while (( SECONDS < loki_deadline )); do
  response="$(curl -sS -G \
    --data-urlencode "query=${logql}" \
    --data-urlencode "limit=100" \
    "${loki_url}/loki/api/v1/query_range" 2>/dev/null || true)"
  compact="$(printf '%s' "${response}" | tr -d ' \t\n')"

  if [[ "${compact}" == *'"service_name":"gateway"'* ]]; then
    loki_gateway=true
  fi
  if [[ "${compact}" == *'"service_name":"orders"'* ]]; then
    loki_orders=true
  fi
  if [[ "${loki_gateway}" == true && "${loki_orders}" == true ]]; then
    break
  fi
  sleep 1
done

loki_missing=()
[[ "${loki_gateway}" == true ]] || loki_missing+=("gateway")
[[ "${loki_orders}" == true ]] || loki_missing+=("orders")
(( ${#loki_missing[@]} == 0 )) \
  || fail "phase 2: trace ${trace_id} missing from Loki for: ${loki_missing[*]}"

printf 'smoke: PASS — phase 2 (trace %s in Loki for gateway and orders)\n' "${trace_id}"
