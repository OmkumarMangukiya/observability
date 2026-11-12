# AI Observability Agent

An AI-powered observability agent that detects, diagnoses, and localizes microservice failures using the three pillars of observability: **metrics** (Prometheus), **logs** (Loki), and **traces** (Tempo).

---

## 🎯 Goal
When a service breaks or an alert triggers, the agent independently:
1. Queries metrics to identify symptom signals (error spikes, latency shifts).
2. Traverses distributed traces across services to locate the bottleneck or failure span.
3. Retrieves relevant logs correlated by `trace_id` to inspect stack traces and error messages.
4. Generates a structured root-cause incident report with concrete evidence citations.

## 🏗️ Architecture

```
Toy Microservices (FastAPI + OpenTelemetry)
       │ (metrics, JSON logs with trace_id, distributed traces)
       ▼
Telemetry Pipeline (Grafana Alloy / OpenTelemetry)
       ├── Metrics ──▶ Prometheus
       ├── Logs ─────▶ Loki
       └── Traces ───▶ Tempo
       │
       ▼
Dashboards & Alerts (Grafana + Alertmanager)
       │ (alert triggers)
       ▼
AI Observability Agent (Python Raw API Loop)
       │ (queries PromQL, LogQL, TraceQL)
       ▼
Root Cause Report (Service, Endpoint, Function, Evidence)
```

## 📁 Repository Structure

```
observability/
├── CLAUDE.md              # Project guidelines and rules for Claude Code
├── README.md              # Project documentation and setup guide
├── .gitignore             # Git ignore rules for Python, Docker volumes, secrets
├── .gitattributes         # Enforces LF line endings
├── .env.example           # Environment variable template
├── docker-compose.yml     # Compose file for infra and demo services
├── infra/                 # Infrastructure configurations
│   ├── prometheus/        # Prometheus scrape configs and alert rules
│   ├── grafana/           # Grafana datasources and dashboards
│   ├── loki/              # Loki log storage configuration
│   ├── tempo/             # Tempo distributed tracing configuration
│   └── alertmanager/      # Alertmanager routing and webhook configuration
├── services/              # Microservices (FastAPI + OpenTelemetry)
│   ├── gateway/           # API Gateway / entrypoint service
│   ├── orders/            # Orders microservice
│   └── payments/          # Payments microservice
├── faults/                # Fault injection scenarios (latency, errors, leaks)
├── agent/                 # AI Agent implementation
│   ├── loop.py            # Raw agent conversation and tool-calling loop
│   ├── tools/             # Observability query tools (PromQL, LogQL, TraceQL)
│   └── prompts/           # Agent system prompts and investigation guides
├── evals/                 # Evaluation suite scoring fault scenario diagnoses
├── docs/                  # Project specifications and decisions
│   └── observability-agent-plan.md
└── .claude/
    └── agents/            # Subagents for Claude Code workflows
```

## 🚀 Getting Started

### Prerequisites
- **WSL 2** (Ubuntu 22.04 recommended)
- **Docker & Docker Compose** (Docker Desktop with WSL2 integration enabled or Docker Engine installed in WSL)
- **Python 3.11+**

### Setup
1. Create and activate a virtual environment (Python 3.11+):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements-dev.txt
   ```
2. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
3. Configure your API keys in `.env`.
4. Run the tests and linter:
   ```bash
   pytest -q
   ruff check .
   ```
5. Launch infrastructure and services (Milestone 1 onwards):
   ```bash
   docker compose up -d --build
   ```

## 🔍 Milestone 1 — One Request, Fully Visible

Everything below runs locally after `docker compose up -d --build`.

| What | URL |
|---|---|
| Gateway (entrypoint) | http://localhost:8000/orders |
| Orders service | http://localhost:8001/orders |
| Prometheus | http://localhost:9090 |
| Loki | http://localhost:3100 |
| Tempo | http://localhost:3200 |
| Grafana | http://localhost:3000 |

In Grafana, open **Dashboards → Demo → Milestone 1**. It shows request rate, 5xx
rate, p95/p99 latency, logs, and traces for the `$service` you pick. Log lines
link to their trace in Tempo, and Tempo spans link back to Loki logs.

### End-to-end smoke check

```bash
scripts/smoke.sh
```

The script makes one request with a synthetic trace ID and checks the same ID
travels through all three phases:

1. the `gateway` and `orders` logs (`docker compose logs`),
2. Loki,
3. Tempo, with spans from both services.

It polls each store for up to 30s, because Alloy ships logs and traces
asynchronously, and exits non-zero naming the failed phase.

## 🗺️ Roadmap & Detailed Plan
See [`docs/observability-agent-plan.md`](docs/observability-agent-plan.md) for detailed architecture, phased milestones, concepts, and eval designs.

## 📄 License
[MIT](LICENSE) © 2026 OmkumarMangukiya
