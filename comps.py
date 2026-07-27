#!/usr/bin/env python3
"""
comps.py — Phase 3/4: find historical trade comparables for a player profile.

Usage:
    python comps.py --war 4.5 --age 27 --years 3 --status signed
    python comps.py --war 1.5 --age 25 --years 4 --status pre-arb --position IF
    python comps.py --war 4.9 --age 26 --years 4 --status signed --position OF --salary 7
    python comps.py --player "Corbin Carroll"   # pull profile from player_value.py output

Matching logic:
    WAR window        : ±1.5 fWAR
    Age window        : ±3 years
    Years control     : ±2 years
    Contract status   : loosely matched (arb/pre-arb/signed/rental grouped)
    Position          : hard filter when specified
    Salary            : optional; adds WAR-salary ratio penalty + end-age decline penalty

Auto-expansion: if min_comps > 0 and fewer comps found than requested, windows
expand up to 2× before giving up. Expanded windows are flagged in output.

Scoring: each matched trade is scored by distance; closest comps shown first.
"""

import csv
import math
import argparse
from pathlib import Path

TRADES_CSV = Path(__file__).parent / "trades.csv"

# How far from the query each field is allowed before a comp is excluded
WAR_WINDOW    = 1.5
AGE_WINDOW    = 3
YEARS_WINDOW  = 2

DOLLAR_PER_WAR = 7.0  # current market rate; calibrated 2026-06-16 via calc_dollar_per_war_auto.py

# Key = trade year; value = median $/fWAR from the offseason FA class that preceded that season.
# 2024-2026 calibrated via calc_dollar_per_war_auto.py --war-years 2 (2-yr WAR avg, tighter mean/median).
# Pre-2024 approximate (FanGraphs Market Reports; 4-yr avg method).
# 2026-07-06: switched to --war-years 2; 2024 7.8, 2025 7.5, 2026 8.5.
DOLLAR_PER_WAR_BY_YEAR = {
    2015: 6.7, 2016: 7.3, 2017: 7.5, 2018: 8.0,
    2019: 8.4, 2020: 8.4, 2021: 8.0,
    2022: 7.0, 2023: 7.2, 2024: 7.8, 2025: 7.5, 2026: 8.5,
}


def _trade_year(trade_date_str):
    for part in trade_date_str.replace("-", "/").split("/"):
        if len(part) == 4 and part.isdigit():
            return int(part)
    return 2020


def _rate_for_year(year):
    if year in DOLLAR_PER_WAR_BY_YEAR:
        return DOLLAR_PER_WAR_BY_YEAR[year]
    years = sorted(DOLLAR_PER_WAR_BY_YEAR)
    return DOLLAR_PER_WAR_BY_YEAR[years[0] if year < years[0] else years[-1]]

AVAIL_GRADE_SCORES = {"A+": 5, "A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
AVAIL_PENALTY_PER_STEP = 0.7

CONTRACT_GROUPS = {
    "pre-arb": "team_control",
    "arb1":    "team_control",
    "arb2":    "team_control",
    "arb3":    "team_control",
    "arb":     "team_control",
    "signed":  "signed",
    "rental":  "rental",
    # opt-out: team declined a club option on a multi-year deal; groups with signed
    # (multi-year deal heritage) not rental (arb players)
    "opt-out": "signed",
}

RETURN_TIER_LABELS = {
    10: "franchise-altering",
    9:  "elite package",
    8:  "very high",
    7:  "high",
    6:  "mid-high",
    5:  "mid",
    4:  "mid-low",
    3:  "depth",
    2:  "minimal",
    1:  "salary-dump assist",
    0:  "(FA signing — no trade return)",
}


def load_trades(trade_type=None):
    trades = []
    with open(TRADES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                row["age_at_trade"]            = int(row["age_at_trade"])
                row["years_control_remaining"] = int(row["years_control_remaining"])
                row["wWAR"]                    = float(row["wWAR"])
                row["war_yr1"]                 = float(row["war_yr1"]) if row.get("war_yr1") else None
                row["return_tier"]             = int(row["return_tier"])
                row["is_pitcher"]              = row["is_pitcher"] == "1"
                row["salary_m"]                = float(row["salary_m"])
                if trade_type and row.get("trade_type", "").lower() != trade_type.lower():
                    continue
                trades.append(row)
            except (ValueError, KeyError):
                pass
    return trades


def _contract_group(status):
    return CONTRACT_GROUPS.get(status.lower(), status.lower())


def score_comp(trade, war, age, years, status, position=None, salary=None,
               avail_grade=None,
               war_window=WAR_WINDOW, age_window=AGE_WINDOW, years_window=YEARS_WINDOW,
               trade_type=None, exclude_player=None):
    """
    Returns a similarity score (lower = closer match). Returns None if outside hard windows.

    When salary is provided, two additional penalties apply:
      - WAR-salary ratio: how team-friendly/market-rate is the player?
        ratio = salary / (war * DOLLAR_PER_WAR); 0.15 = massively team-friendly, 1.0 = market rate
        Penalizes comps whose ratio diverges from the query's ratio.
      - End-age decline: age + years_remaining = projected contract end age.
        Penalizes comps whose end age diverges (older + longer = more decline years priced in).
    """
    war_diff   = abs(trade["wWAR"] - war)
    age_diff   = abs(trade["age_at_trade"]            - age)
    years_diff = abs(trade["years_control_remaining"] - years)

    if war_diff > war_window or age_diff > age_window or years_diff > years_window:
        return None

    if trade["return_tier"] == 0:
        return None

    if exclude_player and trade["player_name"].lower() == exclude_player.lower():
        return None

    combined = trade.get("return_summary", "") + trade.get("notes", "")
    if "[RETURN UNVERIFIED]" in combined:
        return None

    contract_penalty = 0 if _contract_group(trade["contract_status"]) == _contract_group(status) else 5

    if position and position.upper() != trade["position_group"].upper():
        if (position.upper() in ("SP", "RP")) != trade["is_pitcher"]:
            return None
        return None

    score = (war_diff * 4) + (age_diff * 1.5) + (years_diff * 2) + contract_penalty

    if avail_grade is not None:
        comp_grade = trade.get("avail_grade", "")
        if avail_grade in AVAIL_GRADE_SCORES and comp_grade in AVAIL_GRADE_SCORES:
            grade_diff = abs(AVAIL_GRADE_SCORES[avail_grade] - AVAIL_GRADE_SCORES[comp_grade])
            score += grade_diff * AVAIL_PENALTY_PER_STEP

    if salary is not None:
        market_value = max(war * DOLLAR_PER_WAR, 0.74)
        query_ratio  = salary / market_value
        comp_rate    = _rate_for_year(_trade_year(trade["trade_date"]))
        comp_market  = max(trade["wWAR"] * comp_rate, 0.74)
        comp_ratio   = trade["salary_m"] / comp_market
        score += abs(query_ratio - comp_ratio) * 8

        query_end_age = age + years
        comp_end_age  = trade["age_at_trade"] + trade["years_control_remaining"]
        score += abs(query_end_age - comp_end_age) * 0.8

    return round(score, 2)


def find_comps(war, age, years, status, position=None, salary=None, top_n=5,
               avail_grade=None,
               war_window=WAR_WINDOW, age_window=AGE_WINDOW, years_window=YEARS_WINDOW,
               trade_type=None, exclude_player=None, min_comps=0):
    """
    Returns (comps, expanded_mult) tuple.
    - comps: list of (score, trade_dict), sorted by score ascending, up to top_n.
    - expanded_mult: 1.0 if default windows used; 1.5 or 2.0 if auto-expanded.
    - min_comps: if > 0, auto-expand windows up to 2× until this many comps found.
    """
    trades = load_trades(trade_type=trade_type)

    steps = [(1.0, war_window, age_window, years_window)]
    if min_comps > 0:
        steps += [
            (1.5, round(war_window * 1.5, 1), int(age_window * 1.5 + 0.5), int(years_window * 1.5 + 0.5)),
            (2.0, round(war_window * 2.0, 1), int(age_window * 2.0), int(years_window * 2.0)),
        ]

    result, used_mult = [], 1.0
    for mult, ww, aw, yw in steps:
        scored = []
        for t in trades:
            s = score_comp(t, war, age, years, status, position, salary,
                           avail_grade=avail_grade,
                           war_window=ww, age_window=aw, years_window=yw,
                           exclude_player=exclude_player)
            if s is not None:
                scored.append((s, t))
        scored.sort(key=lambda x: x[0])
        result = scored[:top_n]
        used_mult = mult
        if len(result) >= min_comps:
            break

    return result, used_mult


def print_comps(comps, war, age, years, status, position=None, salary=None,
                avail_grade=None, expanded_mult=1.0):
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  TRADE COMPARABLES")
    salary_str = ""
    if salary is not None:
        query_pct = salary / max(war * DOLLAR_PER_WAR, 0.74) * 100
        salary_str = f" | ${salary:.1f}M ({query_pct:.0f}% of market)"
    avail_str = f" | Avail {avail_grade}" if avail_grade else ""
    end_age_str = f" | End age {age + years}" if years > 0 else ""
    print(f"  Query: {war:.1f} WAR | Age {age} | {years} yr(s) control{end_age_str} | {status.upper()}"
          + (f" | {position.upper()}" if position else "")
          + avail_str
          + salary_str)
    ww = round(WAR_WINDOW * expanded_mult, 1)
    aw = int(AGE_WINDOW * expanded_mult + 0.5)
    yw = int(YEARS_WINDOW * expanded_mult + 0.5)
    expand_note = "  [auto-expanded — few comps at default windows]" if expanded_mult > 1.0 else ""
    print(f"  Windows: WAR ±{ww} | Age ±{aw} | Control ±{yw}{expand_note}")
    if position:
        print(f"  Position filter: {position.upper()} (hard exclude)")
    print(sep)

    if not comps:
        print("\n  No comparables found within windows.")
        print("  Try widening: --war-window, --age-window, or loosen --status\n")
        print(f"{sep}\n")
        return

    tiers = [c[1]["return_tier"] for c in comps]
    avg_tier = sum(tiers) / len(tiers)
    tier_label = RETURN_TIER_LABELS.get(round(avg_tier), "")
    print(f"\n  {len(comps)} comp(s) found  |  avg return tier: {avg_tier:.1f}/10 ({tier_label})\n")

    for rank, (score, t) in enumerate(comps, 1):
        unverified = "[PARTIALLY UNVERIFIED]" in t.get("return_summary", "")
        print(f"  {'─'*66}")
        print(f"  #{rank}  {t['player_name']}  ({t['from_team']} → {t['to_team']}, {t['trade_date']})")
        print(f"       Match score: {score:.1f}  |  Season: {t['season']}  |  Type: {t['trade_type']}")
        yr1_str = f" (yr1: {t['war_yr1']:.1f})" if t.get("war_yr1") else ""
        trend   = t.get("trend_label", "")
        trend_str = f" | {trend}" if trend else ""
        comp_avail = t.get("avail_grade", "")
        avail_disp = f" | Avail {comp_avail}" if comp_avail else ""
        ctrl_yrs = t["years_control_remaining"]
        end_age = t["age_at_trade"] + ctrl_yrs
        end_age_disp = f" | End {end_age}" if ctrl_yrs > 0 else ""
        print(f"       Profile: wWAR {t['wWAR']:.1f}{yr1_str}{trend_str} | Age {t['age_at_trade']} | "
              f"{ctrl_yrs} yr ctrl{end_age_disp} | {t['contract_status'].upper()} | ${t['salary_m']:.1f}M"
              + avail_disp)
        if salary is not None:
            comp_year   = _trade_year(t["trade_date"])
            comp_rate   = _rate_for_year(comp_year)
            comp_market = max(t["wWAR"] * comp_rate, 0.74)
            comp_pct    = t["salary_m"] / comp_market * 100
            query_pct   = salary / max(war * DOLLAR_PER_WAR, 0.74) * 100
            gap         = comp_pct - query_pct
            gap_str     = f"+{gap:.0f}% costlier" if gap > 0 else f"{abs(gap):.0f}% cheaper"
            print(f"       Salary ratio : {comp_pct:.0f}% of {comp_year} market  "
                  f"(query {query_pct:.0f}% → comp is {gap_str})")
        print(f"       Return tier: {t['return_tier']}/10 — {RETURN_TIER_LABELS.get(t['return_tier'], '')}")
        print(f"       Key pieces : {t['key_pieces']}")
        summary = t['return_summary'].replace("[PARTIALLY UNVERIFIED]", "").strip()
        print(f"       Summary    : {summary}")
        if t.get("notes"):
            print(f"       Notes      : {t['notes']}")
        if unverified:
            print(f"       [!] Return details partially unverified — verify before citing")
    print(f"  {'─'*66}")

    low_tier  = min(tiers)
    high_tier = max(tiers)
    print(f"\n  Return range based on comps: {low_tier}/10 – {high_tier}/10")
    print(f"  ({RETURN_TIER_LABELS.get(low_tier,'')} to {RETURN_TIER_LABELS.get(high_tier,'')})")

    # Pitcher variance note
    if position and position.upper() in ("SP", "RP"):
        role = "SP" if position.upper() == "SP" else "RP"
        extra = (" Includes Tommy John and role-change-to-bullpen risk." if role == "SP" else
                 " Role changes and declining leverage can compress WAR quickly.")
        print(f"\n  [Pitcher pool] WAR variance is elevated vs. hitter comps.{extra}"
              f"\n  Treat this return range as ~±0.5 tiers wider than the numbers suggest.")
    elif not position:
        pitcher_count = sum(1 for _, t in comps if t.get("is_pitcher"))
        hitter_count  = len(comps) - pitcher_count
        if pitcher_count > 0 and hitter_count > 0:
            print(f"\n  [!] Mixed pool: {pitcher_count} pitcher / {hitter_count} position player comp(s)."
                  f"\n  Add --position to tighten the pool — pitcher and hitter WAR are not directly comparable.")

    print(f"\n{sep}\n")


def main():
    ap = argparse.ArgumentParser(description="Find historical trade comparables")
    ap.add_argument("--war",      type=float, required=True, help="Player WAR (prior season or projection)")
    ap.add_argument("--age",      type=int,   required=True, help="Player age at time of trade")
    ap.add_argument("--years",    type=int,   required=True, help="Years of control remaining")
    ap.add_argument("--status",   required=True,
                    choices=["pre-arb","arb1","arb2","arb3","arb","signed","rental","opt-out"],
                    help="Contract status")
    ap.add_argument("--position", help="Position group: SP RP C 1B 2B 3B SS OF DH IF UTIL")
    ap.add_argument("--salary",   type=float, help="Player salary $M — enables salary-ratio scoring and year-adjusted comp display")
    ap.add_argument("--top",      type=int, default=5, help="Number of comps to show (default: 5)")
    ap.add_argument("--war-window",   type=float, default=WAR_WINDOW,
                    help=f"WAR window (default: {WAR_WINDOW})")
    ap.add_argument("--age-window",   type=int,   default=AGE_WINDOW,
                    help=f"Age window (default: {AGE_WINDOW})")
    ap.add_argument("--years-window", type=int,   default=YEARS_WINDOW,
                    help=f"Years control window (default: {YEARS_WINDOW})")
    ap.add_argument("--trade-type",   choices=["deadline", "offseason"],
                    help="Filter comps to deadline or offseason trades only")
    ap.add_argument("--min-comps", type=int, default=0,
                    help="Auto-expand windows to hit this many comps (0 = no expansion)")
    ap.add_argument("--avail-grade", choices=["A+", "A", "B", "C", "D", "F"],
                    help="Player availability grade — penalizes comps with very different health profiles")
    args = ap.parse_args()

    comps, expanded_mult = find_comps(
        args.war, args.age, args.years, args.status, args.position, args.salary, args.top,
        avail_grade=args.avail_grade,
        war_window=args.war_window, age_window=args.age_window, years_window=args.years_window,
        trade_type=args.trade_type, min_comps=args.min_comps,
    )
    print_comps(comps, args.war, args.age, args.years, args.status, args.position, args.salary,
                avail_grade=args.avail_grade, expanded_mult=expanded_mult)


if __name__ == "__main__":
    main()
