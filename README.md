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

1. the `gateway`, `orders`, and `payments` logs (`docker compose logs`),
2. Loki,
3. Tempo, with spans from all three services.

Phase 4 then turns on the `random_500s` fault on payments, waits for the
`HighErrorRate` alert to fire, and checks the webhook receiver logged it. It
polls each store and Prometheus for up to 60s/3min, because Alloy ships
asynchronously and the alert needs a 1-minute pending period. If a previous run
left the alert firing, phase 4 waits up to 6 minutes for the 5-minute error
window to clear (Alertmanager would not resend sooner). Exits non-zero naming
the failed phase. Skip the slow fault check with `SKIP_FAULT_SMOKE=1`.

## 🧪 Phase 3 — Expand and Break Things

Phase 3 adds the payments service, a fault-injection framework, and alerting.
A request now travels `gateway → orders → payments`.

| What | URL |
|---|---|
| Payments service | http://localhost:8002/healthz |
| Alertmanager | http://localhost:9093 |
| Webhook receiver | http://localhost:9000/webhook |

### Fault injection

Every service mounts the shared fault framework (`services/common/faults.py`).
Faults do nothing until you turn one on, either with an environment variable or
through the control endpoint.

| Fault | Env var | Params | Effect |
|---|---|---|---|
| `slow_endpoint` | `FAULT_SLOW_ENDPOINT=true` | `seconds` or `min_seconds` / `max_seconds` | sleeps before `POST /payments/process` |
| `random_500s` | `FAULT_RANDOM_500S=0.3` | `rate` (0–1) | returns 500 for that share of process requests |
| `dependency_timeout` | `FAULT_DEPENDENCY_TIMEOUT=true` | `seconds` (default 30) | hangs past the caller's 10s timeout |
| `bad_deploy` | `FAULT_BAD_DEPLOY=true` | none | every application request returns 500; startup logs a `deploy` marker |
| `memory_leak` | `FAULT_MEMORY_LEAK=true` | `bytes_per_request` | keeps bytes on every request, so RSS climbs |

Env values can be `true` / `false`, a number, or JSON params, for example
`FAULT_SLOW_ENDPOINT='{"seconds": 3}'`.

Toggles over HTTP (payments listens on port 8002):

```bash
# list active faults
curl -s localhost:8002/faults

# turn random_500s on for 50% of payment requests
curl -s -X POST localhost:8002/faults \
  -H 'content-type: application/json' \
  -d '{"fault": "random_500s", "enabled": true, "params": {"rate": 0.5}}'

# turn it off
curl -s -X POST localhost:8002/faults \
  -H 'content-type: application/json' \
  -d '{"fault": "random_500s", "enabled": false}'
```

`/faults`, `/healthz`, and `/metrics` are never faulted, so you can always list
faults or turn one off.

### Alerts

Prometheus loads three rules from `infra/prometheus/alert-rules.yml`:

- `HighErrorRate` — more than 5% of requests are 5xx for 1 minute.
- `HighLatencyP99` — p99 latency above 2s for 1 minute.
- `ServiceDown` — a scrape target is down for 30s.

Alertmanager groups them and posts them to the webhook receiver, which logs each
payload as structured JSON:

```bash
docker compose logs -f webhook-receiver
```

- Prometheus alert states: http://localhost:9090/alerts
- Alertmanager UI: http://localhost:9093

The Grafana dashboard shows a **5xx error ratio** panel with the alert's 5%
threshold marked, so you can see the same signal the alert watches.

## 🗺️ Roadmap & Detailed Plan
See [`docs/observability-agent-plan.md`](docs/observability-agent-plan.md) for detailed architecture, phased milestones, concepts, and eval designs.

## 📄 License
[MIT](LICENSE) © 2026 OmkumarMangukiya
