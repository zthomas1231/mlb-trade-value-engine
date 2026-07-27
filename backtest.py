"""
Back-test: does the surplus model predict actual trade return tiers?

Runs the model's surplus formula against every complete trade in trades.csv
using historical wWAR and known contract data, then compares the predicted
Net Trade Tier to the hand-labeled return_tier.

Usage: python backtest.py
"""

import sys
import pandas as pd
import numpy as np
from scipy import stats
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from player_value import (project_wars, _TALENT_THRESHOLDS, _DEVELOPMENT_FACTORS,
                          _UNPROVEN_TIER_CAPS, _UNPROVEN_STATUSES,
                          DISCOUNT_RATE, CONTROL_DISCOUNT)
from comps import DOLLAR_PER_WAR_BY_YEAR

DOLLARS_PER_WAR = 7.0  # current calibration — model uses this, so back-test uses this too
MLB_MIN = 0.74


def talent_value(wWAR, age, n, status="signed"):
    wars = project_wars(max(0.0, wWAR), age, n=n)
    tv = sum(w * DOLLARS_PER_WAR / ((1 + DISCOUNT_RATE) ** i) for i, w in enumerate(wars))
    factor = _DEVELOPMENT_FACTORS.get(status, 1.0)
    return round(tv * CONTROL_DISCOUNT * factor, 2)


def talent_tier(tv):
    for threshold, tier in _TALENT_THRESHOLDS:
        if tv >= threshold:
            return tier
    return 1


def salary_pv(aav, n):
    return round(
        sum(aav / ((1 + DISCOUNT_RATE) ** i) for i in range(n)) * CONTROL_DISCOUNT, 2
    )


def contract_adj(spv, tv):
    ratio = spv / tv if tv > 0 else 2.0
    if ratio < 0.25:   return 3
    if ratio < 0.45:   return 2
    if ratio < 0.65:   return 1
    if ratio < 0.85:   return 0
    if ratio < 1.05:   return 0
    if ratio < 1.35:   return -1
    if ratio < 1.75:   return -2
    return -3


def severity_pen(wWAR, age, n, aav):
    wars = project_wars(max(0.0, wWAR), age, n=n)
    total = sum(
        (w * DOLLARS_PER_WAR - aav) / ((1 + DISCOUNT_RATE) ** i)
        for i, w in enumerate(wars)
    ) * CONTROL_DISCOUNT
    if total < -90:   return -3
    if total < -60:   return -2
    if total < -30:   return -1
    return 0


def run_model(row):
    wWAR   = float(row["wWAR"])
    age    = int(row["age_at_trade"])
    n      = max(1, int(row["years_control_remaining"]))  # rentals = 1 year of value
    status = str(row.get("contract_status", "signed")).lower()
    aav    = float(row["aav_m"]) if pd.notna(row.get("aav_m")) and row["aav_m"] > 0 else (
             float(row["salary_m"]) if pd.notna(row.get("salary_m")) and row["salary_m"] > 0 else MLB_MIN)

    tv   = talent_value(wWAR, age, n, status)
    tt   = talent_tier(tv)
    spv  = salary_pv(aav, n)
    cadj = contract_adj(spv, tv)
    sp   = severity_pen(wWAR, age, n, aav)
    net  = max(0, min(10, tt + cadj + sp))

    if status in _UNPROVEN_STATUSES:
        for wWAR_min, cap in _UNPROVEN_TIER_CAPS:
            if wWAR >= wWAR_min:
                net = min(net, cap)
                break

    return {
        "tv": tv, "talent_tier": tt, "cadj": cadj,
        "severity_pen": sp, "model_tier": net,
    }


def main():
    df = pd.read_csv("trades.csv")

    required = ["wWAR", "age_at_trade", "years_control_remaining", "return_tier"]
    df = df.dropna(subset=required)
    df = df[df["return_tier"].astype(float) > 0]      # exclude FA signings (tier 0)
    df = df[df["wWAR"].astype(float) > 0]             # need positive WAR baseline
    df["return_tier"] = df["return_tier"].astype(int)

    rows = []
    for _, r in df.iterrows():
        m = run_model(r)
        rows.append({
            "trade_id":    r["trade_id"],
            "player":      r["player_name"],
            "year":        str(r["trade_date"])[:4],
            "wWAR":        r["wWAR"],
            "age":         int(r["age_at_trade"]),
            "n":           max(1, int(r["years_control_remaining"])),
            "status":      r.get("contract_status", "?"),
            "tv":          m["tv"],
            "talent_tier": m["talent_tier"],
            "cadj":        m["cadj"],
            "sev":         m["severity_pen"],
            "model_tier":  m["model_tier"],
            "actual_tier": r["return_tier"],
            "error":       m["model_tier"] - r["return_tier"],
        })

    res = pd.DataFrame(rows)

    # ── Summary stats ──────────────────────────────────────────────────────────
    r_val, pval = stats.pearsonr(res["model_tier"], res["actual_tier"])
    mae  = res["error"].abs().mean()
    rmse = (res["error"] ** 2).mean() ** 0.5
    within1 = (res["error"].abs() <= 1).mean()
    within2 = (res["error"].abs() <= 2).mean()

    print(f"\n{'='*60}")
    print(f"  BACK-TEST RESULTS  ({len(res)} trades analyzed)")
    print(f"{'='*60}")
    print(f"  Pearson r    : {r_val:.3f}  (p = {pval:.2e})")
    print(f"  MAE          : {mae:.2f} tiers")
    print(f"  RMSE         : {rmse:.2f} tiers")
    print(f"  Within ±1    : {within1:.0%} of trades")
    print(f"  Within ±2    : {within2:.0%} of trades")

    print(f"\n  Error distribution:")
    for e in range(-4, 6):
        count = (res["error"] == e).sum()
        bar = "█" * count
        print(f"    {e:+2d}: {bar} ({count})")

    # ── Tier-level breakdown ───────────────────────────────────────────────────
    print(f"\n  Actual return tier vs model tier (mean predicted, n):")
    print(f"  {'Actual':>8} {'n':>5} {'Model avg':>10} {'Mean err':>10}")
    for t in sorted(res["actual_tier"].unique()):
        sub = res[res["actual_tier"] == t]
        print(f"  {t:>8} {len(sub):>5} {sub['model_tier'].mean():>10.2f} {sub['error'].mean():>+10.2f}")

    # ── Contract status breakdown ──────────────────────────────────────────────
    print(f"\n  Error by contract status:")
    print(f"  {'Status':>12} {'n':>5} {'MAE':>8} {'Mean err':>10}")
    for s in sorted(res["status"].unique()):
        sub = res[res["status"] == s]
        print(f"  {s:>12} {len(sub):>5} {sub['error'].abs().mean():>8.2f} {sub['error'].mean():>+10.2f}")

    # ── Worst misses ───────────────────────────────────────────────────────────
    print(f"\n  Largest misses (|error| ≥ 3):")
    big = res[res["error"].abs() >= 3].sort_values("error", key=abs, ascending=False)
    if big.empty:
        print("  (none)")
    else:
        print(f"  {'Player':<30} {'Status':>8} {'wWAR':>6} {'n':>3} {'Model':>7} {'Actual':>7} {'Err':>5}")
        for _, r in big.iterrows():
            print(f"  {r['player']:<30} {r['status']:>8} {r['wWAR']:>6.1f} {r['n']:>3} "
                  f"{r['model_tier']:>7} {r['actual_tier']:>7} {r['error']:>+5}")

    # ── Systematic bias check ──────────────────────────────────────────────────
    print(f"\n  Bias check (model systematically over/under):")
    mean_err = res["error"].mean()
    print(f"  Mean error (model − actual): {mean_err:+.2f}")
    if mean_err > 0.3:
        print("  → Model predicts HIGHER tiers than actual (overestimates)")
    elif mean_err < -0.3:
        print("  → Model predicts LOWER tiers than actual (underestimates)")
    else:
        print("  → Model is well-calibrated (no significant systematic bias)")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
