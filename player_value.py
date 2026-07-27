#!/usr/bin/env python3
"""
player_value.py — MLB trade value calculator (Phase 1)

Usage:
    python player_value.py "Corbin Carroll"
    python player_value.py "Gerrit Cole" --pitcher
    python player_value.py "Corbin Carroll" --age 23 --war 4.2

Contract data source (default: FanGraphs Roster Resource API):
    python player_value.py "Corbin Carroll" --spotrac
        Force Spotrac scraping instead of FanGraphs (fallback if FG ID lookup fails).

    python player_value.py "Corbin Carroll" --fg-csv payroll.csv
        Use a CSV manually exported from fangraphs.com/roster-resource/payroll.
        Expected columns: Name, Status, Salary (or AAV), Years.
"""

import sys
import re
import csv
import argparse
import math
import datetime
import unicodedata
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd

import requests
from bs4 import BeautifulSoup
import pybaseball
from comps import find_comps, print_comps

# ── Constants ──────────────────────────────────────────────────────────────────
DOLLARS_PER_WAR  = 7.0    # $M per fWAR, 2025 FA market rate — calibrated 2026-06-16 via calc_dollar_per_war_auto.py
DISCOUNT_RATE    = 0.05
CONTROL_DISCOUNT = 0.875  # controllability discount on surplus
MLB_MINIMUM      = 0.740  # $M, 2025 MLB minimum salary
CURRENT_YEAR     = datetime.date.today().year
MAX_PROJECTION_YEARS = 8  # cap speculation beyond 8 years

# Reliever leverage presets (gmLI proxies by role)
RELIEF_ROLE_LEVERAGE = {"closer": 1.8, "setup": 1.4, "middle": 1.1}

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TradeValueEngine/1.0)"}
ONEDRIVE_BASE = "C:/Users/zach.thomas/OneDrive - Driveline Baseball/"


# ── Aging curve ────────────────────────────────────────────────────────────────
def aging_delta(age):
    if age < 27:  return  0.25
    if age <= 30: return  0.00
    if age <= 33: return -0.50
    return -0.75


def project_wars(war_y1, current_age, n=3):
    wars = [war_y1]
    for i in range(1, n):
        wars.append(max(0.0, wars[-1] + aging_delta(current_age + i)))
    return wars


# ── Arb salary estimation ──────────────────────────────────────────────────────
ARB_RATES = {1: 0.40, 2: 0.60, 3: 0.80, 4: 0.90}

def arb_salary(market_value_m, arb_year):
    return market_value_m * ARB_RATES.get(arb_year, 0.40)


# ── Player ID lookup ───────────────────────────────────────────────────────────
def lookup_player_ids(player_name):
    """Returns (fg_id, mlbam_id). Either may be None."""
    parts = player_name.strip().split()
    first, last = parts[0], " ".join(parts[1:])
    try:
        df = pybaseball.playerid_lookup(last, first, fuzzy=True)
    except Exception as e:
        print(f"  playerid_lookup error: {e}")
        return None, None
    if df.empty:
        return None, None
    row = df.iloc[0]
    fg  = row.get("key_fangraphs")
    mlb = row.get("key_mlbam")
    fg_id    = None if (fg  is None or (isinstance(fg,  float) and math.isnan(fg)))  else str(int(fg))
    mlbam_id = None if (mlb is None or (isinstance(mlb, float) and math.isnan(mlb))) else int(mlb)
    matched  = f"{row.get('name_first', '')} {row.get('name_last', '')}".strip()
    print(f"  Matched: {matched}")
    return fg_id, mlbam_id


# ── Local fWAR xlsx lookup (replaces broken FanGraphs leaderboard API) ─────────
def _xlsx_path(year, is_pitcher):
    if is_pitcher:
        double = Path(f"{ONEDRIVE_BASE}{year}__pitching_war_csv.xlsx")
        single = Path(f"{ONEDRIVE_BASE}{year}_pitching_war_csv.xlsx")
        return str(double) if double.exists() else str(single)
    return f"{ONEDRIVE_BASE}{year}_batting_war_csv.xlsx"


def fetch_war_from_xlsx(player_name, is_pitcher, year):
    """
    Look up fWAR from local OneDrive xlsx (qual=0). Returns (war, games) or (None, None).
    Column layout: WAR at index 21 (bat) / 20 (pit); G at index 2 (bat) / 5 (pit).
    """
    path = _xlsx_path(year, is_pitcher)
    if not Path(path).exists():
        return None, None
    df = pd.read_excel(path, header=0)
    war_idx = 20 if is_pitcher else 21
    g_idx   =  5 if is_pitcher else  2

    def _ascii(s):
        return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()

    parts = _ascii(player_name).split()
    for _, row in df.iterrows():
        if all(p in _ascii(row.iloc[0]) for p in parts):
            try:
                war = float(row.iloc[war_idx])
                g   = int(row.iloc[g_idx])
                if not math.isnan(war):
                    return round(war, 1), g
            except (ValueError, TypeError):
                continue
    return None, None


# ── MLB Stats API — age and position from MLBAM ID ────────────────────────────
def fetch_mlb_player_info(mlbam_id):
    resp = requests.get(
        f"https://statsapi.mlb.com/api/v1/people/{mlbam_id}",
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    person = resp.json().get("people", [{}])[0]
    return {
        "age": person.get("currentAge"),
        "position": person.get("primaryPosition", {}).get("abbreviation", "?"),
    }


def fetch_current_season_gs(mlbam_id, is_pitcher):
    """Fetch current-season GS (SP) or G (hitter/RP) from MLB Stats API."""
    group = "pitching" if is_pitcher else "hitting"
    resp = requests.get(
        f"https://statsapi.mlb.com/api/v1/people/{mlbam_id}/stats",
        params={"stats": "season", "season": CURRENT_YEAR, "group": group},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    splits = resp.json().get("stats", [{}])[0].get("splits", [])
    if not splits:
        return None
    stat = splits[0].get("stat", {})
    return stat.get("gamesStarted") if is_pitcher else stat.get("gamesPlayed")


# ── FanGraphs projection API (ZiPS, THE BAT X, Steamer, etc.) ─────────────────
def fetch_fg_projection(player_name, is_pitcher, proj_type, fg_id=None):
    """
    Fetch a FanGraphs projection row for one player.
    proj_type: 'zips' | 'thebatx' | 'steamer' | 'atc' | 'fangraphsdc'
    Returns the matching row dict, or None if not found.
    """
    stats = "pit" if is_pitcher else "bat"
    url = (
        f"https://www.fangraphs.com/api/projections"
        f"?type={proj_type}&stats={stats}&pos=all&team=0&players=0"
    )
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if fg_id:
        for row in data:
            if str(row.get("playerid", "")) == fg_id:
                return row

    name_lower = player_name.lower()
    for row in data:
        if row.get("PlayerName", "").lower() == name_lower:
            return row

    parts = name_lower.split()
    matches = [r for r in data if all(p in r.get("PlayerName", "").lower() for p in parts)]
    if len(matches) > 1:
        print(f"  Multiple {proj_type} matches — using first: "
              + ", ".join(f"{m['PlayerName']} ({m.get('Team','?')})" for m in matches))
    return matches[0] if matches else None


# Keep old name as alias so deadline_board.py / any other callers don't break
def fetch_zips(player_name, is_pitcher, fg_id=None):
    return fetch_fg_projection(player_name, is_pitcher, "zips", fg_id)


# ── FanGraphs Roster Resource contract data ───────────────────────────────────
# Endpoint discovered in JS bundle: /api/roster-resource/contracts/player?playerid={fg_id}
# Publicly accessible (no auth), returns per-year salary breakdown + arb projections.
# This is the default contract source when a FG player ID is available.

FG_CONTRACTS_API = "https://www.fangraphs.com/api/roster-resource/contracts/player"

# Expected FanGraphs CSV columns (export from /roster-resource/payroll):
# Name, Team, Pos, Age, Status, Salary, Years, AAV, playerid
FG_CSV_SALARY_COL  = "Salary"
FG_CSV_NAME_COL    = "Name"
FG_CSV_STATUS_COL  = "Status"
FG_CSV_YEARS_COL   = "Years"
FG_CSV_AAV_COL     = "AAV"


def fetch_fg_contract(fg_id):
    """
    Fetch per-year contract data from FanGraphs Roster Resource.
    Requires a FanGraphs player ID. Returns None if player not found.
    """
    resp = requests.get(FG_CONTRACTS_API, headers=HEADERS, params={"playerid": fg_id}, timeout=15)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None
    return _fg_api_to_contract(data[0])


def _fg_api_to_contract(data):
    """Normalize a FanGraphs contracts/player API response to our contract dict."""
    cs = data["contractSummary"]
    years_data = data.get("contractYears", [])

    service_time = float(cs.get("servicetime") or 0.0)
    future_years = {y["Season"]: y for y in years_data if y["Season"] >= CURRENT_YEAR}

    current_row = future_years.get(CURRENT_YEAR, {})
    type_str = current_row.get("Type", "").upper()
    arb_year_now = int(current_row.get("ArbYear") or 0)

    if "PRE" in type_str or (current_row.get("isEstimate") and arb_year_now == 0):
        status = "pre-arb"
    elif arb_year_now > 0:
        status = "arb"
    elif "FREE" in type_str or "UFA" in type_str:
        status = "ufa"
    else:
        status = "signed"

    yearly = []
    for yr in sorted(future_years):
        row = future_years[yr]
        sal_m = round(row["BaseSalary"] / 1_000_000, 3) if row.get("BaseSalary") else None
        arb_yr = int(row.get("ArbYear") or 0)
        t = row.get("Type", "").upper()
        if arb_yr > 0:
            label = f"ARB {arb_yr}"
        elif "PRE" in t:
            label = "Pre-Arb"
        elif "CLUB" in t:
            label = "Club"
        elif "MUTUAL" in t:
            label = "Team"
        elif "PLAYER" in t:
            label = "Player"
        elif "FREE" in t or "UFA" in t:
            label = "UFA"
        else:
            label = ""
        yearly.append({"year": yr, "status": label, "salary_m": sal_m})

    options = []
    if cs.get("hasClubOption"):    options.append("club option")
    if cs.get("hasMutualOption"):  options.append("mutual option")
    if cs.get("hasVestingOption"): options.append("vesting option")

    aav_m = round(cs["AAV"] / 1_000_000, 2) if cs.get("AAV") else 0.0
    salaries = [r["salary_m"] for r in yearly if r["salary_m"] is not None]

    return {
        "status": status,
        "arb_year_now": arb_year_now if status == "arb" else None,
        "salaries": salaries,
        "yearly": yearly,
        "options": options,
        "service_time": service_time,
        "aav": aav_m,
        "source": "fangraphs",
        "payroll_note": cs.get("ContractSummaryPayrollNote", "") or "",
        "no_trade_note": cs.get("NoTradeNotes", "") or "",
    }


def load_fg_csv(csv_path, player_name, fg_id=None):
    """
    Load contract data from a manually exported FanGraphs Roster Resource CSV.
    Columns expected: Name, Status, Salary, AAV, Years (playerid optional).
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"FanGraphs CSV not found: {csv_path}")

    name_lower = player_name.lower()
    row = None
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if fg_id and str(r.get("playerid", "")) == str(fg_id):
                row = r
                break
            parts = name_lower.split()
            if all(p in r.get(FG_CSV_NAME_COL, "").lower() for p in parts):
                row = r
                break

    if row is None:
        return None

    return _fg_row_to_contract(row)


def _fg_row_to_contract(row):
    """Normalize a FanGraphs Roster Resource row to our contract dict format."""
    status_raw = row.get(FG_CSV_STATUS_COL, row.get("Status", "")).lower()
    if "pre" in status_raw:
        status = "pre-arb"
    elif "arb" in status_raw:
        status = "arb"
    else:
        status = "signed"

    def _parse_money(val):
        if not val:
            return 0.0
        raw = re.sub(r"[^0-9.]", "", str(val))
        return round(float(raw) / 1_000_000, 2) if float(raw or 0) > 1_000 else float(raw or 0)

    aav_raw  = row.get(FG_CSV_AAV_COL, row.get("AAV", ""))
    sal_raw  = row.get(FG_CSV_SALARY_COL, row.get("Salary", ""))
    aav      = _parse_money(aav_raw) or _parse_money(sal_raw)
    years    = int(row.get(FG_CSV_YEARS_COL, row.get("Years", 0)) or 0)

    # FG CSV doesn't give per-year breakdown — build estimated salary list from AAV
    salaries = [aav] * years if aav and years else []

    return {
        "status": status,
        "arb_year_now": None,
        "salaries": salaries,
        "yearly": [],
        "options": [],
        "service_time": None,
        "aav": aav,
        "source": "fangraphs",
    }


# ── Spotrac contract scraping ──────────────────────────────────────────────────
def fetch_spotrac(player_name):
    # /search/autocomplete/?q=NAME renders the actual player page server-side
    query = quote_plus(player_name)
    resp = requests.get(
        f"https://www.spotrac.com/search/autocomplete/?q={query}",
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return parse_spotrac(resp.text)


def parse_spotrac(html):
    soup = BeautifulSoup(html, "html.parser")

    # Find the detailed breakdown table: Year | Age | Service | Status | BaseSalary ...
    target_table = None
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all(["th", "td"])[:12]]
        header_str = " ".join(headers).lower()
        if "status" in header_str and "basesalary" in header_str.replace(" ", ""):
            target_table = table
            break

    # Build per-year rows from the table
    yearly = []        # list of {year, status, salary_m}
    service_time = None
    if target_table:
        rows = target_table.find_all("tr")
        # Identify column indices from header row
        hdr_cells = [td.get_text(strip=True) for td in rows[0].find_all(["td", "th"])]
        col_year = col_status = col_salary = col_service = None
        for i, h in enumerate(hdr_cells):
            hn = h.lower().replace(" ", "")
            if hn == "year":                col_year    = i
            if hn == "status":              col_status  = i
            if hn in ("basesalary",):       col_salary  = i
            if hn in ("service", "yos"):    col_service = i

        for row in rows[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 3:
                continue
            try:
                yr = int(cells[col_year]) if col_year is not None and col_year < len(cells) else None
            except (ValueError, TypeError):
                continue
            if yr is None or yr < CURRENT_YEAR:
                continue

            raw_status = cells[col_status].strip() if col_status is not None and col_status < len(cells) else ""
            raw_salary = cells[col_salary].strip() if col_salary is not None and col_salary < len(cells) else ""
            sal_m = None
            if raw_salary.startswith("$"):
                try:
                    sal_m = round(float(re.sub(r"[^0-9.]", "", raw_salary)) / 1_000_000, 3)
                except ValueError:
                    pass
            # Extract service time from the earliest future year row
            if service_time is None and col_service is not None and col_service < len(cells):
                raw_svc = cells[col_service].strip()
                try:
                    svc = float(raw_svc)
                    if 0.0 <= svc <= 7.0:
                        # Adjust back to CURRENT_YEAR: subtract years between now and this row
                        service_time = round(svc - (yr - CURRENT_YEAR), 3)
                except ValueError:
                    pass

            yearly.append({"year": yr, "status": raw_status, "salary_m": sal_m})

    # UFA rows are not controlled years — strip them before any downstream logic.
    yearly = [r for r in yearly if r["status"].upper() != "UFA"]

    # Determine overall contract status.
    # If the table has any blank-status or "club/team/vesting" rows after arb rows,
    # the player is on a signed extension (arb salaries are negotiated as part of the deal).
    def _is_extension_row(st):
        stl = st.lower()
        return st == "" or stl in ("club", "team", "signed", "extension", "vesting")

    has_extension_years = any(_is_extension_row(r["status"]) for r in yearly)
    spotrac_status = yearly[0]["status"] if yearly else ""

    if has_extension_years:
        status = "signed"
        arb_year_now = None
    elif "arb" in spotrac_status.lower() and any(c.isdigit() for c in spotrac_status):
        status = "arb"
        arb_year_now = int(re.search(r"\d", spotrac_status).group())
    elif "pre" in spotrac_status.lower():
        status = "pre-arb"
        arb_year_now = None
    elif spotrac_status.lower() in ("signed", "extension"):
        status = "signed"
        arb_year_now = None
    else:
        status = "unknown"
        arb_year_now = None

    # Compute AAV from signed (non-arb) years
    signed_salaries = [
        r["salary_m"] for r in yearly
        if r.get("salary_m") and _is_extension_row(r["status"])
    ]

    # Scan for a single-player contract summary table (Length / Value / Avg. Salary).
    # Skip tables that include a "Player" column — those are comparison/comps tables.
    contract_aav_from_table = 0.0
    for table in soup.find_all("table"):
        all_cells = [td.get_text(strip=True).lower() for td in table.find_all(["th", "td"])[:6]]
        hdr_str = " ".join(all_cells)
        if "player" in hdr_str:   # comps table, skip
            continue
        if "avg" in hdr_str and ("salary" in hdr_str or "annual" in hdr_str):
            for row in table.find_all("tr")[1:2]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                for cell in reversed(cells):
                    if "$" in cell:
                        raw = re.sub(r"[^0-9.]", "", cell)
                        try:
                            val = float(raw)
                            if 1_000_000 <= val <= 100_000_000:
                                contract_aav_from_table = round(val / 1_000_000, 2)
                            elif 1 <= val <= 100:
                                contract_aav_from_table = val
                        except ValueError:
                            pass
                        if contract_aav_from_table:
                            break
            if contract_aav_from_table:
                break

    salaries = [r["salary_m"] for r in yearly if r["salary_m"] is not None]

    text_lower = soup.get_text(" ", strip=True).lower()
    options = [
        opt for opt in
        ("team option", "player option", "mutual option", "vesting option", "club option")
        if opt in text_lower
    ]
    # Also capture options visible in the yearly status column
    for r in yearly:
        st = r["status"].lower()
        if st in ("club",) and "club option" not in options:
            options.append("club option")
        if st in ("team",) and "team option" not in options:
            options.append("team option")
        if st in ("player",) and "player option" not in options:
            options.append("player option")

    aav = (
        sum(signed_salaries) / len(signed_salaries) if signed_salaries
        else contract_aav_from_table
    )
    aav_m = re.search(r"avg\.?\s*annual\s*value[:\s]*\$\s*([\d,]+)", text_lower)
    if aav_m and not aav:
        raw_aav = float(aav_m.group(1).replace(",", ""))
        aav = raw_aav if raw_aav < 1_000 else raw_aav / 1_000_000

    return {
        "status": status,
        "arb_year_now": arb_year_now,
        "salaries": salaries,
        "yearly": yearly,
        "options": options,
        "service_time": service_time,
        "aav": aav,
    }


# ── Control year modeling ──────────────────────────────────────────────────────
def build_control_years(contract, current_age, war_projections):
    status      = contract["status"]
    service_time = contract.get("service_time") or 0.0
    arb_year_now = contract.get("arb_year_now") or 1

    # Build a year→salary lookup from Spotrac per-year data if available
    spotrac_salary = {
        r["year"]: r["salary_m"]
        for r in contract.get("yearly", [])
        if r.get("salary_m") is not None
    }

    # How many pre-arb years remain before arb kicks in
    pre_arb_left = max(0, int(3.0 - service_time)) if status == "pre-arb" else 0

    rows = []
    for i, war in enumerate(war_projections):
        yr  = CURRENT_YEAR + i
        age = current_age + i
        market_value = war * DOLLARS_PER_WAR
        discount_factor = 1.0 / ((1 + DISCOUNT_RATE) ** i)

        # Check if this year is a UFA year (no team control, no surplus).
        # Skip UFA detection if user overrode years and this year is within the override window
        # (FanGraphs commonly returns a trailing UFA entry for years that are actually under contract).
        years_override = contract.get("_years_override")
        spotrac_row = next((r for r in contract.get("yearly", []) if r["year"] == yr), {})
        is_ufa = spotrac_row.get("status", "").upper() == "UFA"
        if years_override is not None and i < years_override:
            is_ufa = False  # override wins; user knows the correct contract length
        if is_ufa:
            salary = market_value
            salary_type = "UFA"
        # Use Spotrac salary if we have it for this year
        elif yr in spotrac_salary:
            salary = spotrac_salary[yr]
            raw_st = spotrac_row.get("status", "")
            salary_type = raw_st if raw_st else contract.get("source", "contract")
        elif status == "signed":
            salary = contract["aav"] if contract["aav"] else market_value
            salary_type = "contract (est.)"
        elif status == "pre-arb":
            if i < pre_arb_left:
                salary = MLB_MINIMUM
                salary_type = "pre-arb"
            else:
                arb_yr = i - pre_arb_left + 1
                salary = arb_salary(market_value, min(arb_yr, 3))
                salary_type = f"arb{min(arb_yr, 3)} (est.)"
        elif status == "arb":
            arb_yr = arb_year_now + i
            if arb_yr <= 3:
                salary = arb_salary(market_value, arb_yr)
                salary_type = f"arb{arb_yr} (est.)"
            else:
                salary = market_value
                salary_type = "FA (est.)"
        else:
            salary = MLB_MINIMUM
            salary_type = "est."

        surplus = market_value - salary
        rows.append({
            "year": yr,
            "age": age,
            "war": round(war, 1),
            "market_value": round(market_value, 1),
            "salary": round(salary, 3),
            "salary_type": salary_type,
            "surplus": round(surplus, 1),
            "discounted_surplus": round(surplus * discount_factor, 1),
        })

    return rows


def contract_n_years(contract):
    """How many years to project based on remaining contract control."""
    if contract.get("_years_override") is not None:
        return contract["_years_override"]
    status = contract.get("status", "")
    if status == "pre-arb":
        service_time = contract.get("service_time") or 0.0
        pre_arb_left = max(1, int(3.0 - service_time))
        return min(pre_arb_left + 3, MAX_PROJECTION_YEARS)
    if contract.get("yearly"):
        return min(len(contract["yearly"]), MAX_PROJECTION_YEARS)
    return 3


def trade_value_for_war(war_y1, current_age, contract):
    n = contract_n_years(contract)
    wars = project_wars(max(0.0, war_y1), current_age, n=n)
    rows = build_control_years(contract, current_age, wars)
    return sum(r["discounted_surplus"] for r in rows) * CONTROL_DISCOUNT


# ── Two-component trade value tier system ──────────────────────────────────────
# Talent tier: what the player IS worth (WAR × $/WAR, discounted, no salary effect)
# Contract tier: how favorable/unfavorable the deal is (+3 to -3)
# Net trade value tier: talent tier + contract adjustment (can be negative)
#
# Thresholds calibrated from 87 verified historical trades in trades.csv.
# Higher tiers (7+) have cleaner separation; tiers 2-5 overlap due to rental
# compression (1-year deals suppress talent value regardless of player quality).

# Thresholds recalibrated 2026-07-21: ×1.20 vs original to correct systematic
# +1.56-tier overestimation found in 321-trade back-test.
_TALENT_THRESHOLDS = [
    (144, 10), (96, 9), (66, 8), (42, 7),
    (24, 6), (14, 5), (7, 4), (2, 3), (0, 2),
]

# Development discount on talent_value by service class.
# Pre-arb/arb players have meaningful bust risk; the surplus formula is optimistic
# because it projects full career value at wWAR with no probability-of-sticking haircut.
# Applied only to talent_tier and contract_adj ratio — not to the surplus table display.
# Recalibrated 2026-07-27 via grid search on 321-trade back-test.
# Old values (0.80/0.90/0.95) produced +1.5–1.7 tier overestimation for arb classes.
# New values bring arb1/arb2/arb3 mean errors to ≤ ±0.2 (MAE 1.91, ±2: 71%).
_DEVELOPMENT_FACTORS = {
    "pre-arb": 0.70,
    "arb1":    0.35,
    "arb":     0.60,   # FanGraphs generic "arb" — treat as arb2 equivalent
    "arb2":    0.60,
    "arb3":    0.60,
    "rental":  1.00,
    "signed":  1.00,
}

# Hard net-tier ceiling for pre-arb and arb1 players (original caps retained).
_UNPROVEN_TIER_CAPS = [
    (4.0, 10),
    (2.5,  8),
    (1.0,  6),
    (0.0,  5),
]
# arb2 cap — extends the WAR-floor mechanism to arb2 (tighter than arb1 caps;
# calibrated from p90 of actual return_tier for arb2 by wWAR bucket).
_ARB2_TIER_CAPS = [
    (4.0, 10),
    (2.5,  7),
    (1.0,  5),
    (0.0,  4),
]
_UNPROVEN_STATUSES = {"pre-arb", "arb1"}

_TALENT_LABELS = {
    10: "franchise player",  9: "perennial All-Star",
    8:  "star",              7: "above-average starter",
    6:  "solid starter",     5: "average MLB contributor",
    4:  "below-average / platoon", 3: "fringe roster value",
    2:  "marginal MLB",      1: "replacement level",
}

_CONTRACT_LABELS = {
    3: "pre-arb / extreme team-friendly",
    2: "significantly below market",
    1: "below market",
    0: "near market rate",
   -1: "slightly overpaid",
   -2: "significantly overpaid",
   -3: "severely overpaid",
}

_NET_TIER_LABELS = {
    10: "generational — franchise-defining return",
    9:  "elite haul — multiple top-25 prospects",
    8:  "franchise haul — multiple top-50 + pieces",
    7:  "strong return — top-50 prospect + quality pieces",
    6:  "solid return — quality top-100 package",
    5:  "average — org top-5 or MLB depth piece",
    4:  "modest — fringe prospects or depth",
    3:  "limited — org filler",
    2:  "salary dump — minimal return",
    1:  "salary absorption — trading team needs sweeteners",
    0:  "break-even — concessions offset value",
   -1:  "negative — trading team must include a throw-in",
   -2:  "deeply negative — salary relief + prospect required",
   -3:  "toxic — acquiring team demands major compensation",
}


def calc_talent_value(war_y1, current_age, n_years):
    """Discounted production value at market rate — no salary deduction."""
    wars = project_wars(max(0.0, war_y1), current_age, n=n_years)
    tv = sum(w * DOLLARS_PER_WAR / ((1 + DISCOUNT_RATE) ** i) for i, w in enumerate(wars))
    return round(tv * CONTROL_DISCOUNT, 1)


def calc_salary_pv(contract, current_age, war_y1):
    """Discounted present value of salaries owed under this contract."""
    n = contract_n_years(contract)
    wars = project_wars(max(0.0, war_y1), current_age, n=n)
    rows = build_control_years(contract, current_age, wars)
    return round(sum(r["salary"] / ((1 + DISCOUNT_RATE) ** i) * CONTROL_DISCOUNT
                     for i, r in enumerate(rows)), 1)


def calc_trade_tiers(war_y1, current_age, contract):
    """
    Returns dict with:
      talent_value   — raw $M production value (WAR × $/WAR, discounted)
      talent_tier    — 1–10 pure player quality
      contract_adj   — -3 to +3 contract favorability
      net_tier       — talent_tier + contract_adj, can be negative
      overpay_m      — $M the trading team is overpaying annually (positive = overpaid)
    """
    n = contract_n_years(contract)
    tv_raw = calc_talent_value(war_y1, current_age, n)
    dev_factor = _DEVELOPMENT_FACTORS.get(contract.get("status", "signed"), 1.0)
    tv = round(tv_raw * dev_factor, 1)
    spv = calc_salary_pv(contract, current_age, war_y1)

    # Talent tier
    tt = 1
    for threshold, tier in _TALENT_THRESHOLDS:
        if tv >= threshold:
            tt = tier
            break

    # Contract adjustment: salary burden vs development-adjusted talent value
    ratio = spv / tv if tv > 0 else 2.0
    if ratio < 0.25:   cadj =  3
    elif ratio < 0.45: cadj =  2
    elif ratio < 0.65: cadj =  1
    elif ratio < 0.85: cadj =  0
    elif ratio < 1.05: cadj =  0
    elif ratio < 1.35: cadj = -1
    elif ratio < 1.75: cadj = -2
    else:              cadj = -3

    # Surplus severity penalty: catastrophically negative contracts reduce net tier
    # further even if talent tier is high. Triggers when total surplus is deeply negative.
    n_yrs = contract_n_years(contract)
    wars = project_wars(max(0.0, war_y1), current_age, n=n_yrs)
    ctrl_rows = build_control_years(contract, current_age, wars)
    total_surplus = sum(r["discounted_surplus"] for r in ctrl_rows) * CONTROL_DISCOUNT
    if total_surplus < -90:   severity_pen = -3
    elif total_surplus < -60: severity_pen = -2
    elif total_surplus < -30: severity_pen = -1
    else:                     severity_pen = 0

    net = max(-3, min(10, tt + cadj + severity_pen))

    # Empirical cap for unproven controlled players: surplus formula overvalues
    # multi-year cheap contracts for players who haven't proven they'll stick.
    # Cap derived from p90 of actual return_tier by wWAR bucket (321-trade back-test).
    status = contract.get("status", "signed")
    tier_cap = None
    if status in _UNPROVEN_STATUSES:
        for wWAR_min, cap in _UNPROVEN_TIER_CAPS:
            if war_y1 >= wWAR_min:
                tier_cap = cap
                break
    elif status in ("arb2", "arb"):
        for wWAR_min, cap in _ARB2_TIER_CAPS:
            if war_y1 >= wWAR_min:
                tier_cap = cap
                break
    if tier_cap is not None:
        net = min(net, tier_cap)

    # Annual overpay: Year 1 actual salary vs current year market value.
    # Use ctrl_rows[0] rather than AAV — FanGraphs AAV can be stale for option-year contracts.
    yr1_salary = ctrl_rows[0]["salary"] if ctrl_rows else (contract.get("aav") or 0.0)
    market_now = war_y1 * DOLLARS_PER_WAR
    overpay = round(yr1_salary - market_now, 1) if yr1_salary else 0.0

    return {
        "talent_value":  tv,
        "talent_tier":   tt,
        "contract_adj":  cadj,
        "severity_pen":  severity_pen,
        "net_tier":      net,
        "overpay_m":     overpay,
        "total_surplus": round(total_surplus, 1),
        "dev_factor":    dev_factor,
        "tier_cap":      tier_cap,
    }


# ── Output helpers ─────────────────────────────────────────────────────────────
def _aging_phase_label(age):
    if age < 27:   return "pre-peak (improving)"
    if age <= 30:  return "peak (flat)"
    if age <= 33:  return "decline phase (-0.5/yr)"
    return "steep decline (-0.75/yr)"


def _contract_context(contract, current_age, war_y1):
    status = contract["status"]
    aav = contract.get("aav") or 0.0
    market = war_y1 * DOLLARS_PER_WAR
    n_yrs = contract_n_years(contract)

    if status == "pre-arb":
        svc = contract.get("service_time") or 0.0
        pre_left = max(1, int(3.0 - svc))
        return (
            f"Pre-arb control: ~{pre_left} pre-arb yr(s) + 3 arb yrs "
            f"({CURRENT_YEAR}–{CURRENT_YEAR + n_yrs - 1}). "
            f"Salary starts at ${MLB_MINIMUM:.3f}M (league minimum), steps up through arb. "
            f"Maximum team-friendly window — no extension signed."
        )
    if status == "arb":
        arb_now = contract.get("arb_year_now") or 1
        return (
            f"Arb {arb_now} of 3 — salary estimated at {int(ARB_RATES[arb_now]*100)}% of ${market:.1f}M market value. "
            f"3 remaining arb years before free agency. "
            f"No extension — trade value declines sharply at each arb step."
        )
    if status == "signed":
        yearly = contract.get("yearly", [])
        end_yr = yearly[-1]["year"] if yearly else CURRENT_YEAR + n_yrs - 1
        # Use Year 1 actual salary for market% — FanGraphs AAV can be stale for option-year contracts.
        wars = project_wars(max(0.0, war_y1), current_age, n=n_yrs)
        ctrl_rows = build_control_years(contract, current_age, wars)
        yr1_salary = ctrl_rows[0]["salary"] if ctrl_rows else aav
        salary_for_pct = yr1_salary if yr1_salary else aav
        if salary_for_pct and market > 0:
            pct = salary_for_pct / market
            if pct < 0.5:
                deal_read = f"VERY team-friendly (Year 1 salary is {pct:.0%} of current market value)"
            elif pct < 0.8:
                deal_read = f"team-friendly (Year 1 salary is {pct:.0%} of current market value)"
            elif pct < 1.1:
                deal_read = f"near-market rate (Year 1 salary is {pct:.0%} of current market value)"
            else:
                deal_read = f"above market (Year 1 salary is {pct:.0%} of current market value — aging risk)"
        else:
            deal_read = "contract terms on file"
        salary_label = f"${yr1_salary:.2f}M/yr" if yr1_salary and abs(yr1_salary - aav) > 0.5 else f"${aav:.2f}M AAV"
        return (
            f"Signed through {end_yr} at {salary_label} — {deal_read}. "
            f"{n_yrs} year(s) of control remaining."
        )
    return ""


def _trade_tier(trade_val):
    if trade_val >= 150: return "franchise-altering — multiple top-25 prospects + ML pieces"
    if trade_val >= 100: return "elite — likely requires a top-10 prospect and significant additional return"
    if trade_val >= 70:  return "very high — top-30 prospect package plus additional pieces"
    if trade_val >= 40:  return "high — top-50 prospect(s) + depth; significant deal"
    if trade_val >= 20:  return "mid-tier — 1–2 quality prospects or one near-ready piece"
    if trade_val >= 5:   return "modest — role player or lower-tier prospect swap"
    if trade_val >= -10: return "effectively a salary dump — minimal return expected"
    return "negative — acquiring team likely demands salary relief or a throw-in"


def _aging_arc_summary(control_rows, current_age):
    peak_rows = [r for r in control_rows if 27 <= r["age"] <= 30]
    decline_rows = [r for r in control_rows if r["age"] > 30]
    lines = []
    if current_age < 27:
        years_to_peak = 27 - current_age
        lines.append(f"Still {years_to_peak} yr(s) from peak — WAR projected to improve before leveling off.")
    if peak_rows:
        peak_wrs = [r["war"] for r in peak_rows]
        lines.append(f"Peak window (ages 27–30): {min(peak_wrs):.1f}–{max(peak_wrs):.1f} WAR projected.")
    if decline_rows:
        end_war = decline_rows[-1]["war"]
        lines.append(f"Decline starts age {decline_rows[0]['age']}: drops to {end_war:.1f} WAR by {decline_rows[-1]['year']}.")
    return "  ".join(lines)


# ── Output ─────────────────────────────────────────────────────────────────────
def print_report(player_name, zips_row, contract, control_rows, current_age, is_pitcher, war_y1, leverage=1.0, raw_war=None, proj_sources=None):
    team = zips_row.get("Team", "?")
    pos = "P" if is_pitcher else zips_row.get("_position", zips_row.get("minpos", "?"))
    sep = "=" * 70

    print(f"\n{sep}")
    print(f"  {player_name.upper()} — {team} | {pos} | Age {current_age} | {_aging_phase_label(current_age)}")
    print(sep)
    if proj_sources:
        if len(proj_sources) == 1:
            label, val = next(iter(proj_sources.items()))
            print(f"  WAR      : {val:.1f} ({label})")
        else:
            parts = " | ".join(f"{k}: {v:.1f}" for k, v in proj_sources.items())
            print(f"  WAR      : {sum(proj_sources.values()) / len(proj_sources):.1f} avg  [{parts}]")
    if leverage != 1.0 and raw_war is not None:
        role = next((k for k, v in RELIEF_ROLE_LEVERAGE.items() if v == leverage), "custom")
        print(f"           : × {leverage:.1f} leverage ({role}) → {war_y1:.1f} effective")
    print(f"  Contract : {contract['status'].upper()}")
    if contract["aav"]:
        print(f"  AAV      : ${contract['aav']:.2f}M")
    if contract["options"]:
        print(f"  Options  : {', '.join(contract['options'])}")
    if contract.get("service_time") is not None:
        print(f"  Service  : {contract['service_time']:.3f} years")
    if contract.get("payroll_note"):
        print(f"  Note     : {contract['payroll_note']}")
    if contract.get("no_trade_note"):
        print(f"  NTC      : {contract['no_trade_note']}")
    ctx = _contract_context(contract, current_age, war_y1)
    if ctx:
        print(f"\n  Context  : {ctx}")
    print()

    arc = _aging_arc_summary(control_rows, current_age)
    war_label = "fWAR Projections (aging curve, leverage-adjusted)" if leverage != 1.0 else "fWAR Projections (aging curve)"
    print(f"  {war_label}")
    if arc:
        print(f"  {arc}")
    print(f"  {'Year':<6} {'Age':<5} {'WAR':<6} {'Phase':<22} {'Aging'}")
    print(f"  {'─'*55}")
    for r in control_rows:
        phase = _aging_phase_label(r["age"])
        if r["year"] == CURRENT_YEAR:
            delta_str = "ZiPS base"
        else:
            d = aging_delta(r["age"])
            delta_str = f"{'+' if d >= 0 else ''}{d:.2f} from prior yr"
        print(f"  {r['year']:<6} {r['age']:<5} {r['war']:<6.1f} {phase:<22} {delta_str}")
    print()

    print("  Surplus Value Breakdown")
    print(f"  ($/WAR = ${DOLLARS_PER_WAR}M · discount = {int(DISCOUNT_RATE*100)}%/yr · controllability = {CONTROL_DISCOUNT}×)")
    hdr = f"  {'Year':<6} {'Age':<5} {'WAR':<5} {'Market':>9} {'Salary':>10} {'Type':<11} {'Surplus':>8} {'Disc.':>8}"
    print(hdr)
    print(f"  {'─'*70}")
    for r in control_rows:
        flag = " ◄" if r["surplus"] < 0 else ""
        print(
            f"  {r['year']:<6} {r['age']:<5} {r['war']:<5.1f}"
            f" ${r['market_value']:>7.1f}M ${r['salary']:>7.3f}M"
            f" {r['salary_type']:<11} ${r['surplus']:>6.1f}M ${r['discounted_surplus']:>6.1f}M{flag}"
        )
    print(f"  {'─'*70}")

    total_surplus = sum(r["discounted_surplus"] for r in control_rows)
    trade_val = total_surplus * CONTROL_DISCOUNT
    tv_low  = trade_value_for_war(war_y1 - 1.0, current_age, contract)
    tv_high = trade_value_for_war(war_y1 + 1.0, current_age, contract)
    n_negative = sum(1 for r in control_rows if r["surplus"] < 0)
    tiers = calc_trade_tiers(war_y1, current_age, contract)

    print()
    print(f"  Total Discounted Surplus  : ${total_surplus:.1f}M")
    print(f"  Trade Value (×0.875)      : ${trade_val:.1f}M")
    print(f"  Confidence Range          : ${tv_low:.1f}M – ${tv_high:.1f}M  (±1 WAR on Yr 1)")
    if n_negative:
        print(f"  Underwater years          : {n_negative} of {len(control_rows)} (marked ◄)")
    print()

    tt   = tiers["talent_tier"]
    cadj = tiers["contract_adj"]
    spen = tiers["severity_pen"]
    net  = tiers["net_tier"]
    cadj_str = f"+{cadj}" if cadj > 0 else str(cadj)
    spen_str = f"{spen}" if spen == 0 else f"{spen} (catastrophic surplus)"
    dev_factor = tiers.get("dev_factor", 1.0)
    tier_cap   = tiers.get("tier_cap")
    dev_note = f" (×{dev_factor:.2f} dev discount)" if dev_factor < 1.0 else ""
    print(f"  ┌─ Trade Value Assessment ────────────────────────────────────┐")
    print(f"  │  Talent Tier    : {tt:>2}/10  {_TALENT_LABELS.get(tt, ''): <38}│")
    if dev_note:
        print(f"  │  Dev Discount   :        {dev_note.strip(): <42}│")
    if tier_cap is not None and net == tier_cap:
        cap_note = f"capped at {tier_cap} — unproven at {war_y1:.1f} wWAR (data-derived)"
        print(f"  │  WAR Floor Cap  :        {cap_note: <42}│")
    print(f"  │  Contract       : {cadj_str:>3}    {_CONTRACT_LABELS.get(cadj, ''): <38}│")
    if spen < 0:
        print(f"  │  Surplus Pen.   : {spen:>3}    catastrophic total surplus (-${abs(tiers['total_surplus']):.0f}M) {'':15}│")
    if net <= 3:
        print(f"  │  Net Trade Tier :   —    {'salary relief — not a standard trade asset': <38}│")
    else:
        print(f"  │  Net Trade Tier : {net:>2}     {_NET_TIER_LABELS.get(net, ''): <38}│")
    if tiers["overpay_m"] > 1.0:
        overpay_note = f"${tiers['overpay_m']:.1f}M/yr above market — limits return"
        print(f"  │  Overpay        :        {overpay_note: <42}│")
    elif tiers["overpay_m"] < -3.0:
        savings_note = f"${abs(tiers['overpay_m']):.1f}M/yr below market — boosts return"
        print(f"  │  Savings        :        {savings_note: <42}│")
    print(f"  └─────────────────────────────────────────────────────────────┘")
    print(f"  [!] Model assumes healthy player. Injury history, physical concerns,")
    print(f"      or no-trade clauses can significantly lower actual return.")
    print()

    flags = ["fWAR throughout (bWAR not used) — source: local OneDrive xlsx"]
    if is_pitcher and leverage == 1.0:
        flags.append("RP/SP: leverage not applied — add --relief-role {closer|setup|middle} for relievers")
    if is_pitcher:
        flags.append("Pitcher: note if fWAR vs bWAR gap > 1.0 WAR")
    if leverage != 1.0:
        flags.append(f"Leverage factor {leverage:.1f}× applied (gmLI proxy) — raw fWAR: {raw_war:.1f}")
    if contract["status"] == "unknown":
        flags.append("Contract status undetermined — salary estimates are rough")
    if contract["options"] and not contract.get("_years_override"):
        flags.append(
            f"VERIFY YEARS OF CONTROL — {', '.join(contract['options'])} detected. "
            f"FG contract API frequently reports wrong year counts with option years. "
            f"Confirm actual control length on Spotrac, then pass --years N to lock it in."
        )
    elif contract["options"]:
        flags.append(f"Option year(s) present ({', '.join(contract['options'])}) — years locked via --years override")
    if contract["status"] == "pre-arb":
        flags.append("Pre-arb: value understated if player signs extension before FA — model only sees current window")
    if contract.get("payroll_note") and "defer" in contract["payroll_note"].lower():
        flags.append(
            f"DEFERRED MONEY: surplus calc uses BaseSalary (face value). "
            f"Real cash cost to team is lower — trade value may be understated. "
            f"({contract['payroll_note']})"
        )
    if contract.get("no_trade_note"):
        flags.append(f"Tradeability limited: {contract['no_trade_note']}")
    for f in flags:
        print(f"  [!] {f}")
    print(f"\n{sep}\n")


# ── Shared compute ─────────────────────────────────────────────────────────────
def evaluate_player(player_name, is_pitcher=False, age_override=None, war_override=None,
                    use_spotrac=False, fg_csv_path=None, relief_role=None,
                    leverage_override=None, trade_type=None, run_comps=False,
                    min_comps=3, quiet=False,
                    gs=None, g=None, years_override=None, aav_override=None):
    """
    Compute trade value for a player. Returns a result dict.
    On error the dict has 'error' set; all other keys may be absent.
    Prints progress unless quiet=True.
    """
    def log(*a):
        if not quiet:
            print(*a)

    log(f"\nLooking up {player_name!r}...")
    fg_id, mlbam_id = lookup_player_ids(player_name)
    log(f"  FanGraphs ID: {fg_id}" if fg_id else "  FanGraphs ID not found")

    war_y1 = war_override
    proj_sources = {}
    _proj_rows = {}

    if war_y1 is None:
        for proj_type, label in (("thebatx", "THE BAT X"), ("zips", "ZiPS")):
            log(f"  Fetching {label} projection...")
            row = fetch_fg_projection(player_name, is_pitcher, proj_type, fg_id)
            if row:
                w = float(row.get("WAR") or 0)
                if w:
                    proj_sources[label] = round(w, 1)
                    _proj_rows[label] = row
                    log(f"    {label}: {w:.1f} WAR")

        if proj_sources:
            war_y1 = round(sum(proj_sources.values()) / len(proj_sources), 2)
            log(f"  Projection avg: {war_y1:.2f}")
        else:
            log(f"  No projections — falling back to {CURRENT_YEAR - 1} fWAR from local xlsx...")
            war_y1, _ = fetch_war_from_xlsx(player_name, is_pitcher, CURRENT_YEAR - 1)
            if war_y1 is None:
                log(f"  Not found in {CURRENT_YEAR - 1}, trying {CURRENT_YEAR - 2}...")
                war_y1, _ = fetch_war_from_xlsx(player_name, is_pitcher, CURRENT_YEAR - 2)
            if war_y1 is None:
                return {"player_name": player_name, "error": "WAR not found. Use --war to supply it."}
            proj_sources["historical fWAR"] = round(war_y1, 1)
            log(f"  Historical fWAR: {war_y1}")

    _meta_row = _proj_rows.get("THE BAT X") or _proj_rows.get("ZiPS") or {}
    zips_row = {"PlayerName": player_name, "Team": _meta_row.get("Team", "?"), "xMLBAMID": mlbam_id}

    # Auto-fetch GS/G from MLB Stats API when --war given without --gs/--g
    _gs, _g = gs, g
    if war_override is not None and gs is None and g is None and mlbam_id:
        try:
            fetched = fetch_current_season_gs(int(mlbam_id), is_pitcher)
            if fetched and int(fetched) > 0:
                if is_pitcher:
                    _gs = int(fetched)
                    log(f"  Auto-fetched GS: {_gs} (MLB Stats API {CURRENT_YEAR})")
                else:
                    _g = int(fetched)
                    log(f"  Auto-fetched G: {_g} (MLB Stats API {CURRENT_YEAR})")
        except Exception as e:
            log(f"  GS auto-fetch warning: {e}")

    # Pace annualization: --war <partial> with GS/G (manual or auto-fetched)
    if (_gs is not None or _g is not None) and war_override is not None:
        if _gs is not None:
            full = 30
            annualized = round(war_y1 / _gs * full, 2)
            log(f"  Pace: {war_y1:.1f} WAR / {_gs} GS × {full} full-season GS = {annualized:.1f} WAR")
            war_y1 = annualized
        else:
            full = 65 if is_pitcher else 155
            unit = "G (RP)" if is_pitcher else "G"
            annualized = round(war_y1 / _g * full, 2)
            log(f"  Pace: {war_y1:.1f} WAR / {_g} {unit} × {full} = {annualized:.1f} WAR")
            war_y1 = annualized

    current_age = age_override
    mlb_info = {}
    if mlbam_id:
        try:
            mlb_info = fetch_mlb_player_info(int(mlbam_id))
        except Exception as e:
            log(f"  MLB Stats API warning: {e}")

    if current_age is None:
        current_age = mlb_info.get("age")
    if current_age is None:
        return {"player_name": player_name, "error": "Age not determined. Use age_override."}
    current_age = int(current_age)
    zips_row.setdefault("_position", mlb_info.get("position", "?"))
    log(f"  {zips_row.get('PlayerName', player_name)} | Age {current_age} | Yr1 WAR: {war_y1}")

    # Contract
    contract = None
    if fg_csv_path:
        log(f"Loading contract data from FanGraphs CSV: {fg_csv_path}")
        contract = load_fg_csv(fg_csv_path, player_name, fg_id=fg_id)
        if contract is None:
            return {"player_name": player_name, "error": f"Not found in {fg_csv_path}"}
        log(f"  Status: {contract['status']} | AAV: ${contract['aav']:.2f}M  [source: FanGraphs CSV]")
    else:
        # Spotrac first — more accurate year counts for extensions/rentals.
        # Fall back to FanGraphs when Spotrac returns None or "unknown" (recently signed deals).
        log("Fetching contract data from Spotrac...")
        contract = fetch_spotrac(player_name)
        if contract and contract.get("status") != "unknown":
            log(f"  Status: {contract['status']} | AAV: ${contract['aav']:.2f}M  [source: Spotrac]")
        else:
            if contract:
                log("  Spotrac returned unknown status — falling back to FanGraphs...")
            if fg_id:
                log("Fetching contract data from FanGraphs Roster Resource...")
                fg_contract = fetch_fg_contract(fg_id)
                if fg_contract:
                    contract = fg_contract
                    log(f"  Status: {contract['status']} | AAV: ${contract['aav']:.2f}M  [source: FanGraphs]")
            if contract is None or contract.get("status") == "unknown":
                contract = {"status": "pre-arb", "salaries": [], "yearly": [], "options": [], "service_time": 0.0, "aav": 0.0}
                log("  No contract data found — using pre-arb defaults")

    # Contract overrides (correct stale API data)
    if years_override is not None:
        contract["_years_override"] = years_override
        log(f"  Years override: {years_override} yr(s) of control")
    if aav_override is not None:
        contract["aav"] = aav_override
        log(f"  AAV override: ${aav_override:.1f}M/yr")

    raw_war = war_y1
    if relief_role:
        leverage = RELIEF_ROLE_LEVERAGE[relief_role]
    elif leverage_override is not None:
        leverage = leverage_override
    else:
        leverage = 1.0
    effective_war = round(raw_war * leverage, 2)

    n_years      = contract_n_years(contract)
    war_projs    = project_wars(effective_war, current_age, n=n_years)
    control_rows = build_control_years(contract, current_age, war_projs)
    tiers        = calc_trade_tiers(effective_war, current_age, contract)

    # Position + status for comps query
    raw_pos   = mlb_info.get("position") or zips_row.get("_position") or ""
    pos_group = "OF" if raw_pos.upper() in ("LF", "CF", "RF") else raw_pos.upper() or None
    if pos_group not in ("SP", "RP", "C", "1B", "2B", "3B", "SS", "OF", "DH", "IF"):
        pos_group = ("RP" if relief_role else "SP") if is_pitcher else None

    cs      = contract["status"]
    arb_yr  = contract.get("arb_year_now")
    if cs == "arb" and arb_yr:
        comp_status = f"arb{arb_yr}"
    elif cs == "ufa":
        comp_status = "rental"
    else:
        comp_status = cs

    salary = contract.get("aav") or None

    comps, comp_expanded_mult = [], 1.0
    if run_comps:
        comps, comp_expanded_mult = find_comps(
            effective_war, current_age, n_years, comp_status, pos_group,
            salary=salary, trade_type=trade_type, exclude_player=player_name,
            min_comps=min_comps,
        )

    return {
        "player_name":        player_name,
        "zips_row":           zips_row,
        "contract":           contract,
        "control_rows":       control_rows,
        "current_age":        current_age,
        "is_pitcher":         is_pitcher,
        "war_y1":             effective_war,
        "raw_war":            raw_war,
        "proj_sources":       proj_sources,
        "leverage":           leverage,
        "tiers":              tiers,
        "comps":              comps,
        "comp_expanded_mult": comp_expanded_mult,
        "comp_query": {
            "war":      effective_war,
            "age":      current_age,
            "years":    n_years,
            "status":   comp_status,
            "position": pos_group,
            "salary":   salary,
        },
        "error": None,
    }


def write_result_csv(results, path):
    """Append player summary rows to a CSV. Creates file with header if it doesn't exist."""
    fieldnames = [
        "player", "age", "war", "years_control", "status", "salary_m",
        "net_tier", "talent_tier", "contract_adj", "trade_value_m",
        "avg_comp_tier", "comp_count", "comp_expanded",
    ]
    write_header = not Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        written = 0
        for r in results:
            if r.get("error"):
                continue
            tiers = r["tiers"]
            comps = r.get("comps", [])
            avg_comp = round(sum(c[1]["return_tier"] for c in comps) / len(comps), 1) if comps else ""
            w.writerow({
                "player":        r["player_name"],
                "age":           r["current_age"],
                "war":           r["war_y1"],
                "years_control": len(r["control_rows"]),
                "status":        r["contract"]["status"],
                "salary_m":      round(r["contract"].get("aav") or 0, 2),
                "net_tier":      tiers["net_tier"],
                "talent_tier":   tiers["talent_tier"],
                "contract_adj":  tiers["contract_adj"],
                "trade_value_m": tiers["total_surplus"],
                "avg_comp_tier": avg_comp,
                "comp_count":    len(comps),
                "comp_expanded": r.get("comp_expanded_mult", 1.0) > 1.0,
            })
            written += 1
    print(f"  Written {written} row(s) to {path}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="MLB trade value calculator")
    ap.add_argument("player", nargs="+", help="Player full name")
    ap.add_argument("--pitcher", action="store_true", help="Treat as pitcher")
    ap.add_argument("--age", type=int, help="Override player age")
    ap.add_argument("--war", type=float, help="Override Year 1 WAR projection")
    ap.add_argument(
        "--spotrac", action="store_true",
        help="Force Spotrac scraping for contract data (default: FanGraphs API)",
    )
    ap.add_argument(
        "--fg-csv", metavar="PATH",
        help="Path to a CSV manually exported from fangraphs.com/roster-resource/payroll",
    )
    ap.add_argument(
        "--comps", action="store_true",
        help="Append historical trade comparables from trades.csv",
    )
    ap.add_argument(
        "--trade-type", choices=["deadline", "offseason"],
        help="Filter comps to deadline or offseason trades only",
    )
    ap.add_argument(
        "--relief-role", choices=["closer", "setup", "middle"],
        help="Reliever leverage preset: closer=1.8×, setup=1.4×, middle=1.1× (applies gmLI multiplier to ZiPS WAR)",
    )
    ap.add_argument(
        "--leverage", type=float,
        help="Direct gmLI leverage multiplier (e.g. 1.8). Overridden by --relief-role.",
    )
    ap.add_argument(
        "--csv", metavar="PATH",
        help="Append player summary to CSV (creates file with header if it doesn't exist)",
    )
    ap.add_argument(
        "--gs", type=int, metavar="N",
        help="Games started so far (SP). Annualizes --war to a 30-GS full season.",
    )
    ap.add_argument(
        "--g", type=int, metavar="N",
        help="Games played so far (RP or hitter). Annualizes --war to 65 G (RP) or 155 G (hitter).",
    )
    ap.add_argument(
        "--years", type=int, metavar="N",
        help="Override years of control remaining (corrects stale FanGraphs/Spotrac data).",
    )
    ap.add_argument(
        "--aav", type=float, metavar="M",
        help="Override annual average value in $M (corrects stale contract data).",
    )
    args = ap.parse_args()

    player_name = " ".join(args.player)

    result = evaluate_player(
        player_name,
        is_pitcher=args.pitcher,
        age_override=args.age,
        war_override=args.war,
        use_spotrac=args.spotrac,
        fg_csv_path=args.fg_csv,
        relief_role=args.relief_role,
        leverage_override=args.leverage,
        trade_type=args.trade_type,
        run_comps=args.comps,
        min_comps=3,
        gs=args.gs,
        g=args.g,
        years_override=args.years,
        aav_override=args.aav,
    )

    if result.get("error"):
        msg = result["error"]
        print(f"\nERROR: {msg}")
        if "ZiPS" in msg:
            print("  - Check spelling")
            print("  - Add --pitcher if this is a pitcher")
            print("  - Use --war to supply the projection manually")
        elif "WAR not found" in msg:
            print(f"  Use --war to provide the projection manually.")
        elif "Age not" in msg:
            print("  Use --age to provide it.")
        sys.exit(1)

    print_report(result["player_name"], result["zips_row"], result["contract"],
                 result["control_rows"], result["current_age"], result["is_pitcher"],
                 result["war_y1"], leverage=result["leverage"], raw_war=result["raw_war"],
                 proj_sources=result.get("proj_sources"))

    if args.comps:
        q = result["comp_query"]
        print_comps(result["comps"], q["war"], q["age"], q["years"],
                    q["status"], q["position"], q["salary"],
                    expanded_mult=result["comp_expanded_mult"])

    if args.csv:
        write_result_csv([result], args.csv)


if __name__ == "__main__":
    main()
