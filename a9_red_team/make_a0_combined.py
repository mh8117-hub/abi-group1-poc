"""Join run A0's metadata line (renamed *_ABORTED_no_replies.jsonl) to the 12 replies the still-running
process appended under the original file name. Replies are copied unedited."""
import json
from pathlib import Path

R = Path(__file__).resolve().parent / "runs"
meta = json.loads((R / "20261108T220659Z_live_A_ABORTED_no_replies.jsonl").read_text(encoding="utf-8").splitlines()[0])
meta["run"] = "A0"
meta["note"] = ("First launch of run A (22:06:59Z). It looked dead after its launcher returned, its metadata file was renamed "
                "*_ABORTED_no_replies.jsonl, but the process kept running and appended its 12 replies to the original file name. "
                "It overlapped run A (22:08Z) in time, so the service received two concurrent request streams from this laptop. "
                "Replies are unedited; this file only joins the metadata line to them.")
reps = [json.loads(l) for l in (R / "20261108T220659Z_live_A_llama-3.1-8b-instruct.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
with open(R / "20261108T220659Z_live_A0_combined.jsonl", "w", encoding="utf-8", newline="\n") as fh:
    for rec in [meta] + reps:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
print(len(reps), "replies")
