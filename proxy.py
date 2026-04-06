#!/usr/bin/env python3
"""
LLM Proxy Logger — transparent HTTP proxy that logs all LLM API calls.

Sits between the agent and the upstream LLM gateway (e.g. Bankr).
Assigns incrementing IDs, tracks per-job folders with manifest files,
and maintains a global call log.

Usage:
    python proxy.py                           # defaults
    UPSTREAM_URL=https://llm.bankr.bot/v1 PROXY_PORT=8800 python proxy.py

Config (env vars):
    UPSTREAM_URL  — real LLM endpoint (default https://llm.bankr.bot/v1)
    PROXY_PORT    — listen port (default 8800)
    PROXY_LOG_DIR — log directory (default ./proxy_logs)
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "https://llm.bankr.bot/v1")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8800"))
LOG_DIR = os.environ.get("PROXY_LOG_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_logs"))

# (input $/1M, output $/1M, cached_input $/1M)
# Cached input prices: Anthropic = 10% of input, OpenAI/MiniMax = free
PRICING = {
    "claude-opus-4.6": (15.0, 75.0, 1.50),
    "claude-opus-4-20250514": (15.0, 75.0, 1.50),
    "claude-sonnet-4.6": (3.0, 15.0, 0.30),
    "claude-sonnet-4-20250514": (3.0, 15.0, 0.30),
    "claude-haiku-3.5": (0.80, 4.0, 0.08),
    "gpt-4o": (2.50, 10.0, 0.0),
    "gpt-4o-mini": (0.15, 0.60, 0.0),
    "minimax-m2.7": (0.50, 1.50, 0.0),
}

# Global multiplier to match your provider's actual rates.
# E.g. set to 0.5 if using batch/wholesale pricing through a provider like Bankr.
PRICING_MULTIPLIER = float(os.environ.get("PRICING_MULTIPLIER", "1.0"))

_lock = threading.Lock()


def _estimate_cost(model, input_tokens, output_tokens, cached_tokens=0):
    prices = PRICING.get(model, (5.0, 15.0, 0.50))
    fresh_input = max(input_tokens - cached_tokens, 0)
    cost = (
        (fresh_input / 1_000_000 * prices[0])
        + (cached_tokens / 1_000_000 * prices[2])
        + (output_tokens / 1_000_000 * prices[1])
    )
    return cost * PRICING_MULTIPLIER


def _read_counter(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 1


def _write_counter(path, value):
    with open(path, "w") as f:
        f.write(str(value))


def _load_manifest(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_manifest(path, manifest):
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


class ProxyHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        job_name = self.headers.get("X-Job-Name", "unknown")
        iteration = self.headers.get("X-Iteration")
        phase = self.headers.get("X-Phase")
        intent = self.headers.get("X-Intent")

        try:
            req_json = json.loads(body)
        except json.JSONDecodeError:
            req_json = {}

        model = req_json.get("model", "unknown")

        upstream_path = self.path
        upstream_url = UPSTREAM_URL.rstrip("/") + upstream_path

        fwd_headers = {}
        for key in ("Authorization", "Content-Type", "anthropic-version", "x-api-key"):
            val = self.headers.get(key)
            if val:
                fwd_headers[key] = val

        with _lock:
            os.makedirs(LOG_DIR, exist_ok=True)
            job_dir = os.path.join(LOG_DIR, job_name)
            os.makedirs(job_dir, exist_ok=True)

            global_counter = _read_counter(os.path.join(LOG_DIR, ".global_counter"))
            job_counter = _read_counter(os.path.join(job_dir, ".counter"))

            global_id = global_counter
            call_id = job_counter

            _write_counter(os.path.join(LOG_DIR, ".global_counter"), global_counter + 1)
            _write_counter(os.path.join(job_dir, ".counter"), job_counter + 1)

        req_file = os.path.join(job_dir, f"{call_id:03d}_request.json")
        with open(req_file, "w") as f:
            json.dump(req_json, f, indent=2)
            f.write("\n")

        _log(f"#{global_id} [{job_name}:{call_id}] {model} -> {upstream_url}")

        t0 = time.time()
        try:
            req = Request(upstream_url, data=body, headers=fwd_headers, method="POST")
            with urlopen(req, timeout=300) as resp:
                resp_body = resp.read()
                status_code = resp.status
                resp_headers = dict(resp.getheaders())
        except HTTPError as e:
            resp_body = e.read()
            status_code = e.code
            resp_headers = dict(e.headers)
        except (URLError, TimeoutError) as e:
            elapsed = time.time() - t0
            error_msg = json.dumps({"error": str(e)})
            _log(f"#{global_id} UPSTREAM ERROR: {e} ({elapsed:.1f}s)")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(error_msg.encode())
            return

        elapsed = time.time() - t0

        try:
            resp_json = json.loads(resp_body)
        except json.JSONDecodeError:
            resp_json = {"_raw": resp_body.decode("utf-8", errors="replace")}

        resp_file = os.path.join(job_dir, f"{call_id:03d}_response.json")
        with open(resp_file, "w") as f:
            json.dump(resp_json, f, indent=2)
            f.write("\n")

        usage = resp_json.get("usage", {})
        input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        output_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
        # Cached tokens: OpenAI format (prompt_tokens_details.cached_tokens)
        # or Anthropic format (cache_read_tokens)
        cached_tokens = (
            (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            or usage.get("cache_read_tokens", 0)
            or 0
        )
        cost = _estimate_cost(model, input_tokens, output_tokens, cached_tokens)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        meta = {}
        if iteration is not None:
            meta["iteration"] = iteration
        if phase is not None:
            meta["phase"] = phase
        if intent is not None:
            meta["intent"] = intent

        call_record = {
            "id": call_id,
            "global_id": global_id,
            "timestamp": now,
            "model": model,
            **meta,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "cost_usd": round(cost, 6),
            "elapsed_s": round(elapsed, 2),
            "status_code": status_code,
        }

        with _lock:
            manifest_path = os.path.join(job_dir, "manifest.json")
            manifest = _load_manifest(manifest_path)
            if manifest is None:
                manifest = {
                    "job_name": job_name,
                    "started_at": now,
                    "updated_at": now,
                    "total_calls": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_cached_tokens": 0,
                    "total_cost_usd": 0.0,
                    "models_used": {},
                    "calls": [],
                }

            manifest["updated_at"] = now
            manifest["total_calls"] += 1
            manifest["total_input_tokens"] += input_tokens
            manifest["total_output_tokens"] += output_tokens
            manifest["total_cached_tokens"] = manifest.get("total_cached_tokens", 0) + cached_tokens
            manifest["total_cost_usd"] = round(manifest["total_cost_usd"] + cost, 6)

            if model not in manifest["models_used"]:
                manifest["models_used"][model] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "cost_usd": 0.0,
                }
            m = manifest["models_used"][model]
            m["calls"] += 1
            m["input_tokens"] += input_tokens
            m["output_tokens"] += output_tokens
            m["cached_tokens"] = m.get("cached_tokens", 0) + cached_tokens
            m["cost_usd"] = round(m["cost_usd"] + cost, 6)

            manifest["calls"].append(call_record)
            _save_manifest(manifest_path, manifest)

            global_line = {
                "global_id": global_id,
                "job_name": job_name,
                "call_id": call_id,
                "timestamp": now,
                "model": model,
                **meta,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached_tokens,
                "cost_usd": round(cost, 6),
                "elapsed_s": round(elapsed, 2),
            }
            with open(os.path.join(LOG_DIR, "all_calls.jsonl"), "a") as f:
                f.write(json.dumps(global_line) + "\n")

        meta_tag = " ".join(f"{k}={v}" for k, v in meta.items())
        cache_tag = f" cached={cached_tokens}" if cached_tokens else ""
        _log(
            f"#{global_id} [{job_name}:{call_id}] {model} "
            f"in={input_tokens}{cache_tag} out={output_tokens} "
            f"${cost:.4f} {elapsed:.1f}s -> {status_code}"
            + (f" ({meta_tag})" if meta_tag else "")
        )

        try:
            self.send_response(status_code)
            for key in ("Content-Type",):
                val = resp_headers.get(key)
                if val:
                    self.send_header(key, val)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        except BrokenPipeError:
            _log(f"#{global_id} [{job_name}:{call_id}] client disconnected before response was sent")

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "upstream": UPSTREAM_URL}).encode())
            return

        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error": "not found"}')

    def log_message(self, format, *args):
        pass


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PROXY_PORT), ProxyHandler)
    _log(f"LLM Proxy listening on :{PROXY_PORT}")
    _log(f"Upstream: {UPSTREAM_URL}")
    _log(f"Logs: {LOG_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
