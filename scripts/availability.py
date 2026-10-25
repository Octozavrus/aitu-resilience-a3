"""Part B - availability model for the synthetic AITU student-services platform.

  * analytic series/parallel model (R_par = 1 - prod(1 - R_i))
  * per-tier share of total unavailability (which block hurts most)
  * "+1 replica" what-if sweep and combined upgrade options
  * Monte Carlo cross-check of the analytic end-to-end result
  * N+1 capacity note for the 500 req/s peak

Writes results/availability.json and prints a readable summary.
Run: python scripts/availability.py
"""
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "availability.json"

# Given synthetic data (assignment table). name -> (single-unit availability, units)
BASE = {
    "Load Balancer": (0.9995, 1),
    "API Gateway":   (0.9990, 1),
    "Auth":          (0.9950, 2),
    "Course":        (0.9950, 2),
    "Registration":  (0.9950, 2),
    "Database":      (0.9970, 2),
}
TARGET = 0.999
HOURS_YEAR = 365 * 24
MIN_MONTH = 30 * 24 * 60
PEAK_RPS = 500


def parallel(r, n):
    return 1 - (1 - r) ** n


def tiers(cfg):
    return {k: parallel(r, n) for k, (r, n) in cfg.items()}


def series(values):
    return math.prod(values)


def e2e(cfg, only=None):
    t = tiers(cfg)
    return series(v for k, v in t.items() if only is None or k in only)


def downtime(a):
    return {"hours_per_year": round((1 - a) * HOURS_YEAR, 2),
            "minutes_per_month": round((1 - a) * MIN_MONTH, 2)}


def with_units(cfg, **units):
    new = dict(cfg)
    for name, n in units.items():
        key = name.replace("_", " ")
        new[key] = (cfg[key][0], n)
    return new


def monte_carlo(cfg, samples=2_000_000, seed=2026):
    """Sample independent up/down states per unit; system up iff every tier has >=1 unit up."""
    rng = np.random.default_rng(seed)
    up = np.ones(samples, dtype=bool)
    for r, n in cfg.values():
        tier_up = np.zeros(samples, dtype=bool)
        for _ in range(n):
            tier_up |= rng.random(samples) < r
        up &= tier_up
    a = up.mean()
    se = math.sqrt(a * (1 - a) / samples)
    return {"samples": samples, "seed": seed, "availability": round(float(a), 6),
            "ci95": [round(float(a - 1.96 * se), 6), round(float(a + 1.96 * se), 6)]}


def main():
    t = tiers(BASE)
    a = series(t.values())
    q_total = 1 - a
    q = {k: 1 - v for k, v in t.items()}
    q_sum = sum(q.values())  # first-order approximation of total unavailability
    shares = {k: round(100 * v / q_sum, 2) for k, v in q.items()}

    journeys = {
        "All services (assignment chain)": list(BASE),
        "Enroll in a course: LB-GW-Auth-Registration-DB": ["Load Balancer", "API Gateway", "Auth",
                                                           "Registration", "Database"],
        "Timetable lookup: LB-GW-Auth-Course-DB": ["Load Balancer", "API Gateway", "Auth",
                                                   "Course", "Database"],
    }
    journey_res = {name: round(e2e(BASE, set(parts)), 6) for name, parts in journeys.items()}

    sweep = []
    for name, (r, n) in BASE.items():
        cfg = with_units(BASE, **{name.replace(" ", "_"): n + 1})
        a2 = e2e(cfg)
        sweep.append({"change": f"{name}: {n} -> {n + 1}", "availability": round(a2, 6),
                      "gain_pp": round(100 * (a2 - a), 4), "meets_target": a2 >= TARGET,
                      **downtime(a2)})
    sweep.sort(key=lambda s: -s["availability"])

    options = {
        "Baseline": BASE,
        "2x API Gateway": with_units(BASE, API_Gateway=2),
        "2x Load Balancer": with_units(BASE, Load_Balancer=2),
        "2x API Gateway + 2x Load Balancer": with_units(BASE, API_Gateway=2, Load_Balancer=2),
    }
    option_res = {k: {"availability": round(e2e(c), 6), "meets_target": e2e(c) >= TARGET,
                      **downtime(e2e(c))} for k, c in options.items()}

    result = {
        "inputs": {k: {"unit_availability": r, "units": n} for k, (r, n) in BASE.items()},
        "tiers": {k: {"formula": (f"1-(1-{BASE[k][0]})^{BASE[k][1]}" if BASE[k][1] > 1
                                  else "single unit"),
                      "availability": round(v, 6)} for k, v in t.items()},
        "end_to_end": {"availability": round(a, 6), "percent": round(100 * a, 4),
                       "target": TARGET, "meets_target": a >= TARGET,
                       "gap_pp": round(100 * (TARGET - a), 4), **downtime(a),
                       "target_downtime": downtime(TARGET)},
        "unavailability_share_percent": shares,
        "bottleneck": max(q, key=q.get),
        "journeys": journey_res,
        "plus_one_replica_sweep": sweep,
        "upgrade_options": option_res,
        "monte_carlo_baseline": monte_carlo(BASE),
        "monte_carlo_2x_gateway": monte_carlo(options["2x API Gateway"]),
        "capacity_note": {
            "peak_rps": PEAK_RPS,
            "rule": "N+1: with 2 replicas the survivor must carry 100% of peak",
            "required_rps_per_registration_pod_if_one_fails": PEAK_RPS,
            "required_rps_per_pod_when_both_up": PEAK_RPS / 2,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))

    print("Tier availability")
    for k, v in t.items():
        print(f"  {k:<14} {v:.6f}  ({100*v:.4f}%)   share of downtime {shares[k]:5.2f}%")
    print(f"\nEnd-to-end series: {a:.6f} ({100*a:.4f}%)  target {TARGET}  "
          f"meets={a >= TARGET}  downtime {downtime(a)}")
    mc = result["monte_carlo_baseline"]
    print(f"Monte Carlo check: {mc['availability']} (95% CI {mc['ci95']})")
    print(f"Bottleneck: {result['bottleneck']}")
    print("\n+1 replica sweep:")
    for s in sweep:
        print(f"  {s['change']:<26} {100*s['availability']:.4f}%  +{s['gain_pp']:.4f} pp  "
              f"meets={s['meets_target']}")
    print("\nUpgrade options:")
    for k, v in option_res.items():
        print(f"  {k:<36} {100*v['availability']:.4f}%  {v['hours_per_year']} h/yr")
    print(f"\nWrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
