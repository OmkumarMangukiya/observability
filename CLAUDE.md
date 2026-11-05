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
services/{gateway,orders,payments}/
agent/loop.py             # raw API agent loop
agent/tools/              # Python tool functions
agent/prompts/            # prompt templates
infra/{prometheus,loki,tempo,grafana,alertmanager}/
faults/                   # fault-injection scenarios
evals/                    # agent evaluation harness
docs/                     # plans, decisions, notes
```

## Commands

```bash
docker compose up -d            # start infra + services
docker compose down
docker compose logs -f orders   # tail one service
pytest tests/                   # run tests
ruff check . && ruff format .   # lint + format
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

## Rules

1. **Tests first.** Write a failing test before implementing. Work in small, verifiable steps.
2. **No sneak dependencies.** Do not add third-party libraries without asking.
3. **Surgical changes.** Touch only code required by the task. Don't refactor adjacent code/comments or formatting.
4. **Follow the plan.** Adhere to decisions in `docs/observability-agent-plan.md` unless explicitly discussed.
5. **No secrets in code.** Use `.env` / `.env.example`. Never commit credentials.
6. Talk like a friend explaining to a beginner: short, plain, informal English, straight to the point, and explain any jargon in a few words.
