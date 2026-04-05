# llm-proxy

A transparent HTTP proxy that logs all LLM API calls. Sits between an agent and an upstream LLM gateway, assigning incrementing IDs, tracking per-job folders with manifest files, and maintaining a global call log.

## Usage

```bash
python proxy.py
```

Point your LLM client at `http://localhost:8800` instead of the upstream endpoint.

## Configuration

| Env Var | Default | Description |
|---|---|---|
| `UPSTREAM_URL` | `https://llm.bankr.bot/v1` | Real LLM endpoint to proxy to |
| `PROXY_PORT` | `8800` | Port to listen on |
| `PROXY_LOG_DIR` | `./proxy_logs` | Directory for logs |

## Logs

Each job gets its own folder under `proxy_logs/<job-name>/`:

- `001_request.json`, `001_response.json`, ... — full request/response pairs
- `manifest.json` — running totals for the job (calls, tokens, cost, models used)

A global `proxy_logs/all_calls.jsonl` file records every call across all jobs.

Set the `X-Job-Name` header on requests to group calls by job.

## Cost Tracking

Estimates cost per call based on model pricing (input/output tokens). Supported models:

- `claude-opus-4.6`, `claude-sonnet-4.6`, `claude-haiku-3.5`
- `gpt-4o`, `gpt-4o-mini`
- `minimax-m2.7`

Unknown models fall back to a default rate.

## Health Check

```
GET /health
```

Returns `{"status": "ok", "upstream": "<UPSTREAM_URL>"}`.
