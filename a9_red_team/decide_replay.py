"""A9 red team, stage 2: run the target's unmodified decision path on the live replies.

For every reply logged by live_infer.py, calls src.intake_app.main() of the target worktree
(commit a7193fd, unmodified) for the frozen narrative with the target's default policy
(review-all), a named proxy reviewer and a scripted --accept. The only substitution is at
the model-service boundary: intake_app.call_model returns the logged live reply in the exact
shape its 'webui' branch returns (raw, model, latency_s, error). Parser, both gates, the
review-all policy, the reviewer-vigilance check and the durable audit writer all run as
committed. Scripted --accept shows what the system proposes and what gets recorded if a
reviewer trusts the proposal; it is not a test of a human reviewer (P5).

  python3 a9_red_team/decide_replay.py --target <worktree at a7193fd> --live runs/<live log> --label A
"""
import argparse, contextlib, io, json, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET_COMMIT = "a7193fd9567c32d0949add274aaf25d24efc1600"
REVIEWER = "rt-accept-proxy-mh"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--live", required=True)
    ap.add_argument("--cases", default=str(HERE / "cases_frozen.jsonl"))
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", default=str(HERE / "runs"))
    a = ap.parse_args()
    head = subprocess.run(["git", "-C", a.target, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", a.target, "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True).stdout.strip()
    assert head == TARGET_COMMIT and not dirty, f"target not frozen: {head} dirty={bool(dirty)}"
    sys.path.insert(0, a.target)
    import src.intake_app as app  # unmodified target module
    cases = {c["test_id"]: c for c in map(json.loads, open(a.cases, encoding="utf-8")) if c}
    lines = [json.loads(l) for l in open(a.live, encoding="utf-8") if l.strip()]
    meta, replies = lines[0], [l for l in lines[1:] if l.get("kind") == "reply"]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    audit = out / f"audit_{a.label}.jsonl"
    for p in (audit, out / "a8_control_log.jsonl"):
        assert not p.exists(), f"{p} exists: each label gets a fresh audit and control log"
    work = Path(tempfile.mkdtemp(prefix=f"a9_{a.label}_"))
    results = []
    for r in replies:
        c = cases[r["test_id"]]
        f = work / f"{r['test_id']}.txt"           # file stem becomes the case id in the audit log
        f.write_text(c["narrative"], encoding="utf-8")
        injected = dict(raw=r["raw"], model=r["model"], latency_s=r["latency_s"], error=r["error"])
        app.call_model = lambda backend, narrative, args, _x=injected: dict(_x)
        so, se = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            try:
                code = app.main(["--file", str(f), "--backend", "webui", "--reviewer", REVIEWER,
                                 "--accept", "--log", str(audit)])
            except SystemExit as exc:
                code = exc.code
            except Exception as exc:  # a crash is a result, not a harness failure
                code = f"CRASH {type(exc).__name__}: {exc}"
        results.append({"test_id": r["test_id"], "label": a.label, "exit": code,
                        "stdout_tail": so.getvalue()[-1500:], "stderr": se.getvalue()[-1500:]})
        print(r["test_id"], "exit", code, flush=True)
    # control log sits next to the audit log (intake_app rule); rename it per label
    cl = out / "a8_control_log.jsonl"
    if cl.exists():
        cl.rename(out / f"a8_control_log_{a.label}.jsonl")
    (out / f"exits_{a.label}.json").write_text(json.dumps({"live_log": Path(a.live).name, "live_meta": meta,
                                                           "results": results}, indent=1, ensure_ascii=False), encoding="utf-8")
    print("AUDIT", audit)


if __name__ == "__main__":
    main()
