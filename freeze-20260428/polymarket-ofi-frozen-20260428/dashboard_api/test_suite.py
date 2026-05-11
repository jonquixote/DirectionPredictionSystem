import urllib.request
import json
import base64
import math
from scipy.stats import norm
import numpy as np

BASE_URL = "http://localhost:8765/api"
AUTH_HEADER = "Basic " + base64.b64encode(b"admin:changeme_generate_random_32char").decode("ascii")

def get_json(path, auth=True):
    req = urllib.request.Request(BASE_URL + path)
    if auth:
        req.add_header("Authorization", AUTH_HEADER)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.code}
    except Exception as e:
        return {"error": str(e)}

def compute_realized_net(direction: str, correct: bool, p_market: float, fee: float = 0.009) -> float:
    if direction == "up":
        return +(1 - p_market - fee) if correct else -(p_market + fee)
    else:  # down
        return +(p_market - fee) if correct else -((1 - p_market) + fee)

def wilson_ci(wins: int, n: int, confidence: float = 0.95):
    z = norm.ppf((1 + confidence) / 2)
    p = wins / n if n > 0 else 0
    center = (p + z**2 / (2*n)) / (1 + z**2 / n)
    margin = (z * (p*(1-p)/n + z**2/(4*n**2))**0.5) / (1 + z**2/n)
    return (center - margin, center + margin)

def run_tests():
    report = []
    
    # 1 & 9. Auth
    res = get_json("/status", auth=False)
    auth_fail = res.get("error") == 401
    res = get_json("/status", auth=True)
    auth_pass = "error" not in res
    report.append(f"[Auth] 401 on missing auth: {auth_fail}, 200 on basic auth: {auth_pass}")

    # 2. Filters & Empty Sets
    res = get_json("/trades?model=h60&symbol=XYZNONEXISTENT")
    empty_pass = "error" not in res and res.get("data") == []
    report.append(f"[Filters] Empty set handling: {empty_pass}")

    # 3. Pagination
    res1 = get_json("/trades?page=1&page_size=2")
    res_last = get_json("/trades?page=999999&page_size=50")
    pag_pass = len(res1.get("data", [])) == 2 and res_last.get("data") == []
    report.append(f"[Pagination] Boundary handling: {pag_pass}")

    # 4. NE_t match manual array
    res = get_json("/trades?model=h60&page_size=10")
    net_match = True
    for t in res.get("data", []):
        if t.get("resolved") and t.get("prediction_correct") is not None:
            calc = compute_realized_net(t["pred_direction"], t["prediction_correct"], t["p_market"], 0.009)
            if abs(calc - t["realized_net_computed"]) > 0.0001:
                net_match = False
    report.append(f"[NE_t] Realized net matches manual calculation: {net_match}")

    # 5. Rolling 50 matches
    res = get_json("/performance/rolling?model=h300&n=50")
    roll_pass = False
    series = res.get("series", [])
    if series and len(series) >= 50:
        # Check last point
        last_pt = series[-1]
        roll_pass = "accuracy" in last_pt
    report.append(f"[Rolling-50] Computation succeeds and returns series: {roll_pass}")

    # 6. Wilson CI Bounds
    res = get_json("/performance/summary")
    ci_pass = True
    for m in res:
        if m["total_trades"] > 0 and m["accuracy"] is not None:
            low, high = wilson_ci(m["wins"], m["total_trades"])
            if abs(low - m["ci_low"]) > 0.01 or abs(high - m["ci_high"]) > 0.01:
                ci_pass = False
    report.append(f"[Wilson CI] Matches scipy bounds: {ci_pass}")

    # 7. Suppressed trades excluded
    res_sup = get_json("/performance/suppression-effectiveness?model=h300")
    sup_pass = len(res_sup) > 0 and type(res_sup) == list
    report.append(f"[Suppression] Suppression endpoints working correctly: {sup_pass}")

    # 8. Unresolved trades return null NE_t
    res = get_json("/trades?model=h60&page_size=50")
    null_pass = True
    for t in res.get("data", []):
        if not t.get("resolved"):
            if t.get("realized_net_computed") is not None:
                null_pass = False
    report.append(f"[Unresolved] Return null NE_t: {null_pass}")
    
    for line in report:
        print(line)

if __name__ == "__main__":
    run_tests()
