"""
Rebuilds trades.csv with a clean canonical schema.

New column order:
  trade_id, player_name, trade_date, season, trade_type, from_team, to_team,
  position_group, is_pitcher, age_at_trade,
  contract_status, years_control_remaining, salary_m, aav_m,
  war_yr0, g_yr0, war_yr1, g_yr1, war_yr2, g_yr2, war_yr3, g_yr3, war_peak, covid_yr,
  wWAR, war_trend, trend_label, yr0_rate, avail_pct, avail_grade, peak_gap,
  key_pieces, return_summary, return_tier, return_prospect_grades,
  notes

Dropped: war_prior_season, war_2prior_season, war3_prior_season, Team 1 War, Team 2 War
Reference CSV is authoritative for all WAR/games values.
"""

import csv
from pathlib import Path

TRADES_PATH = Path.home() / "projects/trade-value-engine/trades.csv"
REF_PATH    = Path.home() / "OneDrive - Driveline Baseball/war reference.csv"

CANONICAL_FIELDS = [
    "trade_id", "player_name", "trade_date", "season", "trade_type",
    "from_team", "to_team",
    "position_group", "is_pitcher", "age_at_trade",
    "contract_status", "years_control_remaining", "salary_m", "aav_m",
    "war_yr0", "g_yr0",
    "war_yr1", "g_yr1",
    "war_yr2", "g_yr2",
    "war_yr3", "g_yr3",
    "war_peak", "covid_yr",
    "wWAR", "war_trend", "trend_label", "yr0_rate",
    "avail_pct", "avail_grade", "peak_gap",
    "key_pieces", "return_summary", "return_tier", "return_prospect_grades",
    "notes",
]

SP_TARGET  = 30
RP_TARGET  = 65
HIT_TARGET = 162
MARCEL     = {1: 5, 2: 4, 3: 3}
MIN_SAMPLE = {"sp": 10, "rp": 15, "hitter": 30}
AVAIL_GRADES_HIT = [(0.957,"A+"),(0.895,"A"),(0.802,"B"),(0.679,"C"),(0.556,"D")]
AVAIL_GRADES_SP  = [(31,"A+"),(28,"A"),(25,"B"),(20,"C"),(15,"D")]
AVAIL_GRADES_RP  = [(65,"A+"),(58,"A"),(50,"B"),(40,"C"),(30,"D")]


def _flt(v):
    if v is None: return None
    s = str(v).strip().replace(",", ".")
    if s in ("", "-", "n/a", "na", "none"): return None
    try: return float(s)
    except: return None

def _int(v):
    f = _flt(v)
    return int(f) if f is not None else None

def role_cat(role_str):
    r = (role_str or "").upper()
    if "SP" in r: return "sp"
    if any(x in r for x in ("RP", "CP", "CL")): return "rp"
    return "hitter"

def season_target(cat):
    return {"sp": SP_TARGET, "rp": RP_TARGET, "hitter": HIT_TARGET}[cat]

def calc_wwar(yr1, yr2, yr3, covid_slot):
    slots = {1: yr1, 2: yr2, 3: yr3}
    pairs = [(v, MARCEL[s]) for s, v in slots.items()
             if v is not None and s != covid_slot]
    if not pairs: return None
    total_w = sum(w for _, w in pairs)
    return round(sum(v * w for v, w in pairs) / total_w, 2)

def calc_avail(g1, g2, g3, covid_slot, cat):
    slot_games = {1: g1, 2: g2, 3: g3}
    valid = [g for s, g in slot_games.items() if g is not None and s != covid_slot]
    if not valid: return None, ""
    avg = sum(valid) / len(valid)
    tgt = season_target(cat)
    pct = round(avg / tgt, 3)
    if cat == "sp":
        for thr, gr in AVAIL_GRADES_SP:
            if avg >= thr: return pct, gr
    elif cat == "rp":
        for thr, gr in AVAIL_GRADES_RP:
            if avg >= thr: return pct, gr
    else:
        for thr, gr in AVAIL_GRADES_HIT:
            if pct >= thr: return pct, gr
    return pct, "F"

def calc_yr0_rate(war0, g0, cat):
    if war0 is None or g0 is None: return ""
    if g0 < MIN_SAMPLE[cat]: return ""
    return round(war0 / g0 * season_target(cat), 2)

def trend_label(val):
    if val is None: return ""
    if val >= 1.0: return "rising"
    if val <= -1.0: return "declining"
    return "stable"


def load_ref():
    with open(REF_PATH, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    lookup = {}
    for r in rows:
        key = (r["player_name"].strip().lower(), r["trade_date"].strip())
        lookup[key] = r
    return lookup


def main():
    ref = load_ref()

    with open(TRADES_PATH, newline="", encoding="utf-8-sig") as f:
        old_rows = list(csv.DictReader(f))

    new_rows = []
    enriched = 0

    for old in old_rows:
        key = (old["player_name"].strip().lower(), old["trade_date"].strip())
        r = ref.get(key)

        new = {f: "" for f in CANONICAL_FIELDS}

        # --- Copy identity + context fields ---
        for f in ("trade_id", "player_name", "trade_date", "season", "trade_type",
                  "from_team", "to_team", "position_group", "is_pitcher", "age_at_trade",
                  "contract_status", "years_control_remaining", "salary_m", "aav_m",
                  "key_pieces", "return_summary", "return_tier", "return_prospect_grades",
                  "notes"):
            new[f] = old.get(f, "")

        if r is not None:
            # --- Reference is authoritative for all WAR/games ---
            yr0  = _flt(r.get("war_yr0"));  g0  = _int(r.get("g_yr0"))
            yr1  = _flt(r.get("war_yr1"));  g1  = _int(r.get("g_yr1"))
            yr2  = _flt(r.get("war_yr2"));  g2  = _int(r.get("g_yr2"))
            yr3  = _flt(r.get("war_yr3"));  g3  = _int(r.get("g_yr3"))
            peak = _flt(r.get("war_peak"))
            covid_raw  = r.get("covid_yr", "").strip()
            covid_slot = int(covid_raw) if covid_raw.isdigit() else None
            cat        = role_cat(r.get("role", "") or old.get("position_group", ""))

            new["war_yr0"] = yr0 if yr0 is not None else ""
            new["g_yr0"]   = g0  if g0  is not None else ""
            new["war_yr1"] = yr1 if yr1 is not None else ""
            new["g_yr1"]   = g1  if g1  is not None else ""
            new["war_yr2"] = yr2 if yr2 is not None else ""
            new["g_yr2"]   = g2  if g2  is not None else ""
            new["war_yr3"] = yr3 if yr3 is not None else ""
            new["g_yr3"]   = g3  if g3  is not None else ""
            new["war_peak"]  = peak        if peak        is not None else ""
            new["covid_yr"]  = covid_slot  if covid_slot  is not None else ""

            wwar = calc_wwar(yr1, yr2, yr3, covid_slot)
            new["wWAR"] = wwar if wwar is not None else ""

            recent = yr1 if covid_slot != 1 else yr2
            if wwar is not None and recent is not None:
                trend = round(recent - wwar, 2)
                new["war_trend"]   = trend
                new["trend_label"] = trend_label(trend)

            new["yr0_rate"]  = calc_yr0_rate(yr0, g0, cat)
            apct, agr        = calc_avail(g1, g2, g3, covid_slot, cat)
            new["avail_pct"]   = apct if apct is not None else ""
            new["avail_grade"] = agr
            new["peak_gap"]    = round(peak - wwar, 2) if (peak is not None and wwar is not None) else ""
            enriched += 1

        else:
            # No reference match — preserve old WAR values in yr1/yr2/yr3 slots
            new["war_yr1"] = old.get("war_prior_season", "")
            new["war_yr2"] = old.get("war_2prior_season", "")
            new["war_yr3"] = old.get("war3_prior_season", "")
            # Compute wWAR from whatever we have
            yr1 = _flt(new["war_yr1"]); yr2 = _flt(new["war_yr2"]); yr3 = _flt(new["war_yr3"])
            wwar = calc_wwar(yr1, yr2, yr3, None)
            new["wWAR"] = wwar if wwar is not None else ""
            if wwar is not None and yr1 is not None:
                trend = round(yr1 - wwar, 2)
                new["war_trend"]   = trend
                new["trend_label"] = trend_label(trend)

        new_rows.append(new)

    with open(TRADES_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_FIELDS)
        writer.writeheader()
        writer.writerows(new_rows)

    print(f"Rebuilt trades.csv — {len(new_rows)} rows, {enriched} enriched from reference.")
    print(f"Columns: {len(CANONICAL_FIELDS)}")

    # Spot-check
    print("\n--- Spot check (war/games paired) ---")
    with open(TRADES_PATH, newline="", encoding="utf-8") as f:
        check_rows = list(csv.DictReader(f))
    hdr = f"{'Player':<28} {'wWAR':>5} {'yr1':>5} {'g1':>4} {'yr2':>5} {'g2':>4} {'yr3':>5} {'g3':>4} {'TLbl':<10}"
    print(hdr)
    print("-" * len(hdr))
    shown = 0
    for r in check_rows:
        if not r.get("war_yr1"): continue
        print(f"{r['player_name'][:28]:<28} "
              f"{r['wWAR']:>5} "
              f"{r['war_yr1']:>5} {r['g_yr1']:>4} "
              f"{r['war_yr2']:>5} {r['g_yr2']:>4} "
              f"{r['war_yr3']:>5} {r['g_yr3']:>4} "
              f"{r['trend_label']:<10}")
        shown += 1
        if shown >= 15: break


if __name__ == "__main__":
    main()
