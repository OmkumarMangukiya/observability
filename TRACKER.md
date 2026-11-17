# Tracker — last updated 2026-11-16

## Phase status
| Phase | Name | Status |
|---|---|---|
| 0 | Concepts | ✅ Done |
| 1 | Repo skeleton + CLAUDE.md | ✅ Done |
| 2 | Milestone 1 — one request fully visible | ✅ Done (M1.1–M1.11) |
| 3 | Expand and break things | 🔄 In progress (M3.1–M3.9) |
| 4 | Detection and localization by hand | ⬜ |
| 5 | Agent tools | ⬜ |
| 6 | Raw agent loop | ⬜ |
| 7 | Evals | ⬜ |
| Stretch | Kubernetes, PagerDuty, auto-remediation | ⬜ |

## Phase 3 — Expand and break things, split into small steps

Phase 3 adds the third service (payments), a fault-injection framework, multiple failure scenarios, and alerting. Each step is independently verifiable and should land as one reviewable change set.

**Definition of done (whole phase):**
- [ ] `docker compose up -d --build` brings up gateway, orders, payments, and all infra
- [ ] `curl localhost:8000/orders` triggers gateway → orders → payments chain; the same `trace_id` appears in all three services' JSON logs
- [ ] Prometheus has `http_server_requests_total` and `http_server_request_duration_seconds_bucket` for all three services
- [ ] Tempo shows traces with gateway → orders → payments spans
- [ ] Each fault scenario is activatable via env var / HTTP toggle and produces a visible signal in metrics, logs, and/or traces
- [ ] Alertmanager fires alerts to a webhook receiver when fault scenarios are active
- [ ] Grafana dashboard updated to include the payments service
- [ ] `scripts/smoke.sh` passes with all three services; fault-injection smoke tests pass
- [ ] `pytest` and `ruff check` pass

**Out of scope (do not build in Phase 3):** manual investigation playbooks (Phase 4), agent tools (Phase 5), the agent loop (Phase 6), evals (Phase 7), Kubernetes, PagerDuty integration.

**Step index:**
| Step | Scope | Status |
|---|---|---|
| M3.1 | Payments service | ✅ |
| M3.2 | Wire payments into gateway + compose | ✅ |
| M3.3 | Fault-injection framework | ✅ |
| M3.4 | Fault: slow endpoint | ✅ |
| M3.5 | Fault: random 500s | ✅ |
| M3.6 | Fault: dependency timeout | ✅ |
| M3.7 | Fault: bad deploy / memory leak | ✅ |
| M3.8 | Alert rules + Alertmanager | ✅ |
| M3.9 | Smoke, docs, acceptance | ⬜ |

### M3.1 — Payments service ✅
- **Build:** `services/payments/main.py` — in-memory payment records; `POST /payments/process` (accepts `order_id`, `amount`; returns payment confirmation with random processing time 50–200 ms), `GET /payments/{id}` (404 for unknown), `GET /healthz`; `init_observability(app, "payments")`. Reuses `services/common/` telemetry, logging, middleware.
- **Tests:** `tests/test_payments.py` — process 200, unknown id 404, healthz 200, metrics carry `service_name="payments"`.
- **Done when:** tests pass; `ruff check` clean.

### M3.2 — Wire payments into gateway + compose ✅
- **Build:** Orders service calls payments when creating/listing orders (e.g., each order triggers a payment processing call); gateway routes propagate trace context through the full chain. Compose entry for payments (shared build, port 8002, `OTEL_SERVICE_NAME=payments`). Update `scripts/smoke.sh` to verify the three-service trace.
- **Tests:** `tests/test_gateway.py` updated for the three-service flow; `tests/test_orders.py` updated for the payments dependency.
- **Done when:** `curl localhost:8000/orders` produces a trace with gateway → orders → payments spans; `scripts/smoke.sh` passes; Grafana dashboard shows payments in the `$service` variable.

### M3.3 — Fault-injection framework ✅
- **Build:** `services/common/faults.py` — a lightweight fault-injection middleware/module. Faults are toggled via environment variables (e.g., `FAULT_SLOW_ENDPOINT=true`, `FAULT_RANDOM_500S=0.3`) and/or an HTTP control endpoint `POST /faults` (JSON body `{"fault": "slow_endpoint", "enabled": true, "params": {...}}`), `GET /faults` (list active faults). The framework is imported and mounted by each service but does nothing unless a fault is activated. Each fault type is a pluggable function.
- **Tests:** `tests/test_faults.py` — framework loads with no faults active; faults can be toggled on/off via env and HTTP; unknown fault names return 400.
- **Done when:** tests pass; services start cleanly with no faults enabled; `GET /faults` returns empty list.

### M3.4 — Fault: slow endpoint ✅
- **Build:** When `FAULT_SLOW_ENDPOINT=true` (or toggled via HTTP), the payments `POST /payments/process` adds an artificial delay (configurable, default 2–5 s). This should cause p95/p99 latency to spike on the payments service and the gateway's upstream latency to increase.
- **Tests:** `tests/test_fault_slow.py` — with fault active, response time exceeds the configured delay; with fault inactive, response time is under 500 ms; the latency histogram records the inflated duration.
- **Done when:** tests pass; manually verifiable: activate fault, hit endpoint, see latency spike in Prometheus `http_server_request_duration_seconds_bucket`.

### M3.5 — Fault: random 500s ✅
- **Build:** When `FAULT_RANDOM_500S=<rate>` (float 0–1, e.g., `0.3` for 30% errors), the payments service randomly returns HTTP 500 with a JSON error body and a structured `fault_injected` log line. The error counter `http_server_requests_total{http_response_status_code="500"}` increases.
- **Tests:** `tests/test_fault_500s.py` — with rate=1.0, every request is 500; with rate=0.0, no 500s; error responses include the fault marker in logs.
- **Done when:** tests pass; the 5xx error rate panel in Grafana visibly increases when the fault is on.

### M3.6 — Fault: dependency timeout ✅
- **Build:** When `FAULT_DEPENDENCY_TIMEOUT=true`, orders' call to payments hangs (payments delays indefinitely or past orders' client timeout). Orders should log `payments_call_failed` (same `{dependency}_call_failed` convention as gateway's `orders_call_failed`) and return 504 or 502. The trace should show the orders span waiting and eventually timing out.
- **Tests:** `tests/test_fault_timeout.py` — with fault active, orders returns 502/504 within a reasonable client timeout; the error is logged with structured fields; the trace shows the timeout span.
- **Done when:** tests pass; the trace in Tempo shows the timeout behavior.

### M3.7 — Fault: bad deploy / memory leak ✅
- **Build:** Two faults:
  1. **Bad deploy:** When `FAULT_BAD_DEPLOY=true`, payments returns 500 for *all* requests (simulating a completely broken deploy). A deploy marker (structured log with `event=deploy`, `git_sha=<fake>`, `service=payments`) is emitted at startup so the agent can later correlate.
  2. **Memory leak:** When `FAULT_MEMORY_LEAK=true`, payments allocates memory on each request (e.g., appending to a growing list). Expose a `process_resident_memory_bytes` metric (or use the default process metrics from `prometheus_client`) so the leak is visible.
- **Tests:** `tests/test_fault_deploy.py` — bad deploy: all requests 500, deploy marker present in logs. `tests/test_fault_memory.py` — memory metric increases after N requests.
- **Done when:** tests pass; both faults produce distinct, observable signals.

### M3.8 — Alert rules + Alertmanager ✅
- **Build:** `infra/prometheus/alert-rules.yml` — alert rules:
  1. `HighErrorRate`: `rate(http_server_requests_total{http_response_status_code=~"5.."}[5m]) / rate(http_server_requests_total[5m]) > 0.05` for 1m.
  2. `HighLatencyP99`: `histogram_quantile(0.99, sum(rate(http_server_request_duration_seconds_bucket[5m])) by (le, service_name)) > 2` for 1m.
  3. `ServiceDown`: `up == 0` for 30s.
  - `infra/alertmanager/alertmanager.yml` — routes all alerts to a webhook receiver (`http://webhook-receiver:9000/webhook`). A minimal webhook receiver container (or a simple Python script in `services/webhook-receiver/`) that logs received alerts as structured JSON.
  - Compose entries for Alertmanager and the webhook receiver; Prometheus config updated to point to Alertmanager.
  - Grafana datasource for Alertmanager (UID `alertmanager`).
- **Tests:** extend `tests/test_infra_configs.py` — alert rules YAML parses; Alertmanager config parses; receiver endpoint is configured; Prometheus config references the rules file and Alertmanager.
- **Done when:** with a fault active, Prometheus shows the alert in PENDING → FIRING; Alertmanager routes it to the webhook receiver; the receiver logs the alert payload.

### M3.9 — Smoke, docs, acceptance ⬜
- **Build:**
  - `scripts/smoke.sh` updated: add phase 4 — verify three-service trace (gateway + orders + payments spans). Optionally add a fault-injection smoke phase that toggles a fault and checks the alert fires.
  - Dashboard `milestone-1.json` updated (or new `phase-3.json`): `$service` variable now includes `payments`; panels still work for all three services.
  - `README.md` updated with Phase 3 info: new service, fault injection usage, alerting.
  - `CLAUDE.md` updated: repo layout adds `payments/`, `faults/`, `webhook-receiver/`; notes on fault-injection conventions.
- **Tests:** extend `tests/test_infra_configs.py` for any new configs.
- **Verified:** all containers up; three-service traces in Tempo; each fault scenario produces its expected signal; alerts fire and reach the webhook receiver; `scripts/smoke.sh` all phases pass; `pytest -q` passes; `ruff check` clean.
- **Done when:** every Phase 3 definition-of-done box at the top is ticked.

## Working rules for these steps
- One step per change set; tests first within each step.
- Verify a step before starting the next (`pytest`, `ruff check`; Docker from M1.7 on; `scripts/smoke.sh` once it exists).
- `ruff format` only new/touched files; do not churn `agent/loop.py`.
- Use only the pinned dependency lists; ask before adding anything else.
- No commits unless explicitly asked.
- Tick the step and its index row when it lands.

## Done
- [x] Brainstorm + decisions (plan 2)
- [x] Phase 1: repo skeleton + CLAUDE.md + initial folder layout
- [x] Phase 0: observability concepts
- [x] Phase 2: Milestone 1 — one request fully visible (M1.1–M1.11)

## In flight / Next
- [x] **M3.1 — Payments service**
- [x] **M3.2 — Wire payments into gateway + compose**
- [x] **M3.3 — Fault-injection framework**
- [x] **M3.4 — Fault: slow endpoint**
- [x] **M3.5 — Fault: random 500s**
- [x] **M3.6 — Fault: dependency timeout**
- [x] **M3.7 — Fault: bad deploy / memory leak**
- [x] **M3.8 — Alert rules + Alertmanager**
- [ ] **M3.9 — Smoke, docs, acceptance**
- **Next:** Phase 4 — detection and localization by hand.

## Open questions / blockers
- None blocking the plan itself.

## Notes
- This is a simple markdown tracker — a single source of truth for *build state*.
- Keep it updated at step boundaries; update `docs/observability-agent-plan.md` when a decision changes.
- Security: `.claude/settings.local.json` appears to contain an OpenRouter key. It is gitignored and untouched here, but rotate it if that file has ever been shared or committed anywhere.
