"""Minimal Open WebUI (OpenAI-compatible) chat client for the DGX Spark endpoint.

Standard library only, so it runs unchanged on Windows, macOS, Linux and the DGX itself.
"""
import json
import time
import urllib.error
import urllib.request

# Bypass any system HTTP proxy: the DGX is on a private campus address (172.22.x.x) and a
# desktop proxy (VPN/Clash/corporate) would otherwise intercept and fail the request.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ModelCallError(RuntimeError):
    pass


def list_models(cfg: dict) -> list:
    req = urllib.request.Request(
        cfg["OPENWEBUI_BASE_URL"].rstrip("/") + "/api/models",
        headers={"Authorization": f"Bearer {cfg['OPENWEBUI_TOKEN']}"},
    )
    with _OPENER.open(req, timeout=float(cfg["TIMEOUT_S"])) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return [m.get("id") for m in body.get("data", [])]


def chat(cfg: dict, messages: list) -> dict:
    """Send one chat completion. Returns dict(text, latency_s, model, usage, raw)."""
    payload = {
        "model": cfg["MODEL_ID"],
        "messages": messages,
        "temperature": float(cfg["TEMPERATURE"]),
        "seed": int(cfg["SEED"]),
        "max_tokens": int(cfg["MAX_TOKENS"]),
        "stream": False,
    }
    req = urllib.request.Request(
        cfg["OPENWEBUI_BASE_URL"].rstrip("/") + "/api/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg['OPENWEBUI_TOKEN']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    start = time.perf_counter()
    try:
        with _OPENER.open(req, timeout=float(cfg["TIMEOUT_S"])) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ModelCallError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ModelCallError(f"Connection failed: {exc.reason}") from exc
    latency = time.perf_counter() - start
    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelCallError(f"Unexpected response shape: {str(body)[:500]}") from exc
    return {
        "text": text,
        "latency_s": round(latency, 2),
        "model": body.get("model", cfg["MODEL_ID"]),
        "usage": body.get("usage"),
    }
