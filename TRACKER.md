# Tracker — last updated 2026-11-18

## Phase status
| Phase | Name | Status |
|---|---|---|
| 0 | Concepts | ✅ Done |
| 1 | Repo skeleton + CLAUDE.md | ✅ Done |
| 2 | Milestone 1 — one request fully visible | ✅ Done (M1.1–M1.11) |
| 3 | Expand and break things | ✅ Done (M3.1–M3.9) |
| 4 | Detection and localization by hand | 🔶 In progress (M4.1 done) |
| 5 | Agent tools | ⬜ |
| 6 | Raw agent loop | ⬜ |
| 7 | Evals | ⬜ |
| Stretch | Kubernetes, PagerDuty, auto-remediation | ⬜ |

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
| M4.1 | Drill runner + scenario catalog | ✅ |
| M4.2 | Investigation method + error drills (`random_500s`, `bad_deploy`) | ⬜ |
| M4.3 | Latency drills (`slow_endpoint`, `dependency_timeout`) | ⬜ |
| M4.4 | Silent and availability drills (`memory_leak`, `ServiceDown`) | ⬜ |
| M4.5 | Agent contract, gap list, results table | ⬜ |

### M4.1 — Drill runner + scenario catalog ✅
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
- [x] M4.1: Drill runner + scenario catalog

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
- [x] **M4.1 — Drill runner + scenario catalog**
- [ ] **M4.2 — Investigation method + error drills**
- [ ] **M4.3 — Latency drills**
- [ ] **M4.4 — Silent and availability drills**
- [ ] **M4.5 — Agent contract, gaps, results**
- **Next:** M4.2 — Investigation method + error drills.

## Open questions / blockers
- None blocking the plan itself.

## Notes
- This is a simple markdown tracker — a single source of truth for *build state*.
- Keep it updated at step boundaries; update `docs/observability-agent-plan.md` when a decision changes.
- Security: `.claude/settings.local.json` appears to contain an OpenRouter key. It is gitignored and untouched here, but rotate it if that file has ever been shared or committed anywhere.
