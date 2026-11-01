# AI Observability Agent: Project Plan and Context

Last updated: 2026-11-01. Read this first in any new chat in this project. It holds the goal, the decisions already made, the architecture, the phased plan and the current status.

## 1. Goal

Build an **AI observability agent**: when something breaks in a microservice system, the agent investigates using metrics, logs and traces, finds the faulty service / endpoint / function, and produces a root cause report with evidence.

There are two learning goals alongside the product goal:

1. Learn to work with AI agents and Claude Code: `CLAUDE.md`, agent config, markdown instruction files, subagents, skills, hooks, MCP.
2. Learn the observability stack and concepts: Prometheus, Grafana, Loki, tracing, histograms, alerting, Kubernetes, PagerDuty.

## 2. Decisions already made

| Topic | Decision |
|---|---|
| Primary language | Python |
| Agent implementation | Raw API loop (no agent framework), to understand how agents work |
| Log store | Loki (not Elasticsearch; lighter, label-indexed, LogQL, tight Grafana integration) |
| Local environment | Docker Compose first; Kubernetes (kind/minikube) only later |
| Dev environment | WSL2 (Linux), chosen because most production systems are Unix-based; Docker used from inside WSL |
| Repo | Folder `observability/` inside the WSL filesystem, tracked with git; moving to native Linux later is just a clone |
| Demo services | New, purpose-built toy services (not reusing Continew) |
| Instrumentation standard | OpenTelemetry (vendor-neutral, so any app can plug in) |
| Agent autonomy | Read-only first; no auto-remediation until much later |

Open questions: none blocking Milestone 1.

## 3. Core idea

The project does not rebuild Prometheus or Grafana. Use standard tools underneath and build the glue and the intelligence:

```
Demo microservices (Python/FastAPI, OpenTelemetry-instrumented)
        | metrics / logs / traces
        v
Prometheus (metrics) + Loki (logs) + Tempo (traces), fed by Grafana Alloy or an OTel Collector
        |
        v
Grafana (dashboards) + Alertmanager (to webhook, later PagerDuty)
        | alert fires
        v
Agent: investigates, localizes the fault, writes a root cause report
```

## 4. Repo layout

```
observability/
  CLAUDE.md            project guide for Claude Code
  README.md
  .gitignore           .env, __pycache__, .venv, data volumes
  .gitattributes       * text=auto eol=lf   (safeguard if files are ever edited from Windows tools)
  .env.example         ANTHROPIC_API_KEY=   (never commit the real .env)
  docker-compose.yml   pin image versions, use relative paths
  infra/               prometheus/, grafana/, loki/, tempo/, alertmanager/
  services/            gateway/, orders/, payments/
  faults/              fault-injection scenarios
  agent/               tools/, loop.py, prompts/
  evals/
  docs/                notes and decisions
  .claude/agents/      subagents, added later
```

WSL notes:
- Keep the repo in the WSL home directory (e.g. `~/observability`), not under `/mnt/c/...`. Cross-filesystem access is slow and causes permission and file-watching problems with Docker bind mounts.
- Docker must work from inside WSL: either Docker Desktop with WSL integration enabled, or Docker Engine installed directly in the WSL distro. Check with `docker compose version` inside WSL.

## 5. Observability concepts to understand

- **Three signals:** metrics say something is wrong, logs say what happened, traces say where.
- **RED** (rate, errors, duration) for services; **USE** (utilization, saturation, errors) for resources.
- **Histograms and percentiles:** why p95/p99 matter and averages hide problems. Example shape (metric name depends on instrumentation): `histogram_quantile(0.99, sum(rate(<metric>_bucket[5m])) by (le, service))`.
- **Distributed tracing:** a trace ID propagates across services; each span records service, operation, duration, status.
- **Alerting:** SLOs and error budgets; alert on symptoms (error rate, latency) rather than causes.
- **Kubernetes basics** (later): pods, deployments, restarts, resource limits; PagerDuty for paging and escalation.

## 6. Phased plan

**Phase 0: Concepts (1-2 days, in parallel with Phase 1).** Cover section 5.

**Phase 1: Repo skeleton + first CLAUDE.md.** Create the layout above. The `CLAUDE.md` should be short: what the project is, architecture sketch, how to run it (`docker compose up`), code conventions (Python version, FastAPI, type hints, formatting), and rules such as "don't add dependencies without asking". Revise it whenever Claude Code makes a mistake worth preventing.

**Phase 2: Milestone 1, one request fully visible.**
- One FastAPI service that calls a second one, both with OpenTelemetry.
- JSON logs containing `trace_id`.
- Prometheus scraping `/metrics`; Loki receiving logs via Grafana Alloy (Promtail is being retired in favor of Alloy); Tempo receiving traces.
- A Grafana dashboard with request rate, error rate, p95/p99 latency, and a click-through from trace to logs.
- *Done when:* one request to the first service shows its metrics, logs and cross-service trace in Grafana.

**Phase 3: Expand and break things.**
- Add the third service and a fault-injection switch.
- Fault scenarios: slow endpoint, random 500s, memory leak, bad deploy, dependency timeout, DB connection exhaustion.
- Add alert rules and Alertmanager (webhook first, PagerDuty optional).

**Phase 4: Detection and localization by hand.** For each injected fault, practice finding it manually. This defines what the agent must do.

**Phase 5: Agent tools.** Plain Python functions (section 8).

**Phase 6: Raw agent loop**, then subagents if useful.

**Phase 7: Evals** (section 9).

**Stretch:** Kubernetes deployment, PagerDuty integration, deploy-marker correlation, proposed fixes or PRs, auto-remediation with human approval.

## 7. How to trace an error to the exact service and function

- **Detection:** alerts on error rate, latency percentiles, saturation.
- **Service level:** distributed traces. The span with the error status or the dominant duration points to the guilty service; compare against the parent spans to separate "slow itself" from "slow dependency".
- **Endpoint level:** metrics labeled by route/method, and span names for HTTP routes.
- **Function level:** span attributes (`code.function`, `code.filepath`), stack traces in logs, and custom spans around key functions.
- **Correlation:** logs carry `trace_id`, so a failing trace can be joined to its log lines.
- **Change correlation:** record deploy events with the git SHA. "What changed just before this started?" explains many incidents.

## 8. Agent design

Treat the agent as an investigator with tools.

**Tools (plain Python functions, later also exposed as an MCP server):**
- `get_alert(alert_id)`
- `query_metrics(promql, start, end)`
- `search_logs(logql, start, end, limit)`
- `find_traces(service, min_duration, status, start, end)` (TraceQL via Tempo)
- `get_trace(trace_id)`
- `list_recent_deploys(since)`
- `read_code(path, start_line, end_line)`

**Investigation flow:** alert arrives, check which metrics moved, find slow or failing traces, pull logs for those trace IDs, check recent deploys, read the suspect code, produce a report.

**Report format (structured):** summary, affected service, endpoint, function, evidence list (each with source and query), suspected cause, confidence, recommended actions.

**Architecture notes:**
- Write the raw loop first (messages in, tool calls out, results back) to understand the mechanics.
- Subagents map naturally to signals: one each for metrics, logs, traces, plus an orchestrator.
- Guardrails: read-only tools, step and token budget, truncate large tool outputs, redact secrets from logs before sending to the model, log every tool call for debugging.
- MCP: exposing the tools as an MCP server lets both Claude Code and the custom agent use them.

## 9. Evals

Run the fault-injection scenarios against the agent and score:
- Correct service identified
- Correct endpoint identified
- Correct cause category (latency, errors, resource, deploy)
- Time and tokens used
- Whether evidence cited actually supports the conclusion

Keep a table of scenario vs result over time. This is what makes the project credible beyond a demo.

## 10. Claude Code learning track (done while building, not separately)

- `CLAUDE.md`: from day one; refine continuously.
- `.claude/agents/`: subagents such as a code reviewer, a test writer, a "PromQL/LogQL helper".
- Skills, slash commands and hooks: add when a task repeats.
- MCP servers: connect Claude Code to Prometheus or Loki as a preview of what the agent does.
- Plan mode and small reviewable tasks; read diffs rather than accepting blindly.

## 11. Working agreements for Claude in this project

- Explain the concept briefly before using it; the user is learning this stack.
- Prefer small, runnable steps over large dumps; each step should end with something verifiable.
- Keep to the decisions in section 2 unless there is a strong reason; flag it if so.
- Python code with type hints; pin versions in compose and requirements.
- Never put secrets in files that get committed.

## 12. Status

- 2026-11-01: brainstorm done, decisions in section 2 made (including WSL2 and fresh toy services), local `observability/` git folder created. No code yet.
- Next: generate starter files (repo layout, `CLAUDE.md`, `docker-compose.yml` with Prometheus, Loki, Tempo, Alloy and Grafana, first FastAPI service), then build Milestone 1.

Update this section at each milestone so later chats start from the right place.