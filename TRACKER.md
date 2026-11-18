# Tracker — last updated 2026-11-18

## Phase status
| Phase | Name | Status |
|---|---|---|
| 0 | Concepts | ✅ Done |
| 1 | Repo skeleton + CLAUDE.md | ✅ Done |
| 2 | Milestone 1 — one request fully visible | ✅ Done (M1.1–M1.11) |
| 3 | Expand and break things | ✅ Done (M3.1–M3.9) |
| 4 | Detection and localization by hand | ⬜ (planned: M4.1–M4.5) |
| 5 | Agent tools | ⬜ |
| 6 | Raw agent loop | ⬜ |
| 7 | Evals | ⬜ |
| Stretch | Kubernetes, PagerDuty, auto-remediation | ⬜ |

## Phase 3 — Expand and break things, split into small steps

Phase 3 adds the third service (payments), a fault-injection framework, multiple failure scenarios, and alerting. Each step is independently verifiable and should land as one reviewable change set.

**Definition of done (whole phase):**
- [x] `docker compose up -d --build` brings up gateway, orders, payments, and all infra
- [x] `curl localhost:8000/orders` triggers gateway → orders → payments chain; the same `trace_id` appears in all three services' JSON logs
- [x] Prometheus has `http_server_requests_total` and `http_server_request_duration_seconds_bucket` for all three services
- [x] Tempo shows traces with gateway → orders → payments spans
- [x] Each fault scenario is activatable via env var / HTTP toggle and produces a visible signal in metrics, logs, and/or traces
- [x] Alertmanager fires alerts to a webhook receiver when fault scenarios are active
- [x] Grafana dashboard updated to include the payments service
- [x] `scripts/smoke.sh` passes with all three services; fault-injection smoke tests pass
- [x] `pytest` and `ruff check` pass

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
| M3.9 | Smoke, docs, acceptance | ✅ |

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

### M3.9 — Smoke, docs, acceptance ✅
- **Build:**
  - `scripts/smoke.sh` updated: add phase 4 — verify three-service trace (gateway + orders + payments spans). Optionally add a fault-injection smoke phase that toggles a fault and checks the alert fires.
  - Dashboard `milestone-1.json` updated (or new `phase-3.json`): `$service` variable now includes `payments`; panels still work for all three services.
  - `README.md` updated with Phase 3 info: new service, fault injection usage, alerting.
  - `CLAUDE.md` updated: repo layout adds `payments/`, `faults/`, `webhook-receiver/`; notes on fault-injection conventions.
- **Tests:** extend `tests/test_infra_configs.py` for any new configs.
- **Verified:** all containers up; three-service traces in Tempo; each fault scenario produces its expected signal; alerts fire and reach the webhook receiver; `scripts/smoke.sh` all phases pass; `pytest -q` passes; `ruff check` clean.
- **Done when:** every Phase 3 definition-of-done box at the top is ticked.
- **Landed:** smoke phase 4 (random_500s → `HighErrorRate` → webhook receiver → recovery), error-ratio dashboard panel, README/CLAUDE docs, `SKIP_FAULT_SMOKE=1` escape hatch. Phase 4 waits out a stale alert; `smoke.sh` is executable in git.
- **Found and fixed during acceptance:**
  - Stale dashboard test expected a `traces` panel; commit `78b6152` intentionally uses a table for TraceQL search results.
  - `HighErrorRate` divided each 5xx series by itself (ratio always 1.0). Both sides now aggregate `by (service_name)`, so it measures the real 5% error share per service.
  - Smoke's Loki wait raised to 60s: on a cold stack, gateway's first logs can take >30s to reach Loki.
  - Phase 4 waits for an earlier `HighErrorRate` to clear first: Alertmanager's `repeat_interval` is 1h and would not resend.
  - `scripts/smoke.sh` was tracked as non-executable, so `scripts/smoke.sh` failed with "Permission denied".

## Phase 4 — Detection and localization by hand, split into small steps

Phase 4 builds no new features. You take each fault from Phase 3, investigate it manually (Grafana + the Prometheus/Loki/Tempo HTTP APIs, no helper code), and write down the queries and decision rules you used. That written record becomes the specification for the agent's tools in Phase 5. Each practice step pairs two faults that look similar but must be told apart — that discrimination is the skill the agent needs.

**Definition of done (whole phase):**
- [ ] Every HTTP-toggle fault scenario runs end-to-end with `python -m faults.run run <scenario>`
- [ ] Each fault scenario has a completed worksheet with observed evidence (the query and its result), not just "done"
- [ ] `ServiceDown` drilled manually (`docker compose stop payments`) and documented
- [ ] Every query in `docs/investigation/queries.md` was executed against the live stack
- [ ] `docs/investigation/agent-requirements.md` maps every scenario to a decision rule and a tool contract
- [ ] `pytest -q` passes; `ruff check .` clean

**Out of scope (do not build in Phase 4):** agent tools (Phase 5), the agent loop (Phase 6), evals (Phase 7), new services or faults, auto-remediation, Kubernetes, PagerDuty.

**Step index:**
| Step | Scope | Status |
|---|---|---|
| M4.1 | Drill runner + scenario catalog | ⬜ |
| M4.2 | Investigation method + error drills (`random_500s`, `bad_deploy`) | ⬜ |
| M4.3 | Latency drills (`slow_endpoint`, `dependency_timeout`) | ⬜ |
| M4.4 | Silent and availability drills (`memory_leak`, `ServiceDown`) | ⬜ |
| M4.5 | Agent contract, gap list, results table | ⬜ |

### M4.1 — Drill runner + scenario catalog ⬜
- **Build:** `faults/__init__.py`, `faults/catalog.json` (one entry per drill: id, service, fault, params, traffic to drive, expected symptom), `faults/run.py` CLI with `list`, `enable`, `disable`, `run`. `run` enables a fault, drives traffic, optionally waits for the alert, then disables — or keeps it on with `--no-disable` so you can investigate in Grafana. Use JSON, not YAML: stdlib only, and PyYAML is dev-only.
- **Scope guard:** the runner is scenario control and traffic only. It does not query metrics, logs or traces — investigation stays manual, which is the point of the phase.
- **Tests:** `tests/test_fault_catalog.py` — catalog is valid; every entry names a fault actually registered by the target service; enable/disable reach `/faults` and are idempotent. Add `"."` to pytest `pythonpath` in `pyproject.toml` so tests can import `faults`.
- **Done when:** `python -m faults.run list` shows every scenario; enable/disable verified against the live service's `GET /faults`; tests pass; `ruff check .` clean.

### M4.2 — Investigation method + error drills ⬜
- **Build:** `docs/investigation/README.md` (the fixed order: alert → scope → service → route → trace → logs → deploy), `docs/investigation/queries.md` (concrete PromQL/LogQL/TraceQL, each with what it tells you), `docs/investigation/playbook-template.md`, and `docs/investigation/playbooks/errors.md` filled in from running `random_500s` (`rate=0.3`) and `bad_deploy`.
- **The lesson:** partial errors on one route (`http_route="/payments/process"`) vs every request failing; and the `deploy` log marker in Loki that correlates the bad deploy.
- **Verified:** both worksheets hold real observed evidence, and re-running each drill using only the doc reproduces the same conclusion.

### M4.3 — Latency drills ⬜
- **Build:** `docs/investigation/playbooks/latency.md` and worksheets for `slow_endpoint` (fixed 3 s delay) and `dependency_timeout`. Extend `queries.md` with per-service p99 and a TraceQL duration search.
- **The lesson:** the guilty service is the span whose self-time dominates. For `slow_endpoint`, payments is slow itself; for `dependency_timeout`, orders waits 10 s on payments, logs `payments_call_failed`, and gateway answers 504. Tell "slow itself" apart from "slow dependency" by comparing a span's duration with its children's.
- **Verified:** the written rule correctly classifies both scenarios, backed by traces actually pulled during the drills.

### M4.4 — Silent and availability drills ⬜
- **Build:** `docs/investigation/playbooks/resources.md`, `docs/investigation/playbooks/availability.md`, and one worksheet each.
- **The lesson:** `memory_leak` raises no alert — find it from `process_resident_memory_bytes{service_name="payments"}` climbing. `ServiceDown` is a manual `docker compose stop payments`, then `up == 0` and 502s at the gateway.
- **Verified:** both faults found and localized; the gaps (no memory alert, no memory panel) recorded for M4.5.

### M4.5 — Agent contract, gaps, results ⬜
- **Build:** `docs/investigation/agent-requirements.md` — for each scenario: symptom, first discriminating query, decision rule, tool needed. Refine the section-8 tool signatures, write the investigation order and stop conditions, and add a results table (scenario, detection source, time-to-localize, gotchas) plus a gap list (memory alert/panel, deploy history, alert payload fields). Update `docs/observability-agent-plan.md` if a decision changes.
- **Done when:** Phase 5 tools could be implemented from that doc alone, without redoing the drills.

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
- [x] Phase 3: Expand and break things (M3.1–M3.9)

## In flight / Next
- [x] **M3.1 — Payments service**
- [x] **M3.2 — Wire payments into gateway + compose**
- [x] **M3.3 — Fault-injection framework**
- [x] **M3.4 — Fault: slow endpoint**
- [x] **M3.5 — Fault: random 500s**
- [x] **M3.6 — Fault: dependency timeout**
- [x] **M3.7 — Fault: bad deploy / memory leak**
- [x] **M3.8 — Alert rules + Alertmanager**
- [x] **M3.9 — Smoke, docs, acceptance**
- [ ] **M4.1 — Drill runner + scenario catalog**
- [ ] **M4.2 — Investigation method + error drills**
- [ ] **M4.3 — Latency drills**
- [ ] **M4.4 — Silent and availability drills**
- [ ] **M4.5 — Agent contract, gaps, results**
- **Next:** M4.1 — Drill runner + scenario catalog.

## Open questions / blockers
- None blocking the plan itself.

## Notes
- This is a simple markdown tracker — a single source of truth for *build state*.
- Keep it updated at step boundaries; update `docs/observability-agent-plan.md` when a decision changes.
- Security: `.claude/settings.local.json` appears to contain an OpenRouter key. It is gitignored and untouched here, but rotate it if that file has ever been shared or committed anywhere.
