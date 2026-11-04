# Tracker — last updated 2026-11-04

## Phase status
| Phase | Name | Status |
|---|---|---|
| 0 | Concepts | ✅ Done |
| 1 | Repo skeleton + CLAUDE.md | ✅ Done |
| 2 | Milestone 1 — one request fully visible | ⬜ Next |
| 3 | Expand and break things | ⬜ |
| 4 | Detection and localization by hand | ⬜ |
| 5 | Agent tools | ⬜ |
| 6 | Raw agent loop | ⬜ |
| 7 | Evals | ⬜ |
| Stretch | Kubernetes, PagerDuty, auto-remediation | ⬜ |

## Done
- [x] Brainstorm + decisions (plan 2)
- [x] Phase 1: repo skeleton + CLAUDE.md + initial folder layout
- [x] Phase 0: observability concepts

## In flight / Next
- [ ] **Phase 2 — Milestone 1:** one request fully visible in Grafana
  - [ ] One FastAPI service that calls a second, both OpenTelemetry-instrumented
  - [ ] JSON logs containing `trace_id`
  - [ ] Prometheus scraping `/metrics`
  - [ ] Loki receiving logs via Grafana Alloy
  - [ ] Tempo receiving traces
  - [ ] Grafana dashboard: request rate, error rate, p95/p99 latency, trace→logs click-through
  - [ ] Done when: one request to the first service shows metrics, logs and cross-service trace in Grafana

## Open questions / blockers
- None blocking Milestone 1 (per plan 2).

## Notes
- This is a simple markdown tracker — a single source of truth for *build state*.
- Keep it updated at phase boundaries; update `docs/observability-agent-plan.md` when a decision changes.
- If Phase 2 gets too large to track as one item, split into per-ticket files under `.scratch/<feature>/issues/`.
