# Tracker — last updated 2026-11-05

## Phase status
| Phase | Name | Status |
|---|---|---|
| 0 | Concepts | ✅ Done |
| 1 | Repo skeleton + CLAUDE.md | ✅ Done |
| 2 | Milestone 1 — one request fully visible | 🔄 In progress — split into M1.1–M1.11 |
| 3 | Expand and break things | ⬜ |
| 4 | Detection and localization by hand | ⬜ |
| 5 | Agent tools | ⬜ |
| 6 | Raw agent loop | ⬜ |
| 7 | Evals | ⬜ |
| Stretch | Kubernetes, PagerDuty, auto-remediation | ⬜ |

## Phase 2 — Milestone 1, split into small steps

Milestone 1 was too large to track as one item, so it is broken into eleven steps. Each step is independently verifiable and should land as one reviewable change set.

**Definition of done (whole milestone):**
- [ ] `docker compose up -d --build` brings up gateway, orders, Prometheus, Loki, Tempo, Alloy, Grafana
- [ ] `curl localhost:8000/orders` succeeds; the same `trace_id` appears in both services' JSON logs
- [ ] Prometheus has `http_server_requests_total` and `http_server_request_duration_seconds_bucket` for both services
- [ ] Tempo shows one trace with gateway → orders spans
- [ ] Grafana dashboard shows request rate, 5xx error rate, p95, p99, a logs panel, and trace→logs click-through works
- [ ] `scripts/smoke.sh` passes all three phases (request → Loki → Tempo)
- [ ] `pytest` and `ruff check` pass

**Out of scope (do not build in Phase 2):** payments, fault injection, Prometheus alert rules, Alertmanager, agent tools, the raw agent loop, evals, Kubernetes. `infra/alertmanager/` stays empty; alerting is Phase 3.

**Locked decisions these steps rely on:**
- Dependencies pinned in `services/requirements.txt`; dev-only tools in `requirements-dev.txt` (see plan). Do not add others without asking.
- Shared telemetry code lives in `services/common/`; one `services/Dockerfile` for all services.
- Metric contract: `http_server_requests_total` (counter) and `http_server_request_duration_seconds_bucket` (histogram), labels `service_name`, `http_request_method`, `http_route`, `http_response_status_code`. `/metrics` and `/healthz` are not recorded.
- Grafana datasource UIDs are fixed: `prometheus`, `loki`, `tempo`. Dashboard `milestone-1`.
- Services push OTLP/HTTP to Alloy; Alloy ships Docker logs to Loki and traces to Tempo; Prometheus scrapes `/metrics` directly.

**Smoke script contract — `scripts/smoke.sh`** (new file; grows across M1.7, M1.9, M1.10):
- One bash script, three phases. Each phase is added when its dependency lands, and every phase exits non-zero naming the check that failed — it is red-capable, not informational. `tests/test_infra_configs.py` proves configs are well-formed; this proves the pipeline actually carried a request end to end.
- The trace ID is **synthesised by the script**, not read out of the logs it is verifying. Send `traceparent: 00-<random 32 hex>-<random 16 hex>-01` to `localhost:8000/orders`, then look for that exact ID downstream. Reuses only propagation M1.6 already tests.
- Phase 1 (M1.7): `curl -sS localhost:8000/orders` → 200, and the synthesised trace ID appears in `docker compose logs gateway orders`.
- Phase 2 (M1.9): poll `GET localhost:3100/loki/api/v1/query_range?query={service_name=~"gateway|orders"} |= "<trace_id>"` until **both** services return a line; fail after 30s.
- Phase 3 (M1.10): poll `GET localhost:3200/api/traces/<trace_id>` until the trace contains spans from **both** `gateway` and `orders`; fail after 30s.
- Polling is required, not optional: Alloy ships logs and traces asynchronously, so a single immediate query is flaky and a flaky check is worse than none.
- Requires Loki published on host `3100` and Tempo on host `3200` (see M1.9/M1.10).

**Step index:**
| Step | Scope | Status |
|---|---|---|
| M1.1 | Scaffolding & test harness | ✅ |
| M1.2 | Structured logging | ⬜ |
| M1.3 | Metrics + request middleware | ⬜ |
| M1.4 | Tracing wiring | ⬜ |
| M1.5 | Orders service | ⬜ |
| M1.6 | Gateway service | ⬜ |
| M1.7 | Containerize the two services | ⬜ |
| M1.8 | Prometheus + Grafana datasource | ⬜ |
| M1.9 | Loki + Alloy logs | ⬜ |
| M1.10 | Tempo + Alloy traces | ⬜ |
| M1.11 | Dashboard, docs, acceptance | ⬜ |

### M1.1 — Scaffolding & test harness ✅
- **Build:** `pyproject.toml` (`[tool.pytest.ini_options]` `pythonpath = ["services"]`, `testpaths = ["tests"]`; `[tool.ruff]` `target-version = "py311"`), pinned `services/requirements.txt` and `requirements-dev.txt`, `.env.example` with `GF_ADMIN_PASSWORD=admin`, `tests/conftest.py` setting `OTEL_EXPORTER_OTLP_ENDPOINT=""`, `ENVIRONMENT=test`, `LOG_LEVEL=debug` before app imports, package `__init__.py` files.
- **Tests:** harness only — `pytest -q` collects cleanly.
- **Done when:** `pip install -r requirements-dev.txt` succeeds; `pytest -q` exits 0; `ruff check .` clean.

### M1.2 — Structured logging ⬜
- **Build:** `services/common/logging.py` — structlog JSON to stdout with ISO-8601 UTC timestamp, level, event, `service`, `trace_id`, `span_id`; configured once. IDs are zero-padded 32/16 hex from the active span, `None` outside a span.
- **Tests:** `tests/test_logging.py`.
- **Done when:** tests pass; output parses as JSON.

### M1.3 — Metrics + request middleware ⬜
- **Build:** `services/common/telemetry.py` metrics half — Resource (`service.name`, `deployment.environment`), MeterProvider + PrometheusMetricReader, instruments `http.server.request.duration` (unit s) and `http.server.requests`, mount `prometheus_client.make_asgi_app()` at `/metrics`; `services/common/middleware.py` — records service/method/route/status/duration, route template from `scope["route"]` with `unmatched` fallback, skips `/metrics` and `/healthz`, logs one `request_completed` line, records 500 and re-raises on exceptions. Idempotent per process.
- **Tests:** `tests/test_metrics.py` — counter and histogram exist with the contract labels; `/metrics` returns Prometheus text; `/metrics` and `/healthz` requests do not increment; a second init does not duplicate series.
- **Done when:** tests pass.

### M1.4 — Tracing wiring ⬜
- **Build:** extend `telemetry.py` — TracerProvider with the Resource, BatchSpanProcessor + OTLP/HTTP exporter only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; `FastAPIInstrumentor.instrument_app(app)` called last so the server span is outermost; idempotent per process.
- **Tests:** `tests/test_tracing.py` — handler sees a valid span whose trace ID matches the log; an httpx call made inside a span carries `traceparent`; no OTLP exporter when the endpoint env var is empty.
- **Done when:** tests pass.

### M1.5 — Orders service ⬜
- **Build:** `services/orders/main.py` — in-memory orders; `GET /orders`, `GET /orders/{id}` (404 for unknown), `GET /healthz`; `init_observability(app, "orders")`.
- **Tests:** `tests/test_orders.py` — list 200, unknown id 404, healthz 200, metrics carry `service_name="orders"`.
- **Done when:** tests pass.

### M1.6 — Gateway service ⬜
- **Build:** `services/gateway/main.py` — lifespan-owned `httpx.Client` (`ORDERS_URL=http://orders:8001`) exposed as a dependency for test overrides; `GET /orders`, `GET /orders/{id}` pass 4xx through and map transport errors to 502; `GET /healthz`; structured `orders_call_failed` log on error.
- **Tests:** `tests/test_gateway.py` — proxy success, 404 passthrough, `httpx.ConnectError` → 502, `traceparent` forwarded on the outgoing request.
- **Done when:** tests pass.

### M1.7 — Containerize the two services ⬜
- **Build:** `services/Dockerfile` (`python:3.11-slim-bookworm`, install requirements, `PYTHONPATH=/app`, copy `common/`, `gateway/`, `orders/`), `services/.dockerignore`, compose entries for gateway and orders (shared build, per-service command/port, `uvicorn --no-access-log`, `OTEL_SERVICE_NAME`, `LOG_LEVEL`); `scripts/smoke.sh` phase 1.
- **Verify:** `docker compose up -d --build`; `curl -s localhost:8000/orders` → 200; `curl -s localhost:8000/orders/999` → 404; `scripts/smoke.sh` phase 1.
- **Done when:** `scripts/smoke.sh` phase 1 passes — the synthesised trace ID appears in both services' logs. First Docker-dependent step.

### M1.8 — Prometheus + Grafana datasource ⬜
- **Build:** `infra/prometheus/prometheus.yml` (5s scrape of `gateway:8000` and `orders:8001`); compose Prometheus `v3.0.1` and Grafana `11.4.0` with named volumes; Grafana provisioning `infra/grafana/provisioning/datasources/datasources.yml` (Prometheus, UID `prometheus`, default); `GF_ADMIN_PASSWORD` from env.
- **Tests:** `tests/test_infra_configs.py` — YAML/JSON parses; image tags pinned (no `latest`); mounts relative; Prometheus targets match compose service names/ports; referenced datasource UIDs exist.
- **Done when:** Prometheus targets UP; both metric families present for both services; Grafana Explore plots request rate.

### M1.9 — Loki + Alloy logs ⬜
- **Build:** `infra/loki/loki-config.yml` (single binary, schema v13, filesystem storage, `auth_enabled: false`, structured metadata on, analytics off); `infra/alloy/config.alloy` (`discovery.docker` → `discovery.relabel` keeping only `gateway|orders` and setting `service_name` from `com.docker.compose.service` → `loki.source.docker` → `loki.write` to `http://loki:3100/loki/api/v1/push`); compose Loki `3.3.2` + Alloy `v1.5.1` mounting `/var/run/docker.sock:ro` and the config, Loki published on host `3100` for the smoke script; Loki datasource UID `loki`; `scripts/smoke.sh` phase 2.
- **Tests:** extend `tests/test_infra_configs.py`.
- **Done when:** Grafana Explore query `{service_name="gateway"}` returns JSON log lines containing `trace_id`; `scripts/smoke.sh` phase 2 passes.

### M1.10 — Tempo + Alloy traces ⬜
- **Build:** `infra/tempo/tempo.yml` (single binary, local backend `/var/tempo`, OTLP receivers 4317/4318); Alloy `otelcol.receiver.otlp` → `otelcol.processor.batch` → `otelcol.exporter.otlp` to `tempo:4317` (insecure), run Alloy with `--stability.level=experimental`; compose Tempo `2.6.1`, Tempo published on host `3200` for the smoke script; services get `OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318` and `OTEL_RESOURCE_ATTRIBUTES=deployment.environment=development`; Tempo datasource UID `tempo`; Loki derived field `trace_id` → Tempo internal link; Tempo `tracesToLogsV2` (filter by trace ID).
- **Tests:** extend `tests/test_infra_configs.py`.
- **Done when:** one trace in Tempo shows gateway and orders spans; clicking a Loki log line opens that trace; "Logs for this span" opens Loki filtered by trace ID; `scripts/smoke.sh` phase 3 passes.

### M1.11 — Dashboard, docs, acceptance ⬜
- **Build:** `infra/grafana/dashboards/milestone-1.json` (15m range, 5s refresh, `$service` variable; request rate, 5xx rate, p95, p99, Loki logs `{service_name=~"$service"} | json`, Tempo TraceQL traces panel), `infra/grafana/provisioning/dashboards/dashboards.yml` → folder Demo; `README.md` (venv/install/test commands, Milestone 1 URLs, `scripts/smoke.sh`); `CLAUDE.md` repo layout update (add `scripts/`); tick this tracker.
- **Tests:** extend `tests/test_infra_configs.py` — dashboard JSON parses; referenced datasource UIDs exist; provisioning points at a real dashboard path.
- **Done when:** every Milestone 1 definition-of-done box at the top is ticked; `pytest -q` and `ruff check services tests` pass.

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

## In flight / Next
- [ ] **M1.2 — Structured logging** (start here)

## Open questions / blockers
- None blocking the plan itself.

## Notes
- This is a simple markdown tracker — a single source of truth for *build state*.
- Keep it updated at step boundaries; update `docs/observability-agent-plan.md` when a decision changes.
- Security: `.claude/settings.local.json` appears to contain an OpenRouter key. It is gitignored and untouched here, but rotate it if that file has ever been shared or committed anywhere.
