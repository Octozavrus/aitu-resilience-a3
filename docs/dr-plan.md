# Disaster-recovery plan — synthetic AITU Registration platform

Targets from the assignment: **RPO ≤ 5 min, RTO ≤ 15 min.**

## What protects against what

| Threat | Handled by | Why the other layer can't |
|---|---|---|
| One pod crashes / hangs | HA: 2 replicas, probes, ReplicaSet (Part D, exp 1, 2, 4) | — |
| DB primary node dies | HA: hot standby promoted in 10–30 s | — |
| `DELETE`/`DROP` by mistake, bad migration, ransomware | **DR**: point-in-time restore from backup | The standby replays the same mistake instantly; readiness probes still pass (shown by `scripts/dr_drill.py`) |
| Cluster, zone or data centre lost | **DR**: rebuild from Git + restore DB elsewhere | Every HA copy lived in the lost place |

## Production design (described, not deployed in the lab)

* PostgreSQL primary + hot standby (CloudNativePG or Patroni), asynchronous streaming replication,
  alert when lag > 10 s; optional synchronous commit during registration week (RPO 0 for failover).
* Base backup daily at 01:30; `archive_timeout = 60` so a WAL segment leaves the primary at least
  every minute; both stored in object storage **outside** the cluster (another site), 14 days.
* Manifests in Git (this repo), secrets in an external secret store, nightly etcd snapshot.

Worst-case data loss = age of the newest archived WAL ≈ 60 s (+ upload time) → **RPO ≈ 1 min**.

## Full-recovery runbook (target ≤ 14 min)

| Step | Action | Budget |
|---|---|---|
| 0 | Declare incident, freeze writes at the gateway (maintenance response) | 1 min |
| 1 | Create namespace / cluster in the healthy site: `kubectl apply -f k8s/00-namespace.yaml -f k8s/10-postgres.yaml` | 2 min |
| 2 | Restore DB: base backup + WAL replay to the last good timestamp (just before the bad statement) | 8 min |
| 3 | `kubectl apply -f k8s/20-registration.yaml -f k8s/30-gateway.yaml`; wait for Ready (probes gate traffic) | 2 min |
| 4 | Smoke test (`make smoke`), compare row counts, re-point DNS / load balancer | 1 min |
| | **Total** | **14 min ≤ 15 min RTO** |

## Lab drill (`make dr-drill`)

The lab uses one DB pod, so the drill substitutes `pg_dump -Fc` for base backup + WAL:
backup copied out of the cluster → `DROP TABLE` (simulated human error) → observe that
pods stay Ready while the API fails → `pg_restore --clean` → time until the API answers 200.
Results go to `results/dr_drill.json` and are quoted in Part E of the report.

Rehearse once per semester **before** registration opens; a backup never restored does not count.
