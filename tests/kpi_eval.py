import json
import os
import time
from pathlib import Path

import httpx

API = os.getenv("API_URL", "http://127.0.0.1:8000")
TOKEN = os.getenv("API_TOKEN", "")
HEAD = {"Authorization": f"Bearer {TOKEN}"}

items = json.loads((Path(__file__).parent / "eval_set.json").read_text(encoding="utf-8"))
with httpx.Client(base_url=API, timeout=30) as c:
    sid = c.post("/api/v1/auth", json={"token": TOKEN}).json()["session_id"]
    understood = correct = fast = auto = 0
    in_scenarios = answered_ok = 0
    for i, it in enumerate(items):
        t0 = time.monotonic()
        r = c.post("/api/v1/chat/messages", headers=HEAD,
                   json={"session_id": sid, "text": it["text"], "client_message_id": f"eval-{i}"}).json()
        took = time.monotonic() - t0
        got_operator = r["status"] != "answered"
        want_operator = it["expect"] == "operator"
        ok = got_operator == want_operator
        understood += ok
        fast += took <= 5
        if not want_operator:
            in_scenarios += 1
            if r["status"] == "answered":
                answered_ok += 1
                needed = it.get("contains", [])
                correct += (not needed) or any(s.lower() in r["answer"].lower() for s in needed)
        print(("OK  " if ok else "FAIL"), f"{took:.2f}s", it["text"], "->", r["status"])
    n = len(items)
    print()
    print(f"Определение обращения (цель >=90%): {understood / n:.0%}")
    print(f"Корректность автоответов (цель >=90%): {correct / max(answered_ok, 1):.0%}")
    print(f"Скорость <=5 с (цель >=95%): {fast / n:.0%}")
    print(f"Автоматизация по сценариям (цель >=80%): {answered_ok / max(in_scenarios, 1):.0%}")
