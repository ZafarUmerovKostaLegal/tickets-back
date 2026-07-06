# Tests layout



```

tests/

├── conftest.py              # fixtures, cached gateway app, path hook

├── conftest_hooks.py        # auto-markers, E2E 25% PR sampling

├── support/

│   └── service_path.py

├── unit/                    # per-service unit tests

├── integration/             # docker-compose stack (httpx → gateway)

└── e2e/

    ├── support/             # route discovery, respx, personas, bodies

    ├── gateway/

    ├── services/

    └── workflows/

```



## Commands



```bash

# Unit + coverage gate (≥40% application layer, see .coveragerc)

pytest tests/unit --cov



# Fast E2E (25% auth-route sample + workflows, parallel)

pytest tests/e2e -m "e2e and not e2e_full" -n auto



# Full route sweep (nightly / manual)

bash scripts/run_e2e_full.sh

# or: E2E_FULL=1 pytest tests/e2e -m e2e_full -n auto



# Integration stack

docker compose -f docker-compose.e2e.yml up -d --wait

pytest tests/integration -m integration

docker compose -f docker-compose.e2e.yml down -v



# Metrics artifact

python scripts/collect_project_metrics.py

```



## CI pipelines



| Workflow | When | What |

|----------|------|------|

| `ci.yml` | every PR | unit+coverage, fast e2e, metrics artifact |

| `integration.yml` | PR + weekly | docker-compose stack, integration tests, full e2e on schedule |

| `tests.yml` (front) | every PR | unit, sharded e2e, staging smoke on schedule |



## Architecture notes



- **Gateway thin logic**: `gateway/application/time_tracking_self_user.py` (user upsert payload)

- **TT reports package**: `time_tracking/application/reports/partner_scope.py`

- Legacy import `application.partner_pending_list_scope` re-exports from `reports`



## Frontend real-backend



```bash

PW_REAL_BACKEND=1 PW_STAGING_URL=http://localhost:1234 npm run test:e2e:real-backend

```



Set repository variable `STAGING_GATEWAY_URL` for scheduled staging smoke in GitHub Actions.



Set `PYTHONPATH=tests` (configured in `pytest.ini`).



Performance canvas: `canvases/project-performance.canvas.tsx`


