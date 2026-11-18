#!/usr/bin/env bash
# End-to-end pipeline check: one request's trace reaches all three services' logs
# (phase 1), Loki (phase 2), and Tempo (phase 3). Phase 4 injects a fault, waits
# for the alert to fire, and checks that the webhook receiver got it.
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
payments_seen=false
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
  if [[ "${payments_seen}" != true ]] \
    && docker compose logs payments 2>/dev/null | grep -q -- "${trace_id}"; then
    payments_seen=true
  fi
  [[ "${gateway_seen}" == true && "${orders_seen}" == true && "${payments_seen}" == true ]] \
    && break
  sleep 1
done

missing=()
[[ "${gateway_seen}" == true ]] || missing+=("gateway")
[[ "${orders_seen}" == true ]] || missing+=("orders")
[[ "${payments_seen}" == true ]] || missing+=("payments")
(( ${#missing[@]} == 0 )) \
  || fail "phase 1: trace ${trace_id} missing from logs of: ${missing[*]}"

printf 'smoke: PASS — phase 1 (trace %s in gateway, orders and payments logs)\n' "${trace_id}"

# --- Phase 2 (M1.9): the same trace must reach Loki for all three services. ---
loki_url="${LOKI_URL:-http://localhost:3100}"
loki_timeout=60
logql="{service_name=~\"gateway|orders|payments\"} |= \"${trace_id}\""

loki_gateway=false
loki_orders=false
loki_payments=false
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
  if [[ "${compact}" == *'"service_name":"payments"'* ]]; then
    loki_payments=true
  fi
  if [[ "${loki_gateway}" == true && "${loki_orders}" == true && "${loki_payments}" == true ]]; then
    break
  fi
  sleep 1
done

loki_missing=()
[[ "${loki_gateway}" == true ]] || loki_missing+=("gateway")
[[ "${loki_orders}" == true ]] || loki_missing+=("orders")
[[ "${loki_payments}" == true ]] || loki_missing+=("payments")
(( ${#loki_missing[@]} == 0 )) \
  || fail "phase 2: trace ${trace_id} missing from Loki for: ${loki_missing[*]}"

printf 'smoke: PASS — phase 2 (trace %s in Loki for gateway, orders and payments)\n' "${trace_id}"

# --- Phase 3 (M1.10): the same trace must reach Tempo with spans from all three services. ---
tempo_url="${TEMPO_URL:-http://localhost:3200}"
tempo_timeout=30

tempo_gateway=false
tempo_orders=false
tempo_payments=false
tempo_deadline=$((SECONDS + tempo_timeout))
while (( SECONDS < tempo_deadline )); do
  trace_json="$(curl -sS "${tempo_url}/api/traces/${trace_id}" 2>/dev/null || true)"
  compact="$(printf '%s' "${trace_json}" | tr -d ' \t\n')"

  if [[ "${compact}" == *'"stringValue":"gateway"'* ]]; then
    tempo_gateway=true
  fi
  if [[ "${compact}" == *'"stringValue":"orders"'* ]]; then
    tempo_orders=true
  fi
  if [[ "${compact}" == *'"stringValue":"payments"'* ]]; then
    tempo_payments=true
  fi
  if [[ "${tempo_gateway}" == true && "${tempo_orders}" == true && "${tempo_payments}" == true ]]; then
    break
  fi
  sleep 1
done

tempo_missing=()
[[ "${tempo_gateway}" == true ]] || tempo_missing+=("gateway")
[[ "${tempo_orders}" == true ]] || tempo_missing+=("orders")
[[ "${tempo_payments}" == true ]] || tempo_missing+=("payments")
(( ${#tempo_missing[@]} == 0 )) \
  || fail "phase 3: trace ${trace_id} missing from Tempo for: ${tempo_missing[*]}"

printf 'smoke: PASS — phase 3 (trace %s in Tempo for gateway, orders and payments)\n' "${trace_id}"

# --- Phase 4 (M3.9): a fault raises an alert that reaches the webhook receiver. ---
if [[ "${SKIP_FAULT_SMOKE:-0}" == "1" ]]; then
  printf 'smoke: SKIP — phase 4 (fault smoke disabled by SKIP_FAULT_SMOKE=1)\n'
else
  payments_url="${PAYMENTS_URL:-http://localhost:8002}"
  prometheus_url="${PROMETHEUS_URL:-http://localhost:9090}"
  alert_timeout="${FAULT_ALERT_TIMEOUT_SECONDS:-180}"
  clear_timeout="${FAULT_ALERT_CLEAR_TIMEOUT_SECONDS:-360}"

  high_error_rate_firing() {
    local alerts compact
    alerts="$(curl -sS "${prometheus_url}/api/v1/alerts" 2>/dev/null || true)"
    compact="$(printf '%s' "${alerts}" | tr -d ' \t\n')"
    [[ "${compact}" == *'"alertname":"HighErrorRate"'*'"state":"firing"'* ]]
  }

  # Always turn the fault off, even when a check below fails.
  fault_active=false
  disable_fault() {
    if [[ "${fault_active}" == true ]]; then
      curl -sS -X POST -H 'content-type: application/json' \
        -d '{"fault": "random_500s", "enabled": false}' \
        "${payments_url}/faults" >/dev/null 2>&1 || true
      fault_active=false
    fi
  }
  trap disable_fault EXIT

  # A previous run keeps HighErrorRate firing until its 5-minute rate window
  # empties. Alertmanager would not resend inside repeat_interval, so wait for a
  # clean slate before generating a fresh alert.
  if high_error_rate_firing; then
    printf 'smoke: HighErrorRate is still firing from an earlier run; waiting up to %ss...\n' "${clear_timeout}"
    clear_deadline=$((SECONDS + clear_timeout))
    while (( SECONDS < clear_deadline )) && high_error_rate_firing; do
      sleep 10
    done
    if high_error_rate_firing; then
      fail "phase 4: HighErrorRate still firing after ${clear_timeout}s; rerun later or set SKIP_FAULT_SMOKE=1"
    fi
  fi

  # Remember how much the receiver logged so old alerts cannot pass the check.
  webhook_before="$({ docker compose logs webhook-receiver 2>/dev/null || true; } \
    | wc -l | tr -d ' ')"

  curl -sS -X POST -H 'content-type: application/json' \
    -d '{"fault": "random_500s", "enabled": true, "params": {"rate": 1.0}}' \
    "${payments_url}/faults" >/dev/null \
    || fail "phase 4: could not enable random_500s on payments"
  fault_active=true

  status="$(curl -sS -o /dev/null -w '%{http_code}' "${base_url}/orders" || true)"
  [[ "${status}" == "502" ]] \
    || fail "phase 4: with random_500s on, GET /orders returned ${status}, expected 502"

  fired=false
  deadline=$((SECONDS + alert_timeout))
  while (( SECONDS < deadline )); do
    alerts="$(curl -sS "${prometheus_url}/api/v1/alerts" 2>/dev/null || true)"
    compact="$(printf '%s' "${alerts}" | tr -d ' \t\n')"
    if [[ "${compact}" == *'"alertname":"HighErrorRate"'*'"state":"firing"'* ]]; then
      fired=true
      break
    fi
    # Keep failing traffic flowing so the 5-minute error ratio stays above 5%.
    curl -sS -o /dev/null "${base_url}/orders" 2>/dev/null || true
    sleep 5
  done
  [[ "${fired}" == true ]] \
    || fail "phase 4: HighErrorRate did not reach FIRING within ${alert_timeout}s"

  delivered=false
  deadline=$((SECONDS + 30))
  while (( SECONDS < deadline )); do
    new_logs="$({ docker compose logs webhook-receiver 2>/dev/null || true; } \
      | tail -n +"$((webhook_before + 1))")"
    compact_logs="$(printf '%s' "${new_logs}" | tr -d ' \t\n')"
    if [[ "${compact_logs}" == *alert_received* \
      && "${compact_logs}" == *'"alertname":"HighErrorRate"'* \
      && "${compact_logs}" == *'"status":"firing"'* ]]; then
      delivered=true
      break
    fi
    sleep 2
  done
  [[ "${delivered}" == true ]] \
    || fail "phase 4: webhook-receiver never logged the HighErrorRate alert"

  disable_fault
  status="$(curl -sS -o /dev/null -w '%{http_code}' "${base_url}/orders" || true)"
  [[ "${status}" == "200" ]] \
    || fail "phase 4: after disabling the fault, GET /orders returned ${status}, expected 200"

  printf 'smoke: PASS — phase 4 (random_500s → HighErrorRate → webhook-receiver, then recovery)\n'
fi
