"""A9 red team, stage 1: live inference for the frozen tests against the identified target.

Reproduces exactly the 'webui' backend of src.intake_app.call_model at the target commit:
the target's own prompt builder (src.a6_prompts.build_messages, prompted-v1) and its own
client (src.client.chat), imported unmodified from the target worktree, with the target's
default settings (llama-3.1-8b-instruct, temperature 0, seed 42, max_tokens 600, timeout 60 s).
The replies are saved unedited; stage 2 (decide_replay.py) feeds them through the target's
unmodified decision path.

Why two stages: the target's durable audit writer is POSIX-only (fcntl, O_DIRECTORY), so
src.intake_app cannot complete on the Windows laptop that holds the NYU VPN connection, and
the Linux environment that can run it cannot reach the DGX model service. Inference itself
runs on the DGX-hosted Open WebUI service in both the team's runs and this one.

  py a9_red_team/live_infer.py --target <worktree at a7193fd> --env <.env with token> --run A
"""
import argparse, hashlib, json, os, platform, socket, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET_COMMIT = "a7193fd9567c32d0949add274aaf25d24efc1600"
TARGET_FILES = ["src/intake_app.py", "src/a6_prompts.py", "src/client.py", "src/config.py",
                "src/parser.py", "src/authority_gate.py", "src/data_guard.py",
                "src/reviewer_vigilance.py", "candidates/yufei/a7/authority_gate.py",
                "candidates/yufei/a8/durable_audit.py"]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_token(env_path):
    for line in Path(env_path).read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("OPENWEBUI_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no OPENWEBUI_TOKEN in " + env_path)


def verify_target(target):
    head = subprocess.run(["git", "-C", target, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", target, "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True).stdout.strip()
    if head != TARGET_COMMIT or dirty:
        raise SystemExit(f"target is not the frozen version: HEAD={head} dirty={bool(dirty)}")
    return {"commit": head, "clean": True, "file_sha256": {f: sha(Path(target) / f) for f in TARGET_FILES}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--env", required=True)
    ap.add_argument("--cases", default=str(HERE / "cases_frozen.jsonl"))
    ap.add_argument("--run", required=True, help="run label, e.g. A or B")
    ap.add_argument("--model", default="llama-3.1-8b-instruct",
                    help="target model; anything else is a labelled diagnostic, not the target")
    ap.add_argument("--only", nargs="*", help="test ids (diagnostic runs only)")
    ap.add_argument("--out", default=str(HERE / "runs"))
    a = ap.parse_args()
    target_info = verify_target(a.target)
    sys.path.insert(0, a.target)
    from src import a6_prompts
    from src.client import chat
    from src.config import DEFAULTS
    os.environ["OPENWEBUI_TOKEN"] = read_token(a.env)  # never printed or logged
    cfg = dict(DEFAULTS, OPENWEBUI_TOKEN=os.environ["OPENWEBUI_TOKEN"], MODEL_ID=a.model)
    cfg["TIMEOUT_S"] = "60"  # intake_app --timeout default
    settings = {k: cfg[k] for k in ("OPENWEBUI_BASE_URL", "MODEL_ID", "TEMPERATURE", "SEED", "MAX_TOKENS", "TIMEOUT_S")}
    cases_sha = sha(a.cases)
    cases = [json.loads(l) for l in open(a.cases, encoding="utf-8") if l.strip()]
    if a.only:
        cases = [c for c in cases if c["test_id"] in a.only]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    role = "target" if a.model == "llama-3.1-8b-instruct" and not a.only else "diagnostic"
    log = out / f"{stamp}_live_{a.run}_{a.model}.jsonl"
    meta = {"kind": "run_meta", "run": a.run, "role": role, "utc": stamp, "host": socket.gethostname(),
            "platform": platform.platform(), "python": platform.python_version(),
            "target": target_info, "prompt_version": "prompted-v1",
            "prompt_sha256": a6_prompts.prompt_sha256("prompted-v1"), "settings": settings,
            "cases_file_sha256": cases_sha, "n_cases": len(cases)}
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
    for c in cases:
        messages = a6_prompts.build_messages("prompted-v1", c["narrative"])
        t0 = time.perf_counter()
        try:  # mirrors intake_app.call_model, backend 'webui'
            r = chat(cfg, messages)
            rec = dict(raw=r["text"], model=f"webui:{r.get('model') or cfg['MODEL_ID']}",
                       latency_s=round(time.perf_counter() - t0, 2), error=None, usage=r.get("usage"))
        except Exception as exc:
            rec = dict(raw=None, model="webui", latency_s=round(time.perf_counter() - t0, 2),
                       error=f"{type(exc).__name__}: {exc}"[:300], usage=None)
        rec.update(kind="reply", run=a.run, role=role, test_id=c["test_id"],
                   utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   narrative_sha256=hashlib.sha256(c["narrative"].encode("utf-8")).hexdigest(),
                   messages_sha256=hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest())
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(c["test_id"], rec["latency_s"], "s", rec["error"] or "ok", flush=True)
    print("LOG", log)


if __name__ == "__main__":
    main()
