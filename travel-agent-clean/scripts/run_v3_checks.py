#!/usr/bin/env python3
"""
Travel Agent V3 runtime checks.

Default mode is offline-safe:
  - validates basic environment/dependency presence
  - runs backend/tests/test_v3.py
  - compiles the V3 entrypoints/modules

Use --api when the FastAPI server is already running and you want live endpoint
checks against Azure OpenAI, AMAP, Ctrip/Booking, and the chat workflow.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"

REQUIRED_ENV = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
    "AMAP_API_KEY",
)

OPTIONAL_ENV_DEFAULTS = {
    "MAX_TOOL_ROUNDS": "8",
    "OPENAI_TIMEOUT_SECONDS": "60",
    "CONTEXT_RECENT_MESSAGES": "12",
    "CONTEXT_SUMMARY_TRIGGER_MESSAGES": "20",
    "CONTEXT_STORE": "memory",
    "PRICE_CURRENCY": "CNY",
}

PY_COMPILE_TARGETS = [
    "backend/main.py",
    "backend/cli.py",
    "backend/api/routes.py",
    "backend/api/schemas.py",
    "backend/agent/context_manager.py",
    "backend/agent/orchestrator.py",
    "backend/agent/requirement_agent.py",
    "backend/agent/planner_agent.py",
    "backend/agent/output_formatter.py",
]

PYTHON_DEPS = (
    "fastapi",
    "uvicorn",
    "openai",
    "mcp",
    "dotenv",
    "requests",
)


def maybe_reexec_with_project_venv() -> None:
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if os.environ.get("V3_CHECKS_REEXECED") == "1":
        return
    if sys.prefix != sys.base_prefix:
        return
    if not venv_python.exists():
        return
    if Path(sys.executable) == venv_python:
        return

    print(f"[INFO] Re-running with project venv: {venv_python}")
    os.environ["V3_CHECKS_REEXECED"] = "1"
    os.execv(str(venv_python), [str(venv_python), *sys.argv])


class CheckResult:
    def __init__(self) -> None:
        self.failed = 0
        self.warned = 0

    def pass_(self, message: str) -> None:
        print(f"[PASS] {message}")

    def warn(self, message: str) -> None:
        self.warned += 1
        print(f"[WARN] {message}")

    def fail(self, message: str) -> None:
        self.failed += 1
        print(f"[FAIL] {message}")


def _load_dotenv_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for env_file in (PROJECT_ROOT / ".env", BACKEND_DIR / ".env"):
        for key, value in _load_dotenv_file(env_file).items():
            env.setdefault(key, value)
    return env


def run_command(args: list[str], cwd: Path, result: CheckResult, label: str) -> bool:
    print(f"\n$ {' '.join(args)}")
    started = time.time()
    proc = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    elapsed = time.time() - started
    if proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.returncode == 0:
        result.pass_(f"{label} ({elapsed:.1f}s)")
        return True
    result.fail(f"{label} exited with code {proc.returncode}")
    return False


def check_environment(result: CheckResult, env: dict[str, str]) -> None:
    print("\n== Environment ==")

    if sys.prefix == sys.base_prefix:
        result.warn("当前 Python 看起来没有激活虚拟环境；建议先运行 source .venv/bin/activate")
    else:
        result.pass_(f"virtualenv active: {sys.prefix}")

    missing = [key for key in REQUIRED_ENV if not env.get(key)]
    if missing:
        result.fail("缺少必要环境变量: " + ", ".join(missing))
    else:
        deployment = env.get("AZURE_OPENAI_DEPLOYMENT", "")
        result.pass_(f"Azure/OpenAI env present; deployment={deployment}")
        if deployment and deployment != "gpt-5.4":
            result.warn("当前 AZURE_OPENAI_DEPLOYMENT 不是 gpt-5.4；如需测 5.4，请确认 Azure deployment 后再切换")

    for key, default in OPTIONAL_ENV_DEFAULTS.items():
        value = env.get(key) or default
        result.pass_(f"{key}={value}")

    for dep in PYTHON_DEPS:
        if importlib.util.find_spec(dep) is None:
            result.fail(f"Python dependency missing: {dep}; run pip install -r backend/requirements.txt")
        else:
            result.pass_(f"Python dependency available: {dep}")


def run_offline_checks(result: CheckResult) -> None:
    print("\n== Offline Checks ==")
    run_command([sys.executable, "backend/tests/test_v3.py"], PROJECT_ROOT, result, "V3 orchestrator tests")

    compile_args = [sys.executable, "-m", "py_compile", *PY_COMPILE_TARGETS]
    run_command(compile_args, PROJECT_ROOT, result, "V3 syntax compile")


def http_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float,
) -> tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        try:
            parsed: Any = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        return resp.status, parsed


def check_api(result: CheckResult, base_url: str, timeout: float) -> None:
    print("\n== API Checks ==")
    base = base_url.rstrip("/")

    def get(path: str, *, critical: bool = True) -> Any | None:
        url = base + path
        try:
            status, payload = http_json("GET", url, timeout=timeout)
        except Exception as exc:
            message = f"GET {path} failed: {type(exc).__name__}: {exc}"
            if critical:
                result.fail(message)
            else:
                result.warn(message)
            return None
        if 200 <= status < 300:
            result.pass_(f"GET {path}")
            return payload
        message = f"GET {path} returned HTTP {status}"
        if critical:
            result.fail(message)
        else:
            result.warn(message)
        return None

    def post(path: str, body: dict[str, Any], *, critical: bool = False) -> Any | None:
        url = base + path
        try:
            status, payload = http_json("POST", url, body=body, timeout=timeout)
        except Exception as exc:
            message = f"POST {path} failed: {type(exc).__name__}: {exc}"
            if critical:
                result.fail(message)
            else:
                result.warn(message)
            return None
        if 200 <= status < 300:
            result.pass_(f"POST {path}")
            return payload
        message = f"POST {path} returned HTTP {status}"
        if critical:
            result.fail(message)
        else:
            result.warn(message)
        return None

    health = get("/health")
    if isinstance(health, dict) and health.get("status") == "ok":
        result.pass_("health payload is ok")
    else:
        result.warn("health payload did not match {'status': 'ok'}")

    docs = get("/docs")
    if docs is not None:
        result.pass_("Swagger docs reachable")

    weather = get("/api/v1/weather?city_code=110000", critical=False)
    if isinstance(weather, dict) and not weather.get("error"):
        result.pass_("weather endpoint returned JSON")
    else:
        result.warn("weather endpoint returned an error-like payload; check AMAP_API_KEY")

    poi_query = urllib.parse.urlencode({"keywords": "故宫", "city": "北京"})
    pois = get(f"/api/v1/pois?{poi_query}", critical=False)
    if isinstance(pois, dict) and not pois.get("error"):
        result.pass_("POI endpoint returned JSON")
    else:
        result.warn("POI endpoint returned an error-like payload; check AMAP_API_KEY")

    flight_query = urllib.parse.urlencode({
        "origin": "北京",
        "destination": "成都",
        "days": 7,
        "top_n": 5,
    })
    flights = get(f"/api/v1/flights?{flight_query}", critical=False)
    if isinstance(flights, dict) and isinstance(flights.get("cheapest"), list):
        result.pass_("flights endpoint returned cheapest list")
    else:
        result.warn("flights endpoint did not return cheapest list; check Ctrip/network")

    hotel_query = urllib.parse.urlencode({
        "check_in": "2026-05-01",
        "keyword": "故宫",
        "top_n": 5,
    })
    hotels = get(f"/api/v1/hotels?{hotel_query}", critical=False)
    if isinstance(hotels, dict) and isinstance(hotels.get("hotels"), list):
        result.pass_("hotels endpoint returned hotels list")
    else:
        result.warn("hotels endpoint did not return hotels list; check cache/live scraper")

    clarification = post("/api/v1/chat", {"message": "帮我规划北京行程"})
    if isinstance(clarification, dict) and clarification.get("needs_clarification") is True:
        result.pass_("chat clarification flow works")
    else:
        result.warn("chat clarification flow did not return needs_clarification=true")

    itinerary = post(
        "/api/v1/chat",
        {"message": "帮我规划北京3天历史和美食行程，预算3000元，5月1日出发"},
    )
    if isinstance(itinerary, dict) and isinstance(itinerary.get("itinerary"), dict):
        result.pass_("chat itinerary flow returned structured itinerary")
    else:
        result.warn("chat itinerary flow did not return structured itinerary; check Azure/model/tool calls")
        return

    session_id = itinerary.get("session_id")
    modified = post(
        "/api/v1/chat",
        {"session_id": session_id, "message": "把第二天换成室内景点，少走路"},
    )
    if isinstance(modified, dict) and isinstance(modified.get("itinerary"), dict):
        result.pass_("chat modify flow returned structured itinerary")
    else:
        result.warn("chat modify flow did not return structured itinerary")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Travel Agent V3 checks")
    parser.add_argument(
        "--api",
        action="store_true",
        help="also check live FastAPI endpoints; start uvicorn separately first",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args()


def main() -> int:
    maybe_reexec_with_project_venv()

    args = parse_args()
    result = CheckResult()
    env = load_env()

    check_environment(result, env)
    run_offline_checks(result)

    if args.api:
        check_api(result, args.base_url, args.timeout)
    else:
        print("\n[INFO] Skipped live API checks. Start backend and rerun with --api to test endpoints.")

    print(f"\nSummary: {result.failed} failed, {result.warned} warnings")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
