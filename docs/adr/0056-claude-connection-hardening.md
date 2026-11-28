# ADR-056: Claude calls that fail with "Connection error."

## Context
Production showed `Failed: Plan: find a trending product and launch it — APIConnectionError: Connection error.`
The SDK's message hides the real cause. Reproduced locally, the most common cause is a key pasted into a dashboard with a trailing newline, space or quotes. The HTTP header then becomes illegal (`httpx LocalProtocolError: Illegal header value`), and the SDK reports only "Connection error." Other causes that produce the same text:
- a malformed `ANTHROPIC_BASE_URL`
- proxy variables
- DNS or TLS failures
- a connection reset during a long call

## Decision
`models_ai/anthropic_provider.py`:
- **Key:** cleaned of surrounding whitespace and quotes before use.
- **Base URL:** `ANTHROPIC_BASE_URL` is used only if it is a real `https://host` URL. Otherwise the client is pinned to `https://api.anthropic.com` (the SDK would otherwise fall back to the bad env value).
- **Timeouts:** 20 s to connect (the SDK default is 5 s) and 10 min to read. The SDK retries 3 times (was 1); the worker's durable retries sit on top.
- **Errors:**
  - An illegal header is a permanent, plain error: "Re-paste the key in Railway".
  - Any other connection failure raises `ModelUnreachable` (retryable). Its message names the real cause chain (e.g. `ConnectError: [Errno -3] Temporary failure in name resolution`), with every form of the key scrubbed out.
- **Startup log:** the worker logs the connection settings at startup, with no secret values: key set, stray whitespace, `sk-ant-` shape, base URL host, ignored base URL, `ANTHROPIC_AUTH_TOKEN` also set, and proxy variable names.
- **Test button:** Console → Connections → Claude → Test now uses the same key cleaning and base URL as the worker. On failure it shows the cause and these settings.
- **Scrubbing:** `connections.scrub` also scrubs trimmed and escaped forms of every secret.

## How to verify in production
1. After deploy, press Test on Claude in Connections. A pass with `models_available` > 0 means the brain can reach Claude.
2. If it fails, the message names the cause. Fix the one variable it points to: re-paste `ANTHROPIC_API_KEY`, or delete a bad `ANTHROPIC_BASE_URL` or proxy variable.
3. Retry the failed plan from the run page.
