# Assignment 3 — Cloud Computing and Microservices Resilience

Synthetic **AITU Digital Student Services** platform: a Registration microservice on Kubernetes,
its availability model, measured failure experiments and a disaster-recovery drill.
All data, names and credentials are synthetic; nothing here touches real AITU systems.

```
Students ─► Load Balancer ─► API Gateway (NGINX ×2) ─► Registration (×2) ─► PostgreSQL
                                                      Auth / Course (modelled only)
```

| Assignment part | Where it lives | Output |
|---|---|---|
| A — architecture analysis | `scripts/build_report.py` (text), `diagrams/architecture.png` | report |
| B — availability | `scripts/availability.py` | `results/availability.json`, `diagrams/rbd.png`, `diagrams/sensitivity.png` |
| C — Kubernetes YAML | `k8s/20-registration.yaml` (+ DB, gateway, traffic pod) | running cluster |
| D — failure simulation | `scripts/experiments.py` | `results/experiments.json`, `results/raw/`, `results/experiment_log.txt`, `diagrams/experiments.png` |
| E — RPO/RTO, DR | `docs/dr-plan.md`, `scripts/dr_drill.py` | `results/dr_drill.json` |
| Report | `scripts/build_report.py` + `team.yaml` | `report/Assignment3_Report.docx` |

---

## 1. Prerequisites (one-time)

* **Docker Desktop** with **Kubernetes enabled** (Settings → Kubernetes → Enable). This is the
  easiest path on Windows and macOS. Alternatives: `kind` or `minikube`.
* `kubectl` (ships with Docker Desktop). Check: `kubectl get nodes` shows one `Ready` node.
* Python 3.10+ and `pip install -r requirements-dev.txt`.
* `make` (macOS/Linux have it; on Windows use Git Bash, or run the plain commands in §3).

## 2. Run everything (≈10 minutes)

```bash
make model          # Part B numbers
make image          # builds aitu-a3/registration:1.0   (kind: make image CLUSTER=kind)
make deploy         # namespace aitu-a3: postgres, registration ×2, gateway ×2, traffic pod
make smoke          # should print the list of synthetic course sections
make experiments    # Part D, ~6 min, needs nothing else running against the cluster
make dr-drill       # Part E evidence
# edit team.yaml (names, IDs, dates, repo URL, your own problem/limitation text)
make report         # -> report/Assignment3_Report.docx
```

Re-run a single experiment: `python scripts/experiments.py --only 3` (results are merged).
Different load: `python scripts/experiments.py --rate 40 --db-hold 30`.

## 3. Same thing without `make` (Windows PowerShell)

```powershell
python scripts/availability.py
docker build -t aitu-a3/registration:1.0 app/registration
kubectl apply -f k8s/00-namespace.yaml
kubectl -n aitu-a3 create configmap loadgen-script --from-file=app/loadgen/loadgen.py
kubectl apply -f k8s/10-postgres.yaml -f k8s/20-registration.yaml
kubectl -n aitu-a3 rollout status statefulset/registration-db
kubectl -n aitu-a3 rollout status deployment/registration-service
kubectl apply -f k8s/30-gateway.yaml -f k8s/40-loadgen.yaml
kubectl -n aitu-a3 get pods        # wait until all are Running and 1/1
python scripts/experiments.py
python scripts/dr_drill.py
python scripts/diagrams.py
python scripts/build_report.py
```

## 4. What the experiments do

All four run under continuous traffic (default 20 req/s, 70 % course list / 30 % enrolment)
sent **from inside the cluster** through the `api-gateway` Service, so requests are load-balanced
across pods exactly like real traffic (a `kubectl port-forward` would pin one pod).

| # | Fault | How it is injected | Self-healing path exercised |
|---|---|---|---|
| 1 | One replica fails | in-pod `GET 127.0.0.1:8080/chaos/crash` → process exits | kubelet restarts the container (restart count +1) |
| 2 | All replicas fail | `kubectl delete pod -l app=registration-service --force` | ReplicaSet creates two new pods |
| 3 | Database down | `kubectl scale statefulset/registration-db --replicas=0`, 20 s, back to 1 | readiness removes pods, no restarts, auto-return |
| 4 | (bonus) One replica hangs | in-pod `/chaos/hang` → `/livez` stops answering | liveness probe restarts it after ~15 s |

*Detection* = injection → Kubernetes reports < 2 Ready pods. *Recovery* = injection → 2 Ready
pods again. *User impact* = computed from every logged request (`results/raw/exp*_requests.jsonl`).

Why the crash endpoint instead of `kubectl exec … kill -9 1`? The kernel ignores SIGKILL sent to a
container's PID 1 from inside its own PID namespace, so that command silently does nothing.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `ErrImageNeverPull` / `ImagePullBackOff` on registration pods | image not visible to the cluster: kind → `make image CLUSTER=kind`; minikube → `CLUSTER=minikube` |
| registration pods `0/1 Running` forever | DB not ready: `kubectl -n aitu-a3 logs registration-db-0`; `kubectl -n aitu-a3 logs deploy/registration-service` |
| gateway `CrashLoopBackOff` "host not found in upstream" | it was applied before the registration Service; `kubectl apply -f k8s/20-registration.yaml` then restart the gateway |
| postgres `Pending` | no default StorageClass; Docker Desktop/kind/minikube have one, other clusters need one |
| experiments print `None` for recovery | increase `--duration` (slow laptop) |

Clean up: `make clean`.
