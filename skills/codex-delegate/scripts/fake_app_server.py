#!/usr/bin/env python3
"""A scripted stand-in for `codex app-server`, for tests only.

Reads the scenario from the JSON file named by FAKE_APP_SERVER_CFG:
  {"record": "<jsonl file>", "requests": [{"method": ..., "params": {...}}, ...]}
On turn/start it sends each approval request in turn, waiting for the reply
to one before sending the next, then completes the turn with one agentMessage.
Every message it receives is appended to `record`, so a test can assert what
the dispatcher actually answered on the wire.
"""
import json
import os
import sys
from pathlib import Path

cfg = json.loads(Path(os.environ["FAKE_APP_SERVER_CFG"]).read_text(encoding="utf-8"))
record = Path(cfg["record"])
requests = list(cfg.get("requests", []))
# UUID-shaped like a real Codex thread id: --ask refuses anything else.
THREAD = cfg.get("thread_id", "0aaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee1")
TURN = "turn-fake-1"


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def note(obj):
    with record.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj) + "\n")


# Which home and cwd the dispatcher really started us with.
note({"in": {"_env": {"CODEX_HOME": os.environ.get("CODEX_HOME"), "cwd": os.getcwd()}}})
pending = None          # id of the approval request awaiting a reply
turn_text = ""
next_id = 1000


def send_next_or_finish():
    global pending, next_id
    if requests:
        req = requests.pop(0)
        params = {"threadId": THREAD, "turnId": TURN, **req["params"]}
        next_id += 1
        pending = next_id
        out({"id": pending, "method": req["method"], "params": params})
        return
    pending = None
    text = cfg.get("answer_prefix", "done") + (f": {turn_text}" if cfg.get("echo") else "")
    out({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": text}}})
    out({"method": "turn/completed", "params": {"turn": {"id": TURN, "status": "completed"}}})


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    note({"in": msg})
    method, rid = msg.get("method"), msg.get("id")
    if method == "initialize":
        out({"id": rid, "result": {"userAgent": "fake"}})
    elif method == "thread/start":
        out({"id": rid, "result": {"thread": {"id": THREAD}}})
    elif method == "thread/resume":
        if msg["params"].get("threadId") != THREAD:
            out({"id": rid, "error": {"code": -32600, "message": "no rollout found for thread id"}})
        else:
            out({"id": rid, "result": {"thread": {"id": THREAD}}})
    elif method == "turn/start":
        turn_text = " ".join(i.get("text", "") for i in msg["params"].get("input", []))
        out({"id": rid, "result": {"turn": {"id": TURN, "status": "inProgress"}}})
        send_next_or_finish()
    elif method == "turn/steer":
        out({"id": rid, "result": {"turnId": TURN}})
    elif rid is not None and rid == pending and method is None:
        send_next_or_finish()
