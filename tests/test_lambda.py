"""Tests for the AWS Lambda entry point and the writable-directory switch.

These run without AWS: Mangum is invoked with a synthetic Function URL (API Gateway v2)
event, which is the same payload Lambda passes to the handler.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _event(path: str, method: str = "GET") -> dict:
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "example.lambda-url.ap-south-1.on.aws"},
        "requestContext": {
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.7",
                "userAgent": "pytest",
            },
            "stage": "$default",
            "requestId": "test-request",
        },
        "body": "",
        "isBase64Encoded": False,
    }


def test_handler_serves_health():
    sys.path.insert(0, str(ROOT))
    from lambda_handler import handler

    response = handler(_event("/api/health"), None)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["ok"] is True
    assert "demo" in body


def test_handler_serves_dashboard():
    sys.path.insert(0, str(ROOT))
    from lambda_handler import handler

    response = handler(_event("/dashboard"), None)
    assert response["statusCode"] == 200
    assert "Claims Triage Console" in response["body"]


def test_handler_returns_404_for_unknown_api_path():
    sys.path.insert(0, str(ROOT))
    from lambda_handler import handler

    response = handler(_event("/api/nope"), None)
    assert response["statusCode"] == 404


def test_write_dir_redirects_runtime_state(tmp_path):
    """On Lambda the task root is read-only; WRITE_DIR must move every write path."""
    script = (
        "import json, os;"
        "from claims_assistant import config;"
        "print(json.dumps({"
        "'claims': str(config.CLAIMS_PATH),"
        "'usage': str(config.USAGE_PATH),"
        "'runs': str(config.RUNS_PATH),"
        "'queue': str(config.CASE_QUEUE_PATH),"
        "'uploads': str(config.UPLOADS_DIR),"
        "'data_dir': str(config.DATA_DIR)}))"
    )
    env = {**os.environ, "WRITE_DIR": str(tmp_path), "PYTHONPATH": str(ROOT / "src")}
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, check=True
    )
    paths = json.loads(out.stdout)

    assert paths["claims"].startswith(str(tmp_path))
    assert paths["usage"].startswith(str(tmp_path))
    assert paths["runs"].startswith(str(tmp_path))
    assert paths["queue"].startswith(str(tmp_path))
    assert paths["uploads"].startswith(str(tmp_path))
    # packaged inputs stay where they are
    assert paths["data_dir"] != str(tmp_path)
    # the seed database is copied into the writable dir
    assert (tmp_path / "claims.json").exists()
