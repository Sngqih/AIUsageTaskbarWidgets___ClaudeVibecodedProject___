"""Stands in for `codex` in tests: implements just enough of `codex app-server`.

Behaviour is picked with FAKE_CODEX_MODE:
  ok        normal answer (camelCase, like the real server)
  empty1    first rateLimits answer is empty, second has data
  error     JSON-RPC error for the rate-limit request
  hang      never answers
  apikey    answers with no limits (API-key login)
"""

import json
import os
import sys
import time

mode = os.environ.get("FAKE_CODEX_MODE", "ok")
if sys.argv[1:] != ["app-server"]:
    sys.exit(2)

now = int(time.time())
reads = 0
initialized = False
for line in sys.stdin:
    msg = json.loads(line)
    m, mid = msg.get("method"), msg.get("id")
    if mode == "hang":
        continue
    if m == "initialize":
        out = {"id": mid, "result": {"userAgent": "fake/0.1"}}
    elif m == "initialized":
        initialized = True
        print(json.dumps({"method": "account/updated", "params": {"authMode": "chatgpt"}}), flush=True)
        continue
    elif m == "account/rateLimits/read":
        assert initialized, "rate limits requested before 'initialized'"
        reads += 1
        if mode == "error":
            out = {"id": mid, "error": {"code": -32600, "message": "not logged in"}}
        elif mode == "apikey" or (mode == "empty1" and reads == 1):
            out = {"id": mid, "result": {"rateLimits": {"primary": None, "secondary": None}}}
        else:
            out = {"id": mid, "result": {"rateLimits": {
                "primary": {"usedPercent": 37, "windowDurationMins": 300, "resetsAt": now + 4000},
                "secondary": {"usedPercent": 64, "windowDurationMins": 10080, "resetsAt": now + 200000},
                "planType": "plus"}}}
    else:
        out = {"id": mid, "error": {"message": "unknown"}}
    print(json.dumps(out), flush=True)
