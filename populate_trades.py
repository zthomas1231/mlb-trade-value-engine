#!/usr/bin/env python3
"""
populate_trades.py — Fast trade entry for trades.csv.

Fetches WAR (from FanGraphs via pybaseball) and age (from MLB Stats API)
automatically. You supply the facts you know from watching baseball.

Usage:
    python populate_trades.py "Manny Machado" 2018-07-18 BAL LAD rental 0 7
    python populate_trades.py "Jose Berrios" 2021-07-30 MIN TOR arb3 1 7 --pitcher
    python populate_trades.py "Daulton Varsho" 2022-12-08 ARI TOR arb1 4 7 --salary 0.72 --position C

Positional args:
    player          Full player name
    trade_date      Trade date (YYYY-MM-DD or M/D/YYYY)
    from_team       Selling team abbreviation (e.g. BAL)
    to_team         Buying team abbreviation (e.g. LAD)
    status          pre-arb | arb1 | arb2 | arb3 | signed | rental
    years           Years of control remaining (integer)
    tier            Return tier 1-10

Options:
    --war FLOAT     Override WAR (skips FanGraphs lookup)
    --age INT       Override age at time of trade (skips MLB API lookup)
    --pitcher       Treat as pitcher for WAR lookup
    --salary FLOAT  Salary in $M (year of trade, defaults to 0)
    --position STR  Position group (SP RP C 1B 2B 3B SS OF DH IF)
    --notes STR     Notes field text
    --verified      Mark as verified (removes [PARTIALLY UNVERIFIED] flag)
"""

import sys
import csv
import argparse
import datetime
import math
import unicodedata
from pathlib import Path

import pandas as pd
import requests
import pybaseball

TRADES_CSV = Path(__file__).parent / "trades.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TradeValueEngine/1.0)"}
ONEDRIVE_BASE = "C:/Users/zach.thomas/OneDrive - Driveline Baseball/"

FIELDNAMES = [
    "trade_id", "player_name", "trade_date", "season", "trade_type",
    "from_team", "to_team", "num_teams", "position_group", "is_pitcher", "age_at_trade",
    "contract_status", "years_control_remaining", "salary_m", "aav_m",
    "war_yr0", "g_yr0", "war_yr1", "g_yr1", "war_yr2", "g_yr2",
    "war_yr3", "g_yr3", "war_peak", "covid_yr", "wWAR", "war_trend",
    "trend_label", "yr0_rate", "avail_pct", "avail_grade", "peak_gap",
    "key_pieces", "return_summary", "return_tier", "return_prospect_grades", "notes",
]


def next_trade_id():
    if not TRADES_CSV.exists():
        return "T001"
    with open(TRADES_CSV, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        first_col = reader.fieldnames[0] if reader.fieldnames else "trade_id"
        ids = []
        for r in reader:
            val = r.get("trade_id") or r.get(first_col, "")
            if val.startswith("T"):
                ids.append(val)
    nums = [int(tid[1:]) for tid in ids if tid[1:].isdigit()]
    return f"T{max(nums) + 1:03d}" if nums else "T001"


def parse_date(date_str):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(date_str, fmt).date()
        except ValueError:
            pass
    parts = date_str.split("/")
    if len(parts) == 3:
        return datetime.date(int(parts[2]), int(parts[0]), int(parts[1]))
    raise ValueError(f"Cannot parse date: {date_str!r}")


def format_date(d):
    return f"{d.month}/{d.day}/{d.year}"


def lookup_player(name, mlb_id_override=None, fg_id_override=None):
    if mlb_id_override or fg_id_override:
        return fg_id_override, mlb_id_override

    # For package trades ("A + B + C"), look up only the primary player
    lookup_name = name.split("+")[0].strip()
    parts = lookup_name.split()
    first, last = parts[0], " ".join(parts[1:])
    try:
        df = pybaseball.playerid_lookup(last, first, fuzzy=True)
    except Exception:
        return None, None
    if df.empty:
        return None, None

    # Prefer players who were active in recent years (avoids matching historical players with same name)
    _played = pd.to_numeric(df.get("mlb_played_last", df.get("mlb_played_first", pd.Series([0]*len(df)))), errors="coerce").fillna(0)
    recent = df[_played >= 2010]
    row = recent.iloc[0] if not recent.empty else df.iloc[0]

    fg = row.get("key_fangraphs")
    mlb = row.get("key_mlbam")
    fg_id = None if (fg is None or (isinstance(fg, float) and math.isnan(fg))) else str(int(fg))
    mlb_id = None if (mlb is None or (isinstance(mlb, float) and math.isnan(mlb))) else int(mlb)

    # Print who was matched so the user can catch a wrong hit
    matched_name = f"{row.get('name_first', '')} {row.get('name_last', '')}".strip()
    last_year = row.get("mlb_played_last", "?")
    print(f"  Matched: {matched_name} (last MLB year: {last_year})")

    return fg_id, mlb_id


def fetch_birth_and_pos(mlb_id):
    resp = requests.get(
        f"https://statsapi.mlb.com/api/v1/people/{mlb_id}",
        headers=HEADERS, timeout=15,
    )
    resp.raise_for_status()
    person = resp.json().get("people", [{}])[0]
    return person.get("birthDate", ""), person.get("primaryPosition", {}).get("abbreviation", "?")


def age_at(birth_str, trade_date):
    birth = datetime.datetime.strptime(birth_str, "%Y-%m-%d").date()
    age = trade_date.year - birth.year
    if (trade_date.month, trade_date.day) < (birth.month, birth.day):
        age -= 1
    return age


def _xlsx_path(year, is_pitcher):
    if is_pitcher:
        double = Path(f"{ONEDRIVE_BASE}{year}__pitching_war_csv.xlsx")
        single = Path(f"{ONEDRIVE_BASE}{year}_pitching_war_csv.xlsx")
        return str(double) if double.exists() else str(single)
    return f"{ONEDRIVE_BASE}{year}_batting_war_csv.xlsx"


def _xlsx_war(player_name, is_pitcher, year):
    path = _xlsx_path(year, is_pitcher)
    if not Path(path).exists():
        return None
    df = pd.read_excel(path, header=0)
    war_idx = 20 if is_pitcher else 21

    def _ascii(s):
        return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()

    # For package trades ("A + B + C"), search on the first player only
    search_name = player_name.split("+")[0].strip()
    parts = _ascii(search_name).split()
    for _, row in df.iterrows():
        if all(p in _ascii(row.iloc[0]) for p in parts):
            try:
                war = float(row.iloc[war_idx])
                if not math.isnan(war):
                    return round(war, 1)
            except (ValueError, TypeError):
                continue
    return None


def fetch_war(name, trade_date, is_pitcher, fg_id=None):
    # Jan-May: prior season is the reference. Jun-Dec: current season.
    war_year = trade_date.year - 1 if trade_date.month <= 5 else trade_date.year
    print(f"  Fetching {war_year} fWAR from local xlsx...")
    war = _xlsx_war(name, is_pitcher, war_year)
    if war is not None:
        return war, war_year
    # Fallback one year (e.g. player missed war_year due to injury)
    print(f"  Not found in {war_year}, trying {war_year - 1}...")
    war = _xlsx_war(name, is_pitcher, war_year - 1)
    if war is not None:
        return war, war_year - 1
    return None, war_year


def pos_group(raw_pos, is_pitcher):
    if is_pitcher:
        return "SP"
    return {"LF": "OF", "CF": "OF", "RF": "OF"}.get(raw_pos.upper(), raw_pos.upper()) if raw_pos else "?"


def main():
    ap = argparse.ArgumentParser(description="Fast trade entry for trades.csv")
    ap.add_argument("player")
    ap.add_argument("trade_date")
    ap.add_argument("from_team")
    ap.add_argument("to_team")
    ap.add_argument("status", choices=["pre-arb", "arb1", "arb2", "arb3", "signed", "rental"])
    ap.add_argument("years", type=int)
    ap.add_argument("tier", type=int)
    ap.add_argument("--war",      type=float, help="Override WAR")
    ap.add_argument("--age",      type=int,   help="Override age at trade")
    ap.add_argument("--mlb-id",   type=int,   help="Override MLBAM player ID (skips name lookup)")
    ap.add_argument("--fg-id",    help="Override FanGraphs player ID (skips name lookup)")
    ap.add_argument("--pitcher",  action="store_true")
    ap.add_argument("--salary",   type=float, default=0.0, help="Salary in $M")
    ap.add_argument("--position", help="Position group override")
    ap.add_argument("--notes",        default="")
    ap.add_argument("--relief-role",  default=None, help="Relief role (closer/setup/middle) — stored in notes")
    ap.add_argument("--verified",     action="store_true", help="Mark as verified")
    args = ap.parse_args()

    trade_date = parse_date(args.trade_date)
    trade_id   = next_trade_id()
    trade_type = "deadline" if 6 <= trade_date.month <= 9 else "offseason"
    season     = trade_date.year

    print(f"\nLooking up {args.player!r}...")
    fg_id, mlb_id = lookup_player(args.player, getattr(args, "mlb_id", None), getattr(args, "fg_id", None))
    print(f"  FanGraphs ID: {fg_id or 'not found'}")

    # Age
    current_age = args.age
    raw_pos = "?"
    if mlb_id and current_age is None:
        try:
            birth_str, raw_pos = fetch_birth_and_pos(mlb_id)
            current_age = age_at(birth_str, trade_date)
            print(f"  Age at {format_date(trade_date)}: {current_age}  (born {birth_str})")
        except Exception as e:
            print(f"  WARNING: age lookup failed: {e}")

    if current_age is None:
        print("ERROR: Could not determine age. Use --age.")
        sys.exit(1)

    # Position
    position = args.position or pos_group(raw_pos, args.pitcher)

    # WAR
    war = args.war
    war_year = None
    if war is None:
        war, war_year = fetch_war(args.player, trade_date, args.pitcher, fg_id)
        if war is None:
            print("ERROR: Could not fetch WAR. Use --war to supply it manually.")
            sys.exit(1)
        print(f"  WAR ({war_year}): {war}")
    else:
        print(f"  WAR (override): {war}")

    unverified_flag = "" if args.verified else " [PARTIALLY UNVERIFIED]"

    row = {
        "trade_id":                trade_id,
        "player_name":             args.player,
        "trade_date":              format_date(trade_date),
        "season":                  season,
        "trade_type":              trade_type,
        "from_team":               args.from_team.upper(),
        "to_team":                 args.to_team.upper(),
        "position_group":          position,
        "is_pitcher":              1 if args.pitcher else 0,
        "age_at_trade":            current_age,
        "contract_status":         args.status,
        "years_control_remaining": args.years,
        "salary_m":                args.salary,
        "aav_m":                   args.salary,
        "war_yr1":                 war,
        "wWAR":                    war,  # single-season approx; re-run merge_war_reference.py to recompute
        "return_tier":             args.tier,
        "return_summary":          f"[Fill in return summary]{unverified_flag}",
        "key_pieces":              "",
        "notes":                   args.notes,
    }

    with open(TRADES_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerow(row)

    print(f"\n  ✓ Added {trade_id}: {args.player} ({args.from_team.upper()} → {args.to_team.upper()})")
    print(f"    Age {current_age} | {war} WAR | {args.status} | {args.years}yr ctrl | Tier {args.tier}")
    print(f"    Open trades.csv to fill in: return_summary and key_pieces\n")


if __name__ == "__main__":
    main()
