#!/usr/bin/env python3
"""
calc_dollar_per_war.py — compute $/WAR by year from a CSV of free agent contracts.

CSV format: player,year,aav_m,war_y1,war_y2,war_y3
  player  : display name
  year    : offseason year (2025 = 2024-25 offseason)
  aav_m   : annual average value in millions (e.g. 23.0)
  war_y1  : fWAR most recent full healthy season (2025 for 2025-26 offseason)
  war_y2  : fWAR one year prior — 2024 (leave blank if injury-shortened)
  war_y3  : fWAR two years prior — 2023 (leave blank if injury-shortened)

WAR is averaged across however many years are provided.
Outputs per-player breakdown, median by year, and a dict snippet to paste into comps.py.
"""

import csv
import statistics
from pathlib import Path
from collections import defaultdict

FA_CSV = Path(__file__).parent / "fa_contracts.csv"


def _avg_war(row):
    vals = []
    for col in ("war_y1", "war_y2", "war_y3", "war_y4"):
        raw = row.get(col, "").strip()
        if not raw or raw.upper() in ("NA", "N/A", "-"):
            continue
        v = float(raw)
        if v > 0:
            vals.append(v)
    if not vals:
        raise ValueError("no WAR values")
    return sum(vals) / len(vals), len(vals)


def main():
    by_year = defaultdict(list)
    rows = []

    with open(FA_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                aav  = float(row["aav_m"])
                year = int(row["year"])
                war, n_years = _avg_war(row)
            except (ValueError, KeyError):
                continue
            if aav <= 0:
                continue
            dpw = aav / war
            rows.append((year, row["player"], aav, war, n_years, dpw))
            by_year[year].append(dpw)

    if not rows:
        print("No valid rows found. Fill in aav_m and war columns in fa_contracts.csv.")
        return

    print(f"\n{'Player':<25} {'Year':>4}  {'AAV':>7}  {'Avg WAR':>7}  {'Yrs':>3}  {'$/WAR':>7}")
    print("-" * 63)
    for year, player, aav, war, n_years, dpw in sorted(rows):
        print(f"  {player:<23} {year:>4}  ${aav:>5.1f}M  {war:>7.2f}  {n_years:>3}  ${dpw:>6.2f}M")

    print(f"\n{'Year':>4}   {'N':>2}   {'Median $/WAR':>12}   {'Mean $/WAR':>10}")
    print("-" * 42)
    results = {}
    for year in sorted(by_year):
        vals = by_year[year]
        med  = statistics.median(vals)
        mean = statistics.mean(vals)
        results[year] = round(med, 1)
        print(f"  {year:>4}   {len(vals):>2}   ${med:>10.2f}M   ${mean:>8.2f}M")

    print("\n# Paste into DOLLAR_PER_WAR_BY_YEAR in comps.py:")
    items = ", ".join(f"{yr}: {val}" for yr, val in sorted(results.items()))
    print(f"DOLLAR_PER_WAR_BY_YEAR = {{{items}}}")


if __name__ == "__main__":
    main()
