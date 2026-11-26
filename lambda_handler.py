"""AWS Lambda entry point.

The same FastAPI app is served through a Function URL via Mangum. No API Gateway,
no load balancer, no container always running — that is what keeps this at $0.

Lambda's filesystem is read-only except /tmp, so the container sets WRITE_DIR=/tmp/data;
see config.py. Runtime state (queue, token ledger, traces) is per-container and is lost on
cold start: fine for a demo, not a design for production.
"""
from __future__ import annotations

from mangum import Mangum

from claims_assistant.api import app

# lifespan="off": this app has no startup/shutdown handlers, and disabling lifespan
# avoids Mangum's lifespan emulation overhead on every cold start.
handler = Mangum(app, lifespan="off")
