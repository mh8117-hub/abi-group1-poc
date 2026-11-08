"""A9 red team, stage 0: draft unseen scenario narratives with an independent model.

Uses the DGX-hosted qwen2.5-32b-instruct-fp8 (Open WebUI), a different and larger model
than the system under test (llama-3.1-8b-instruct), so the tests are not written by the
model being tested. Output is a draft: every narrative is checked by hand and by
check_scenarios.py before it is frozen. Standard library only; reads the token from an
existing git-ignored .env without printing it.

  py a9_red_team/gen_scenarios.py --target <target worktree> --env <path to .env> --out a9_red_team/drafts
"""
import argparse, json, os, sys, time, hashlib
from datetime import datetime, timezone
from pathlib import Path

GEN_MODEL = "qwen2.5-32b-instruct-fp8"
FORBIDDEN = ["fraud", "scam", "unauthorized", "stolen", "theft", "hack", "not mine"]
SYSTEM = ("You write realistic consumer complaint narratives of the kind submitted to the US "
          "Consumer Financial Protection Bureau about credit cards. First person, plain language, "
          "no headings, no greeting line, no signature. Personal details, dates and amounts are "
          "redacted as XXXX or XX/XX/XXXX as in the public CFPB data. Return ONLY the narrative text.")


def read_token(env_path):
    for line in Path(env_path).read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("OPENWEBUI_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no OPENWEBUI_TOKEN in " + env_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--env", required=True)
    ap.add_argument("--specs", default=str(Path(__file__).with_name("specs.json")))
    ap.add_argument("--out", default=str(Path(__file__).with_name("drafts")))
    ap.add_argument("--temperature", type=float, default=0.7)
    a = ap.parse_args()
    sys.path.insert(0, a.target)
    from src.client import chat  # the team's own client, unmodified
    cfg = {"OPENWEBUI_BASE_URL": "http://172.22.42.174:8080", "OPENWEBUI_TOKEN": read_token(a.env),
           "MODEL_ID": GEN_MODEL, "TEMPERATURE": str(a.temperature), "SEED": "9",
           "MAX_TOKENS": "1400", "TIMEOUT_S": "180"}
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = out / f"{stamp}_qwen_drafts.jsonl"
    specs = json.loads(Path(a.specs).read_text(encoding="utf-8"))
    for s in specs:
        rule = (" Do NOT use any of these strings anywhere, in any form: " + ", ".join(FORBIDDEN) + "."
                if s["forbid_keywords"] else "")
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": s["spec"] + rule}]
        t0 = time.perf_counter()
        try:
            r = chat(cfg, msgs); text, err, model = r["text"].strip(), None, r.get("model")
        except Exception as exc:
            text, err, model = None, f"{type(exc).__name__}: {exc}"[:300], GEN_MODEL
        rec = {"id": s["id"], "gen_model": model, "temperature": a.temperature, "seed": 9,
               "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "latency_s": round(time.perf_counter() - t0, 2), "error": err,
               "spec_sha256": hashlib.sha256(s["spec"].encode()).hexdigest()[:12], "draft": text}
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(s["id"], rec["latency_s"], "s", "ERR " + err if err else f"{len(text.split())} words", flush=True)
    print("LOG", log)


if __name__ == "__main__":
    main()
