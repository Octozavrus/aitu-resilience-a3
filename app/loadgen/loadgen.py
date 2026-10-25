"""In-cluster synthetic student traffic generator (stdlib only).

Sends a fixed-rate mix of course lookups (70%) and enrollment requests (30%)
through the API Gateway Service and prints one JSON line per request:
  {"t": <unix time sent>, "op": "list|enroll", "status": 200, "ms": 12.3, "pod": "registration-..."}
status 0 = connection error / timeout (the student saw no answer at all).

Usage: python loadgen.py --url http://api-gateway --rate 20 --duration 60
"""
import argparse
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

lock = threading.Lock()


def one_request(base, op, timeout):
    if op == "list":
        req = urllib.request.Request(base + "/api/registration/courses")
    else:
        body = json.dumps({"student_id": f"S{random.randint(250000, 259999)}",
                           "section_id": random.choice(["CS-2101-01", "CS-2101-02", "SE-1203-01",
                                                        "MT-1101-03", "CY-3304-01"])}).encode()
        req = urllib.request.Request(base + "/api/registration/enroll", data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Idempotency-Key": str(uuid.uuid4())})
    t0 = time.time()
    status, pod = 0, ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, pod = r.status, r.headers.get("X-Served-By", "")
            r.read()
    except urllib.error.HTTPError as e:
        status, pod = e.code, e.headers.get("X-Served-By", "") if e.headers else ""
    except Exception:
        status = 0
    rec = {"t": round(t0, 3), "op": op, "status": status,
           "ms": round((time.time() - t0) * 1000, 1), "pod": pod}
    with lock:
        sys.stdout.write(json.dumps(rec) + "\n")
        sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://api-gateway")
    ap.add_argument("--rate", type=float, default=20.0, help="requests per second")
    ap.add_argument("--duration", type=float, default=60.0, help="seconds")
    ap.add_argument("--timeout", type=float, default=2.0, help="client timeout, seconds")
    a = ap.parse_args()
    interval = 1.0 / a.rate
    start = time.time()
    nxt = start
    threads = []
    while time.time() - start < a.duration:
        op = "list" if random.random() < 0.7 else "enroll"
        th = threading.Thread(target=one_request, args=(a.url, op, a.timeout), daemon=True)
        th.start()
        threads.append(th)
        nxt += interval
        time.sleep(max(0.0, nxt - time.time()))
    for th in threads:
        th.join(a.timeout + 1)


if __name__ == "__main__":
    main()
