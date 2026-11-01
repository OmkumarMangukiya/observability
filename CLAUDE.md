# AI Observability Agent - Project Guide

## 1. Project Overview
This project builds an autonomous **AI Observability Agent** that investigates failures across microservices.
When an alert fires or an incident occurs, the agent queries metrics (Prometheus), logs (Loki), and distributed traces (Tempo), pinpoints the faulty service/endpoint/function, and generates a structured root-cause report with cited evidence.

Full design context, roadmap, and decisions are documented in [`docs/observability-agent-plan.md`](docs/observability-agent-plan.md).

## 2. Architecture Sketch
```
Toy Microservices (FastAPI + OpenTelemetry)
       | metrics / logs / traces
       v
Prometheus (metrics) + Loki (logs) + Tempo (traces), fed by Grafana Alloy
       |
       v
Grafana (dashboards) + Alertmanager (alerts)
       | alert fires
       v
Agent (Python raw API loop): investigates signals, isolates fault, reports root cause
```

## 3. How to Run
All infrastructure and services run via Docker Compose inside WSL2:

```bash
# Start all infrastructure and microservices
docker compose up -d

# View logs
docker compose logs -f

# Stop services
docker compose down
```

## 4. Code & Architecture Conventions
- **Language & Runtime:** Python 3.11+ with strict type hints on all functions and variables.
- **Frameworks:** FastAPI for demo services.
- **Agent Architecture:** Raw API loop (no third-party agent frameworks such as LangChain or CrewAI) to maintain full control and transparency.
- **Instrumentation:** OpenTelemetry standard with JSON-formatted logs propagating `trace_id`.
- **Docker:** Pin image versions and use relative file paths for volume mounts.

## 5. Working Rules & Agreements
1. **Explain First:** Briefly explain observability concepts (RED, USE, p95/p99 histograms, trace propagation) before using them—the user is actively learning this stack.
2. **Small Increments:** Build in small, runnable, verifiable steps rather than massive unverified code dumps.
3. **Dependencies:** Do NOT add new third-party libraries or dependencies without asking first.
4. **Security & Secrets:** Never commit secrets or credentials. Use `.env` and `.env.example`.
5. **Adhere to Decisions:** Follow the architectural decisions recorded in `docs/observability-agent-plan.md` unless explicitly discussed.
