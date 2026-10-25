"""Part E evidence - backup / restore drill for the registration database.

Shows the HA-vs-DR difference with a real measurement:
  1. take a logical backup (pg_dump -Fc) and copy it OUT of the cluster (results/backup/)
  2. "disaster": DROP both tables (an accidental DROP is replicated instantly by any standby,
     and readiness still passes because the DB itself is up -> HA cannot help)
  3. restore from the backup with pg_restore and time it
  4. time until the Registration API answers 200 again through the gateway
Writes results/dr_drill.json.  Run after `make deploy` (and preferably after experiments).
Lab note: pg_dump stands in for the production design (base backup + WAL archive / PITR).
"""
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NS, DBPOD = "aitu-a3", "registration-db-0"
PSQL = ["psql", "-U", "registration_app", "-d", "registration", "-tAc"]


def k(*args, data=None, binary=False):
    p = subprocess.run(["kubectl", "-n", NS, *args], input=data, capture_output=True,
                       text=not binary, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(p.stderr if not binary else p.stderr.decode())
    return p.stdout


def sql(q):
    return k("exec", DBPOD, "--", *PSQL, q).strip()


def api_ok():
    out = k("exec", "student-traffic", "--", "python", "-c",
            "import urllib.request as u\ntry: print(u.urlopen('http://api-gateway/api/registration/"
            "courses', timeout=2).status)\nexcept Exception as e: print(getattr(e,'code',0))")
    return out.strip() == "200"


def counts():
    return int(sql("SELECT count(*) FROM enrollment")) + int(sql("SELECT count(*) FROM course_section"))


def main():
    before = counts()
    print(f"rows before: {before}")
    t0 = time.time()
    dump = k("exec", DBPOD, "--", "pg_dump", "-U", "registration_app", "-d", "registration", "-Fc",
             binary=True)
    backup_s = time.time() - t0
    bdir = ROOT / "results" / "backup"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "registration.dump").write_bytes(dump)
    print(f"backup {len(dump)} bytes in {backup_s:.2f} s (stored outside the cluster)")

    sql("DROP TABLE enrollment; DROP TABLE course_section;")
    t_dis = time.time()
    time.sleep(2)
    ready_pods = k("get", "pods", "-l", "app=registration-service", "-o",
                   "jsonpath={.items[*].status.containerStatuses[0].ready}")
    api_during = api_ok()
    print(f"after DROP: pods ready = {ready_pods} (HA sees nothing wrong), API ok = {api_during}")

    t1 = time.time()
    k("exec", "-i", DBPOD, "--", "pg_restore", "-U", "registration_app", "-d", "registration",
      "--clean", "--if-exists", "--no-owner", data=dump, binary=True)
    restore_s = time.time() - t1
    deadline = time.time() + 120
    while not api_ok():
        if time.time() > deadline:
            raise SystemExit("API did not recover within 120 s after restore")
        time.sleep(0.5)
    ready_s = time.time() - t1
    after = counts()
    res = {"backup_seconds": round(backup_s, 2), "backup_bytes": len(dump),
           "pods_ready_during_corruption": ready_pods, "api_ok_during_corruption": api_during,
           "restore_seconds": round(restore_s, 2), "service_ready_seconds": round(ready_s, 2),
           "outage_seconds_total": round(time.time() - t_dis, 2),
           "rows_before": before, "rows_after": after, "data_complete": before == after,
           "method": "pg_dump -Fc -> copy out of cluster -> DROP TABLE -> pg_restore --clean"}
    (ROOT / "results" / "dr_drill.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
