# AI Observability Agent — Project Guide

## What This Is

An autonomous agent that investigates microservice incidents. On alert, it queries
Prometheus (metrics), Loki (logs), and Tempo (traces), pinpoints the root cause,
and generates a cited report.

Full roadmap & decisions: `docs/observability-agent-plan.md`

## Architecture

```
FastAPI services (OpenTelemetry-instrumented)
       | metrics / logs / traces
       v
Prometheus + Loki + Tempo  ←  Grafana Alloy
       |
       v
Grafana (dashboards) + Alertmanager
       | alert fires
       v
Agent (raw Python API loop) → root-cause report
```

## Repo Layout

```
CLAUDE.md
docker-compose.yml
.env.example              # never commit .env
services/{gateway,orders,payments,webhook_receiver}/
services/common/          # shared telemetry, logging, fault framework
agent/loop.py             # raw API agent loop
agent/tools/              # Python tool functions
agent/prompts/            # prompt templates
infra/{prometheus,loki,tempo,grafana,alertmanager}/
faults/                   # fault-injection scenarios
evals/                    # agent evaluation harness
scripts/                  # smoke.sh end-to-end pipeline check
docs/                     # plans, decisions, notes
```

## Commands

```bash
docker compose up -d            # start infra + services
docker compose down
docker compose logs -f orders   # tail one service
pytest tests/                   # run tests
ruff check . && ruff format .   # lint + format
scripts/smoke.sh                # end-to-end check (logs, Loki, Tempo, alert path)
```

## Code Conventions

- **Python 3.11+**, strict type hints on all functions/parameters/returns.
- **FastAPI** for services. **Raw API loop** for the agent — no LangChain, CrewAI, etc.
- **OpenTelemetry** instrumentation. JSON logs with `trace_id`.
- **Docker:** pin image versions, relative paths for volume mounts.
- **Formatting:** `ruff format`. **Linting:** `ruff check`. **Tests:** `pytest`.
- **Imports:** stdlib → third-party → local, blank line between groups.
- **Naming:** `snake_case` functions/vars, `PascalCase` classes, Google-style docstrings.
- **Logging:** structlog — never `print()`. Include `trace_id` in request context.
- **Paths:** all Linux paths (WSL2). Never use Windows paths in code or configs.

## Fault Injection

- Framework lives in `services/common/faults.py`. A service calls `init_faults(app)`
  **before** `init_observability(app, ...)`, so metrics/traces still measure injected
  responses and delays.
- Fault types live in `services/<service>/faults.py` and register with
  `@register_fault("name", env_param=..., description=...)`.
- Turn one on with env `FAULT_<NAME>` (`true`/`false`, a number, or JSON params,
  e.g. `FAULT_RANDOM_500S=0.3`) or `POST /faults` with
  `{"fault": "random_500s", "enabled": true, "params": {"rate": 0.3}}`.
- `GET /faults` lists active faults. `/faults`, `/healthz`, and `/metrics` are
  never faulted, so the off switch and probes always work.

## Rules

1. **Tests first.** Write a failing test before implementing. Work in small, verifiable steps.
2. **No sneak dependencies.** Do not add third-party libraries without asking.
3. **Surgical changes.** Touch only code required by the task. Don't refactor adjacent code/comments or formatting.
4. **Follow the plan.** Adhere to decisions in `docs/observability-agent-plan.md` unless explicitly discussed.
5. **No secrets in code.** Use `.env` / `.env.example`. Never commit credentials.
6. **Beginner-friendly tone.** Talk like a friend explaining to a beginner: short, plain, informal English, straight to the point, and explain any jargon in a few words.
7. **Plan before coding.** Before creating, editing, or deleting any file:
   - Explain what will change, how it works, and why this approach.
   - Show the critical code you plan to write.
   - Stop and wait for my explicit approval (e.g. "go ahead") before making any file changes.
   - Do not call Edit, Write, or any file-modifying command until I approve.
