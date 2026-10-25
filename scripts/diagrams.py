"""Figures for the report.

  diagrams/architecture.png  - deployed architecture (what runs in the cluster vs. modelled tiers)
  diagrams/rbd.png           - reliability block diagram with tier availabilities
  diagrams/sensitivity.png   - end-to-end availability for each +1 replica option vs 99.9% target
  diagrams/experiments.png   - per experiment: request latency/status + Ready pods over time
                               (only when results/experiments.json exists)
Run: python scripts/diagrams.py
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "diagrams"
RES = ROOT / "results"
INK, INK2, MUTED, SURF = "#0b0b0b", "#52514e", "#a3a29c", "#fcfcfb"
BLUE, BLUE_L, ORANGE, AQUA = "#2a78d6", "#cde2fb", "#eb6834", "#1baf7a"
GOOD, BAD = "#0ca30c", "#e34948"

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": MUTED,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.dpi": 200})


def box(ax, x, y, w, h, title, sub="", fill=BLUE_L, edge=BLUE, dashed=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                fc=fill, ec=edge, lw=1.6, ls="--" if dashed else "-"))
    ax.text(x + w / 2, y + h / 2 + (0.12 if sub else 0), title, ha="center", va="center",
            fontsize=10, color=INK, weight="bold")
    if sub:
        ax.text(x + w / 2, y + h / 2 - 0.2, sub, ha="center", va="center", fontsize=8, color=INK2)


def arrow(ax, x1, y1, x2, y2):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=INK2, lw=1.2))


def architecture():
    fig, ax = plt.subplots(figsize=(11, 5.2))
    ax.set_xlim(0, 11)
    ax.set_ylim(0, 5.6)
    ax.axis("off")
    box(ax, 0.1, 2.3, 1.3, 1.0, "Students", "10,000 / 500 rps", fill="#f0efec", edge=MUTED)
    box(ax, 1.9, 2.3, 1.5, 1.0, "Load Balancer", "ingress / cloud LB", fill="#f0efec", edge=MUTED,
        dashed=True)
    box(ax, 3.9, 2.0, 1.7, 1.6, "API Gateway", "NGINX x2 (PDB)\n450 ms timeout", fill="#fde3d8",
        edge=ORANGE)
    box(ax, 6.2, 4.1, 1.8, 1.0, "Auth", "x2 (modelled)", fill="#f0efec", edge=MUTED, dashed=True)
    box(ax, 6.2, 2.3, 1.8, 1.0, "Registration", "x2 Deployment")
    box(ax, 6.2, 0.5, 1.8, 1.0, "Course", "x2 (modelled)", fill="#f0efec", edge=MUTED, dashed=True)
    box(ax, 8.7, 2.1, 2.1, 1.4, "PostgreSQL", "StatefulSet + PVC\n(design: primary + standby)",
        fill="#d4f1e5", edge=AQUA)
    arrow(ax, 1.4, 2.8, 1.9, 2.8)
    arrow(ax, 3.4, 2.8, 3.9, 2.8)
    for y in (4.6, 2.8, 1.0):
        arrow(ax, 5.6, 2.8, 6.2, y)
    arrow(ax, 8.0, 2.8, 8.7, 2.8)
    arrow(ax, 8.0, 4.6, 8.7, 3.3)
    arrow(ax, 8.0, 1.0, 8.7, 2.3)
    ax.text(0.1, 5.35, "Namespace aitu-a3 - synthetic AITU Digital Student Services", fontsize=12,
            weight="bold", color=INK)
    ax.text(0.1, 0.05, "Solid = deployed and tested in Kubernetes;  dashed = part of the availability "
            "model, not deployed in the lab.  Orange = bottleneck fixed by replication.",
            fontsize=8.5, color=INK2)
    fig.savefig(OUT / "architecture.png", bbox_inches="tight")
    plt.close(fig)


def rbd(av):
    t = av["tiers"]
    fig, ax = plt.subplots(figsize=(12, 3.6))
    ax.set_xlim(0, 12.2)
    ax.set_ylim(0, 3.6)
    ax.axis("off")
    order = [("Load Balancer", 1), ("API Gateway", 1), ("Auth", 2), ("Course", 2),
             ("Registration", 2), ("Database", 2)]
    x = 0.2
    for i, (name, n) in enumerate(order):
        single = av["inputs"][name]["unit_availability"]
        col = (ORANGE, "#fde3d8") if name == "API Gateway" else (BLUE, BLUE_L)
        if n == 1:
            box(ax, x, 1.2, 1.6, 0.9, name, f"{100*single:.2f}%", fill=col[1], edge=col[0])
        else:
            for k, y in enumerate((2.0, 0.4)):
                box(ax, x, y, 1.6, 0.8, f"{name} {k+1}", f"{100*single:.2f}%", fill=col[1],
                    edge=col[0])
                yc = y + 0.4
                ax.plot([x - 0.2, x - 0.05], [1.65, yc], color=INK2, lw=1.1)   # fan-in
                ax.plot([x + 1.65, x + 1.8], [yc, 1.65], color=INK2, lw=1.1)   # fan-out
            ax.plot([x - 0.2, x + 1.8], [1.65, 1.65], "o", color=INK2, ms=4)   # join nodes
        ax.text(x + 0.8, 3.35, f"{100*t[name]['availability']:.4f}%", ha="center", fontsize=9,
                color=INK, weight="bold")
        if i < len(order) - 1:
            arrow(ax, x + (1.8 if n > 1 else 1.6), 1.65, x + 1.8, 1.65)
        x += 2.0
    e = av["end_to_end"]
    ax.text(0.2, 0.0, f"Series product = {100*e['availability']:.4f}%  (target 99.9%, "
            f"{e['hours_per_year']} h/yr downtime).  Bold = tier availability.", fontsize=9,
            color=INK2)
    fig.savefig(OUT / "rbd.png", bbox_inches="tight")
    plt.close(fig)


def sensitivity(av):
    rows = [("Baseline", av["end_to_end"]["availability"])]
    rows += [(s["change"], s["availability"]) for s in av["plus_one_replica_sweep"]]
    rows.append(("Gateway x2 + LB x2",
                 av["upgrade_options"]["2x API Gateway + 2x Load Balancer"]["availability"]))
    rows = rows[::-1]
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    vals = [100 * v for _, v in rows]
    colors = [BLUE if v >= 99.9 else MUTED for v in vals]
    ax.barh(range(len(rows)), [v - 99.8 for v in vals], left=99.8, color=colors, height=0.6)
    ax.axvline(99.9, color=INK, lw=1, ls="--")
    ax.text(99.9, -0.75, " 99.9% target", fontsize=8.5, color=INK, va="center")
    ax.set_yticks(range(len(rows)), [r for r, _ in rows])
    for i, v in enumerate(vals):
        ax.text(v + 0.002, i, f"{v:.4f}%", va="center", fontsize=8.5, color=INK,
                bbox=dict(fc=SURF, ec="none", pad=1.5), zorder=5)
    ax.set_xlim(99.8, 100.02)
    ax.set_ylim(-1.1, len(rows) - 0.5)
    ax.set_xlabel("End-to-end availability, %  (blue = meets target)")
    ax.set_title("Which single redundancy change matters?", loc="left", fontsize=11, color=INK)
    ax.grid(axis="x", color="#e6e5e0", lw=0.6)
    ax.set_axisbelow(True)
    fig.savefig(OUT / "sensitivity.png", bbox_inches="tight")
    plt.close(fig)


def experiments():
    path = RES / "experiments.json"
    if not path.exists():
        print("results/experiments.json not found - skipping experiments.png (run experiments first)")
        return
    data = json.loads(path.read_text())["experiments"]
    fig, axes = plt.subplots(2, len(data), figsize=(4.2 * len(data), 5.6),
                             gridspec_kw={"height_ratios": [2.2, 1]}, squeeze=False)
    for j, e in enumerate(data):
        n = e["experiment"]
        reqs = [json.loads(l) for l in (RES / "raw" / f"exp{n}_requests.jsonl").read_text().splitlines()
                if l.strip()]
        tl = [json.loads(l) for l in (RES / "raw" / f"exp{n}_timeline.jsonl").read_text().splitlines()
              if l.strip()]
        t0 = e["t_inject"]
        ok = [r for r in reqs if 0 < r["status"] < 500]
        bad = [r for r in reqs if not 0 < r["status"] < 500]
        a = axes[0][j]
        a.scatter([r["t"] - t0 for r in ok], [r["ms"] for r in ok], s=9, color=BLUE, label="success",
                  lw=0)
        a.scatter([r["t"] - t0 for r in bad], [max(r["ms"], 0.5) for r in bad], s=22, color=BAD,
                  marker="x", label="failed (5xx / no answer)")
        a.axvline(0, color=INK, lw=1, ls="--")
        a.axhline(500, color=MUTED, lw=0.8, ls=":")
        a.set_yscale("log")
        a.set_title(f"Exp {n}: {e['name']}", loc="left", fontsize=9.5, color=INK)
        a.set_ylabel("latency, ms (log)" if j == 0 else "")
        if j == 0:
            a.legend(fontsize=8, frameon=False, loc="upper left")
        b = axes[1][j]
        pts = [(s["t"] - t0, s["ready"]) for s in tl if s["ready"] is not None]
        b.step([p[0] for p in pts], [p[1] for p in pts], where="post", color=AQUA, lw=2)
        b.axvline(0, color=INK, lw=1, ls="--")
        if e.get("t_restore"):
            for ax_ in (a, b):
                ax_.axvline(e["t_restore"] - t0, color=GOOD, lw=1, ls="--")
        b.set_ylim(-0.2, 2.6)
        b.set_yticks([0, 1, 2])
        b.set_ylabel("Ready pods" if j == 0 else "")
        b.set_xlabel("seconds from injection (dashed)")
        for ax_ in (a, b):
            ax_.set_xlim(-15, max(p[0] for p in pts) if pts else 45)
    fig.tight_layout()
    fig.savefig(OUT / "experiments.png", bbox_inches="tight")
    plt.close(fig)


def main():
    OUT.mkdir(exist_ok=True)
    av = json.loads((RES / "availability.json").read_text())
    architecture()
    rbd(av)
    sensitivity(av)
    experiments()
    print("figures written to diagrams/")


if __name__ == "__main__":
    main()
