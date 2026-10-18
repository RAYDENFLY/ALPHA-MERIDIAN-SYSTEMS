import urllib.request
import json

url = "http://127.0.0.1:8000/api/model_b/setups?limit=20"
req = urllib.request.urlopen(url)
data = json.loads(req.read().decode())
print("Total candidates returned:", data["count"])
for s in data["setups"]:
    print(f"CID: {s['candidate_id']} | Status: {s['status']} | ExecState: {s.get('execution_state')} | OrderId: {s.get('order_id')} | Qty: {s.get('quantity')}")
    if s.get("not_submitted_reason"):
        print(f"   -> NotSubmittedReason: {s.get('not_submitted_reason')}")
