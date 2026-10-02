"""Convert a baseline run log (runs/<id>_baseline.jsonl) into an Open WebUI chat-import file.

Each API call becomes one chat: the baseline system prompt (in the chat's system params),
the complaint exactly as sent, and the model's raw reply exactly as the DGX returned it,
with the server-reported token usage. Nothing is edited or regenerated. Titles are marked
"(imported API log)" so a reader can tell these were recorded via the API, not typed in the UI.

  python -m src.export_openwebui runs/20261002T151238Z_baseline.jsonl
  -> runs/20261002T151238Z_openwebui_import.json  (Settings > Data > Import chats)
"""
import csv
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.config import REPO_ROOT
from src.prompt import SYSTEM_PROMPT, build_messages


def main():
    run_path = Path(sys.argv[1])
    run_id = run_path.name.split("_")[0]
    summary = json.loads((run_path.parent / f"{run_id}_summary.json").read_text(encoding="utf-8"))
    cases_file = REPO_ROOT / summary["cases_file"].replace("\\", "/")
    with open(cases_file, encoding="utf-8-sig", newline="") as fh:
        narratives = {r["case_id"]: r["consumer_complaint_narrative"] for r in csv.DictReader(fh)}

    start = datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
    clock = start
    chats = []
    for line in run_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if "raw_text" not in rec:
            continue
        model = rec.get("model_returned") or summary["model_requested"]
        user_text = build_messages(narratives[rec["case_id"]])[1]["content"]
        t_user = int(clock)
        clock += rec["latency_s"]
        t_reply = int(clock)
        uid, aid, cid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        user_msg = {"id": uid, "parentId": None, "childrenIds": [aid], "role": "user",
                    "content": user_text, "timestamp": t_user, "models": [model]}
        bot_msg = {"id": aid, "parentId": uid, "childrenIds": [], "role": "assistant",
                   "content": rec["raw_text"], "model": model, "modelName": model,
                   "timestamp": t_reply, "done": True, "usage": rec.get("usage") or {}}
        title = (f"A4 API run {run_id} | case {rec['case_id']} "
                 f"({rec.get('sample_group', '')[:1]}) (imported API log, commit {summary['git_commit']})")
        chat = {
            "id": cid, "title": title, "models": [model],
            "params": {"system": SYSTEM_PROMPT, "temperature": summary["temperature"],
                       "seed": summary["seed"], "max_tokens": summary["max_tokens"]},
            "history": {"messages": {uid: user_msg, aid: bot_msg}, "currentId": aid},
            "messages": [user_msg, bot_msg], "tags": [], "timestamp": t_user * 1000,
        }
        chats.append({"id": cid, "title": title, "chat": chat, "meta": {"tags": []},
                      "created_at": t_user, "updated_at": t_reply,
                      "archived": False, "pinned": False, "share_id": None, "folder_id": None})

    out = run_path.parent / f"{run_id}_openwebui_import.json"
    out.write_text(json.dumps(chats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(chats)} chats -> {out}")


if __name__ == "__main__":
    main()
