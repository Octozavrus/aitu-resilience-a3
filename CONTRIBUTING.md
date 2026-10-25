# Team workflow — three real contributors

The instructor checks *individual* contribution, so every student commits **their own part from
their own GitHub account**. Don't let one person push everything with three names in the log;
`git log` and the GitHub "Contributors" graph must match `team.yaml → members[].owns`.

## Roles

| Student | Owns (commits these files) | Explains in defense |
|---|---|---|
| **S1 — Analysis** | `scripts/availability.py`, `scripts/diagrams.py`, `docs/dr-plan.md`, `scripts/dr_drill.py` | Part B math, bottleneck, Part E, HA vs DR |
| **S2 — Platform** | `app/registration/`, `k8s/00-namespace.yaml`, `k8s/10-postgres.yaml`, `k8s/20-registration.yaml` | Part C: every probe/rollout/resource setting |
| **S3 — Chaos & report** | `app/loadgen/`, `k8s/30-gateway.yaml`, `k8s/40-loadgen.yaml`, `scripts/experiments.py`, `scripts/build_report.py`, `Makefile`, `.github/` | Part D: method + results, the report |

Everyone runs the full pipeline on their own laptop at least once (§4) — any of you can be asked
"what happens if both replicas fail?".

## 1. S1 creates the repository (once)

1. GitHub → New repository → name `aitu-resilience-a3` → **empty** (no README/licence) → Create.
2. Settings → Collaborators → *Add people* → add S2 and S3 (they must accept the e-mail invite).
3. Locally, inside the unzipped project folder:

```bash
git init -b main
git config user.name  "S1 Full Name"
git config user.email "s1-github-email@example.com"     # the email on S1's GitHub account
git add README.md CONTRIBUTING.md .gitignore team.yaml requirements-dev.txt
git commit -m "Project skeleton: README, team file, workflow"
git remote add origin https://github.com/<S1-username>/aitu-resilience-a3.git
git push -u origin main
```

Keep the other folders on the side for now — S2 and S3 will add theirs.

## 2. Everyone: one branch per part, merged by pull request

```bash
git clone https://github.com/<S1-username>/aitu-resilience-a3.git && cd aitu-resilience-a3
git config user.name "Your Name"; git config user.email "your-github-email"
git switch -c part-c-platform            # S1: part-b-model   S3: part-d-chaos
# copy YOUR files from the unzipped project into the repo, then:
git add app/registration k8s/00-namespace.yaml k8s/10-postgres.yaml k8s/20-registration.yaml
git commit -m "Registration service + Kubernetes manifests (Part C)"
git push -u origin part-c-platform
```

Open a Pull Request on GitHub; **another** member reviews and merges it (reviews are evidence too).
Commit in several small steps as you actually change things (e.g. tuning probe values after a run)
rather than one giant commit.

Suggested order (each depends on the previous being merged):

1. S1 `part-b-model` — availability script + `results/availability.json` + figures.
2. S2 `part-c-platform` — service, Dockerfile, namespace/DB/registration YAML.
3. S3 `part-d-chaos` — gateway, traffic pod, experiments, Makefile, CI.
4. S1 `part-e-dr` — `docs/dr-plan.md`, `scripts/dr_drill.py`.
5. S3 `report` — `scripts/build_report.py`.

## 3. The measured run (one laptop, one person drives, others watch)

```bash
git pull
make model image deploy experiments dr-drill
git add results/ diagrams/
git commit -m "Measured run on <laptop/cluster>: experiments + DR drill"
```

Then each person edits **their own** lines in `team.yaml` (name, ID, GitHub) and the shared
`individual:` text, commits it, and S3 runs `make report` and commits `report/Assignment3_Report.docx`.
Put the final commit hash in the submission (the report prints it automatically).

## 4. Before the defense — everyone

```bash
make clean && make deploy && python scripts/experiments.py --only 2
```

Watch `kubectl -n aitu-a3 get pods -w` in a second terminal while it runs.
