"""Part D - automated failure-injection experiments on a real Kubernetes cluster.

Experiments (all under continuous in-cluster student traffic through the API Gateway):
  1  one Registration replica crashes (process exits -> kubelet restarts the container)
  2  all Registration replicas are lost at once (both pods force-deleted -> ReplicaSet re-creates)
  3  database unavailable for HOLD seconds (StatefulSet scaled to 0, then back to 1)
  4  (bonus) one replica hangs (liveness endpoint stops answering -> liveness restart)

Measured, not estimated:
  detection_s  injection -> Kubernetes marks capacity lost (Ready pods drop below 2)
  recovery_s   injection -> 2 Ready pods again (for exp 3 also: DB restore -> 2 Ready)
  user impact  from every request the load generator sent: failures, success %,
               error window, latency percentiles, how fast failures were returned

Outputs: results/experiments.json, results/raw/exp<N>_{requests,timeline}.jsonl,
         results/experiment_log.txt
Run:     python scripts/experiments.py            (all)
         python scripts/experiments.py --only 1 3 (subset)
"""
import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
RAW = RES / "raw"
NS = "aitu-a3"
SEL = "app=registration-service"
LOADGEN_POD = "student-traffic"
EXPECTED = 2
LOG_LINES = []


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}"
    print(line, flush=True)
    LOG_LINES.append(line)


def kubectl(*args, check=True, timeout=60):
    p = subprocess.run(["kubectl", "-n", NS, *args], capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def snapshot_text(title):
    out = kubectl("get", "pods", "-o", "wide", check=False)
    LOG_LINES.append(f"\n--- {title} ---\n{out.rstrip()}")


def reg_pods():
    data = json.loads(kubectl("get", "pods", "-l", SEL, "-o", "json"))
    pods = []
    for p in data["items"]:
        conds = {c["type"]: c["status"] for c in p["status"].get("conditions", [])}
        cs = (p["status"].get("containerStatuses") or [{}])[0]
        pods.append({"name": p["metadata"]["name"], "uid": p["metadata"]["uid"],
                     "ready": conds.get("Ready") == "True" and "deletionTimestamp" not in p["metadata"],
                     "terminating": "deletionTimestamp" in p["metadata"],
                     "restarts": cs.get("restartCount", 0), "phase": p["status"].get("phase")})
    return pods


class Monitor(threading.Thread):
    """Polls registration pod state as fast as kubectl allows (~0.2-0.4 s resolution)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.samples, self.stop_flag = [], False

    def run(self):
        while not self.stop_flag:
            try:
                pods = reg_pods()
                self.samples.append({"t": time.time(), "ready": sum(p["ready"] for p in pods),
                                     "pods": pods})
            except Exception as e:  # API hiccup - keep going
                self.samples.append({"t": time.time(), "ready": None, "error": str(e)[:100]})
            time.sleep(0.1)


def clock_offset():
    """Pod clock minus host clock (seconds), so loadgen timestamps line up with injections."""
    t0 = time.time()
    pod_t = float(kubectl("exec", LOADGEN_POD, "--", "python", "-c",
                          "import time;print(time.time())").strip())
    t1 = time.time()
    return pod_t - (t0 + t1) / 2


def wait_ready(n=EXPECTED, timeout=180, stable=3.0):
    end, since = time.time() + timeout, None
    while time.time() < end:
        if sum(p["ready"] for p in reg_pods()) >= n:
            since = since or time.time()
            if time.time() - since >= stable:
                return True
        else:
            since = None
        time.sleep(0.5)
    return False


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(round(q / 100 * (len(v) - 1))))], 1)


def analyse(exp, reqs, samples, t_inject, t_restore, restarts_before, names_before):
    after = [s for s in samples if s["t"] >= t_inject and s.get("ready") is not None]
    det = next((s for s in after if s["ready"] < EXPECTED), None)
    rec = None
    if det:
        rec = next((s for s in after if s["t"] > det["t"] and s["ready"] >= EXPECTED), None)
    fails = [r for r in reqs if r["status"] == 0 or r["status"] >= 500]
    ok = [r for r in reqs if 0 < r["status"] < 500]
    first_fail_after = next((r for r in sorted(fails, key=lambda r: r["t"]) if r["t"] >= t_inject - 1),
                            None)
    final = reg_pods()
    res = {
        "experiment": exp["id"], "name": exp["name"], "injection": exp["how"],
        "detection_s": round(det["t"] - t_inject, 2) if det else None,
        "detection_signal": "Ready registration pods < 2 (pod Ready condition / EndpointSlice)",
        "first_failed_request_after_injection_s": (round(first_fail_after["t"] - t_inject, 2)
                                                    if first_fail_after else None),
        "recovery_s": round(rec["t"] - t_inject, 2) if rec else None,
        "recovery_after_restore_s": (round(rec["t"] - t_restore, 2) if rec and t_restore else None),
        "min_ready_pods": min((s["ready"] for s in after), default=None),
        "requests_total": len(reqs), "requests_failed": len(fails),
        "success_percent": round(100 * len(ok) / len(reqs), 2) if reqs else None,
        "error_window_s": (round(max(r["t"] for r in fails) - min(r["t"] for r in fails), 2)
                           if fails else 0.0),
        "failed_by_status": {str(s): sum(1 for r in fails if r["status"] == s)
                             for s in sorted({r["status"] for r in fails})},
        "p50_ms_ok": pct([r["ms"] for r in ok], 50), "p95_ms_ok": pct([r["ms"] for r in ok], 95),
        "p99_ms_ok": pct([r["ms"] for r in ok], 99),
        "p95_ms_failed": pct([r["ms"] for r in fails], 95),
        "ok_over_500ms": sum(1 for r in ok if r["ms"] > 500),
        "served_by": {p: sum(1 for r in ok if r["pod"] == p) for p in sorted({r["pod"] for r in ok})},
        "restarts_before": restarts_before,
        "restarts_after": sum(p["restarts"] for p in final),
        "pods_before": names_before, "pods_after": [p["name"] for p in final],
        "pods_recreated": sorted(set(p["name"] for p in final) - set(names_before)),
    }
    return res


def run_experiment(exp, rate, duration, inject_at, offset):
    log(f"=== Experiment {exp['id']}: {exp['name']} ===")
    if not wait_ready():
        raise RuntimeError("registration pods not Ready before experiment")
    pods = reg_pods()
    names_before = [p["name"] for p in pods]
    restarts_before = sum(p["restarts"] for p in pods)
    snapshot_text(f"Exp {exp['id']} BEFORE")

    mon = Monitor()
    mon.start()
    lg = subprocess.Popen(["kubectl", "-n", NS, "exec", LOADGEN_POD, "--", "python",
                           "/opt/loadgen/loadgen.py", "--url", "http://api-gateway",
                           "--rate", str(rate), "--duration", str(duration)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(inject_at)
    t_inject = time.time()
    log(f"inject: {exp['how']}")
    exp["inject"](names_before)
    time.sleep(1.0)
    snapshot_text(f"Exp {exp['id']} DURING (t+1 s)")
    t_restore = None
    if exp.get("restore"):
        time.sleep(exp["hold"] - 1.0)
        snapshot_text(f"Exp {exp['id']} DURING (end of fault, t+{exp['hold']} s)")
        t_restore = time.time()
        log(f"restore: {exp['restore_how']}")
        exp["restore"]()
    out, err = lg.communicate(timeout=duration + 60)
    wait_ready(timeout=120)
    time.sleep(1)
    mon.stop_flag = True
    mon.join(5)
    snapshot_text(f"Exp {exp['id']} AFTER")

    reqs = []
    for line in out.splitlines():
        try:
            r = json.loads(line)
            r["t"] -= offset  # pod clock -> host clock
            reqs.append(r)
        except ValueError:
            pass
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"exp{exp['id']}_requests.jsonl").write_text("\n".join(json.dumps(r) for r in reqs))
    (RAW / f"exp{exp['id']}_timeline.jsonl").write_text(
        "\n".join(json.dumps({"t": s["t"], "ready": s.get("ready"),
                              "pods": [(p["name"], p["ready"], p["restarts"])
                                       for p in s.get("pods", [])]}) for s in mon.samples))
    res = analyse(exp, reqs, mon.samples, t_inject, t_restore, restarts_before, names_before)
    res.update(t_inject=t_inject, t_restore=t_restore, load_rate_rps=rate, load_duration_s=duration)
    log(json.dumps({k: res[k] for k in ("detection_s", "recovery_s", "requests_failed",
                                        "requests_total", "success_percent", "error_window_s")}))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", type=int, default=[1, 2, 3, 4])
    ap.add_argument("--rate", type=float, default=20.0)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--inject-at", type=float, default=15.0)
    ap.add_argument("--db-hold", type=float, default=20.0)
    a = ap.parse_args()

    def crash(names):
        kubectl("exec", names[0], "--", "python", "-c",
                "import urllib.request\ntry: urllib.request.urlopen("
                "'http://127.0.0.1:8080/chaos/crash', timeout=2)\nexcept Exception: pass",
                check=False)

    def delete_all(_):
        kubectl("delete", "pod", "-l", SEL, "--grace-period=0", "--force", "--wait=false",
                check=False)

    def db_down(_):
        kubectl("scale", "statefulset/registration-db", "--replicas=0")

    def db_up():
        kubectl("scale", "statefulset/registration-db", "--replicas=1")

    def hang(names):
        kubectl("exec", names[-1], "--", "python", "-c",
                "import urllib.request;urllib.request.urlopen("
                "'http://127.0.0.1:8080/chaos/hang', timeout=2)", check=False)

    exps = {
        1: {"id": 1, "name": "One Registration replica fails",
            "how": "process crash in 1 of 2 pods (os._exit via in-pod /chaos/crash)", "inject": crash},
        2: {"id": 2, "name": "All Registration replicas fail",
            "how": "both pods force-deleted (kubectl delete pod --grace-period=0 --force)",
            "inject": delete_all},
        3: {"id": 3, "name": "Database unavailable",
            "how": f"registration-db StatefulSet scaled to 0 for {a.db_hold:.0f} s", "inject": db_down,
            "restore": db_up, "restore_how": "StatefulSet scaled back to 1", "hold": a.db_hold},
        4: {"id": 4, "name": "(bonus) One replica hangs",
            "how": "/livez of 1 pod stops answering (simulated deadlock)", "inject": hang},
    }

    log(f"cluster context: {subprocess.run(['kubectl', 'config', 'current-context'], capture_output=True, text=True).stdout.strip()}")
    version = subprocess.run(["kubectl", "version", "-o", "json"], capture_output=True, text=True).stdout
    try:
        server_version = json.loads(version)["serverVersion"]["gitVersion"]
    except Exception:
        server_version = "unknown"
    offset = clock_offset()
    log(f"server {server_version}, pod-host clock offset {offset*1000:.1f} ms")
    # warm-up: create schema, warm connections, prove the path works
    subprocess.run(["kubectl", "-n", NS, "exec", LOADGEN_POD, "--", "python", "/opt/loadgen/loadgen.py",
                    "--url", "http://api-gateway", "--rate", "5", "--duration", "4"],
                   capture_output=True, text=True)

    results = []
    for i in a.only:
        results.append(run_experiment(exps[i], a.rate, a.duration, a.inject_at, offset))
        time.sleep(5)

    RES.mkdir(exist_ok=True)
    out = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "kubernetes_server": server_version, "namespace": NS,
           "load": {"rate_rps": a.rate, "duration_s": a.duration, "inject_at_s": a.inject_at},
           "experiments": results}
    path = RES / "experiments.json"
    if path.exists() and set(a.only) != {1, 2, 3, 4}:  # merge partial re-runs
        old = json.loads(path.read_text())
        keep = [e for e in old.get("experiments", []) if e["experiment"] not in a.only]
        out["experiments"] = sorted(keep + results, key=lambda e: e["experiment"])
    path.write_text(json.dumps(out, indent=2))
    (RES / "experiment_log.txt").write_text("\n".join(LOG_LINES) + "\n")
    log(f"wrote {path.relative_to(ROOT)} and results/experiment_log.txt")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
