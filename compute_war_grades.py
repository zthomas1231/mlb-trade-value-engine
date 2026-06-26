"""
Reads war reference.csv, computes wWAR / grades / availability, writes back.

Column conventions (from the CSV):
  war_yr0 / g_yr0  — partial season if deadline trade (intentional signal)
  war_yr1 / g_yr1  — most recent full season
  war_yr2 / g_yr2  — 2 seasons back
  war_yr3 / g_yr3  — 3 seasons back

wWAR uses Marcel weights (5/4/3) on yr1/yr2/yr3 (full seasons only).
Availability uses yr1 games as the most recent full-season baseline.
"""

import csv
import re
from pathlib import Path

CSV_PATH = Path.home() / "OneDrive - Driveline Baseball" / "war reference.csv"

# Marcel weights: yr1=5, yr2=4, yr3=3
MARCEL = [5, 4, 3]

WAR_GRADES = [
    (8.0,  "A+"),
    (6.0,  "A"),
    (4.5,  "A-"),
    (3.5,  "B+"),
    (2.5,  "B"),
    (1.5,  "B-"),
    (0.5,  "C+"),
    (0.0,  "C"),
    (-1.0, "D"),
]

# (min_pct_of_target, grade)
AVAIL_GRADES = [
    (0.957, "A+"),
    (0.895, "A"),
    (0.802, "B"),
    (0.679, "C"),
    (0.556, "D"),
]

SP_TARGETS = [(31, "A+"), (28, "A"), (25, "B"), (20, "C"), (15, "D")]
RP_TARGETS = [(65, "A+"), (58, "A"), (50, "B"), (40, "C"), (30, "D")]


def _parse_float(val):
    if val is None:
        return None
    s = str(val).strip().replace(",", ".")  # fix "1,6" typo
    if s == "" or s.lower() in ("n/a", "na", "none", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(val):
    f = _parse_float(val)
    return int(f) if f is not None else None


def war_grade(war):
    if war is None:
        return ""
    for threshold, grade in WAR_GRADES:
        if war >= threshold:
            return grade
    return "F"


def weighted_war(yr1, yr2, yr3):
    """Marcel 5/4/3 on full seasons. Returns None if no data."""
    pairs = [(v, w) for v, w in zip([yr1, yr2, yr3], MARCEL) if v is not None]
    if not pairs:
        return None
    total_w = sum(w for _, w in pairs)
    return round(sum(v * w for v, w in pairs) / total_w, 2)


def _role_category(role):
    if not role:
        return "hitter"
    r = role.upper()
    if any(x in r for x in ("SP", "STARTER")):
        return "sp"
    if any(x in r for x in ("RP", "CP", "CL", "CLOSER", "RELIEF")):
        return "rp"
    return "hitter"


def avail_grade(games, role_cat):
    if games is None:
        return ""
    if role_cat == "sp":
        for threshold, grade in SP_TARGETS:
            if games >= threshold:
                return grade
        return "F"
    if role_cat == "rp":
        for threshold, grade in RP_TARGETS:
            if games >= threshold:
                return grade
        return "F"
    # hitter
    pct = games / 162
    for threshold, grade in AVAIL_GRADES:
        if pct >= threshold:
            return grade
    return "F"


def avail_pct(games, role_cat):
    if games is None:
        return ""
    if role_cat == "sp":
        return round(games / 30, 3)
    if role_cat == "rp":
        return round(games / 65, 3)
    return round(games / 162, 3)


def process():
    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames[:]
        rows = list(reader)

    # Add new columns if not present
    new_cols = ["wWAR", "wWAR_grade", "peak_grade", "avail_pct_computed", "avail_grade"]
    for col in new_cols:
        if col not in fieldnames:
            fieldnames.append(col)

    computed = 0
    for row in rows:
        yr1 = _parse_float(row.get("war_yr1"))
        yr2 = _parse_float(row.get("war_yr2"))
        yr3 = _parse_float(row.get("war_yr3"))
        g1  = _parse_int(row.get("g_yr1"))
        peak = _parse_float(row.get("war_peak"))
        role = row.get("role", "")
        role_cat = _role_category(role)

        # Fix Varsho-style comma typo in war_yr2 back to the source field
        if row.get("war_yr2", "").strip().replace(".", "").replace("-", "").replace(",", "").isdigit():
            row["war_yr2"] = str(_parse_float(row["war_yr2"])) if _parse_float(row["war_yr2"]) is not None else row["war_yr2"]

        wwar = weighted_war(yr1, yr2, yr3)
        row["wWAR"]              = wwar if wwar is not None else ""
        row["wWAR_grade"]        = war_grade(wwar)
        row["peak_grade"]        = war_grade(peak)
        row["avail_pct_computed"] = avail_pct(g1, role_cat)
        row["avail_grade"]       = avail_grade(g1, role_cat)

        if wwar is not None:
            computed += 1

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Done. Computed wWAR for {computed} players.")
    print(f"Wrote to: {CSV_PATH}")

    # Preview top rows with data
    print()
    header = f"{'Player':<35} {'Role':<6} {'wWAR':>5} {'wG':<5} {'Peak':>5} {'PkGr':<4} {'AvailPct':>8} {'AG':<4}"
    print(header)
    print("-" * len(header))
    previewed = 0
    for row in rows:
        if row.get("wWAR") == "" or row.get("wWAR") is None:
            continue
        wwar_val = _parse_float(row["wWAR"])
        peak_val = _parse_float(row.get("war_peak"))
        g1_val   = _parse_int(row.get("g_yr1"))
        print(
            f"{row['player_name'][:35]:<35} "
            f"{row['role'][:6]:<6} "
            f"{wwar_val:>5.2f} "
            f"{row['wWAR_grade']:<5} "
            f"{(peak_val or 0):>5.1f} "
            f"{row['peak_grade']:<4} "
            f"{row['avail_pct_computed']:>8} "
            f"{row['avail_grade']:<4}"
        )
        previewed += 1
        if previewed >= 30:
            print(f"  ... ({computed - 30} more)")
            break


if __name__ == "__main__":
    process()
