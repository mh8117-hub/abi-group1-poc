"""DGX smoke test: run a few guarded Development cases through the real model path.

Proves on the target environment that the data guard sits in front of the existing client:
  dev CSV -> data_guard (schema, held-out refusal + overlap exclusion, scope)
          -> build_model_input / assert_input_boundary (narrative only)
          -> prompt baseline-v1 -> Open WebUI on DGX Spark -> parser
and logs, per case, the payload keys actually sent, a hash of the user message, and the
model's review flag against BOTH the course review label and the intake-time reference.

Usage (repo root, NYU network/VPN, .env with OPENWEBUI_TOKEN):
  python -m src.dgx_guard_smoke --dev PATH/ABI_Bank_Complaints_Development_8000.csv
"""
import argparse
import hashlib
import json
import random
from datetime import datetime, timezone

from src import data_guard as g
from src.client import ModelCallError, chat
from src.config import REPO_ROOT, load_config
from src.parser import parse_model_output
from src.prompt import PROMPT_VERSION, build_messages
from src.run_baseline import git_commit


def pick(scoped, per_group, seed):
    rng = random.Random(seed)
    ann = [dict(r, **g.annotate(r)) for r in scoped]
    groups = {
        "A_fraudtext_billing_owner": [a for a in ann if a["stratum"].startswith("A")],
        "B_fraudtext_fraud_owner": [a for a in ann if a["stratum"].startswith("B")],
        "C_review_post_outcome_only": [a for a in ann if a["review_post_outcome_only"] == "Yes"],
        "C_clean_review_no": [a for a in ann if a["stratum"].startswith("C")
                              and a["human_review_required"] == "No"],
    }
    out = []
    for name, members in groups.items():
        members.sort(key=lambda a: int(a["case_id"]))
        out += [dict(m, smoke_group=name) for m in rng.sample(members, per_group)]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", required=True)
    ap.add_argument("--per-group", type=int, default=2)
    ap.add_argument("--seed", type=int, default=5)
    a = ap.parse_args()

    rows, log = g.load_development(a.dev, g.load_heldout_hashes())
    cases = pick(g.in_scope(rows), a.per_group, a.seed)
    cfg = load_config()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPO_ROOT / "runs" / f"{stamp}_a5_guard_smoke.jsonl"
    meta = {"run_id": stamp, "git_commit": git_commit(), "model": cfg["MODEL_ID"],
            "endpoint": cfg["OPENWEBUI_BASE_URL"] + "/api/chat/completions",
            "prompt_version": PROMPT_VERSION, "temperature": float(cfg["TEMPERATURE"]),
            "seed": int(cfg["SEED"]), "excluded_heldout_overlap": log["excluded_heldout_overlap"]}
    print(json.dumps(meta, indent=2))
    with open(path, "w", encoding="utf-8") as out:
        out.write(json.dumps({"meta": meta}) + "\n")
        for i, c in enumerate(cases, 1):
            payload = g.build_model_input(c)
            g.assert_input_boundary(payload, c)
            msgs = build_messages(payload["consumer_complaint_narrative"])
            rec = {"case_id": c["case_id"], "smoke_group": c["smoke_group"],
                   "payload_keys": sorted(payload),
                   "user_msg_sha256": hashlib.sha256(msgs[-1]["content"].encode()).hexdigest()[:16],
                   "ref_routing": c["routing_destination"],
                   "ref_review": c["human_review_required"],
                   "ref_review_intake": c["review_ref_intake"]}
            try:
                resp = chat(cfg, msgs)
                p = parse_model_output(resp["text"])
                r = p.record or {}
                rec.update(parse_status=p.status, latency_s=resp["latency_s"],
                           model_routing=r.get("recommended_routing"),
                           model_review=r.get("human_review_required"),
                           model_fraud=r.get("fraud_or_security_concern"), raw_text=resp["text"])
            except ModelCallError as exc:
                rec.update(parse_status="call_error", error=str(exc))
                out.write(json.dumps(rec) + "\n")
                print(f"call error, stopping: {exc}")
                break
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            print(f"[{i}/{len(cases)}] {c['case_id']:>8} {c['smoke_group'][:26]:<26} "
                  f"{rec['parse_status']:<8} {rec['latency_s']:>5}s keys={rec['payload_keys']} "
                  f"route={rec['model_routing']} (ref {c['routing_destination']}) "
                  f"review={rec['model_review']} (ref {c['human_review_required']}, "
                  f"intake {c['review_ref_intake']})")
    print(f"evidence -> {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
