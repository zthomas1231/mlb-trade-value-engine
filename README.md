# MLB Trade Value Engine

A tool for estimating fair trade return for any MLB player based on surplus production, contract context, and 282 verified historical trades (2019–2025).

Input a player name. Get a surplus value breakdown, a Net Trade Tier (1–10), and the closest historical comps from the database.

---

## Motivation

MLB trades are priced by two things: how much production a player generates above their salary cost, and how many years a team controls that surplus. The market has a consistent internal logic — but it is rarely made explicit.

This project makes it explicit. The model translates a player's WAR projection, salary, contract length, and age into a surplus value figure, then finds the closest matching trades in a hand-built database to anchor the expected return range.

---

## How It Works

### 1. Surplus Value

**Surplus = (Projected WAR × $/WAR) − Salary**, summed across all control years with a 5% annual discount and a 0.875× controllability factor (team control is more valuable than a free agent because the player cannot opt out).

```
$/WAR  = $7.0M  (calibrated from 2022–2025 free agent contracts)
Discount rate = 5%/yr
Control discount = 0.875×
Aging curve = +0.25/yr pre-27, flat 27–30, −0.50/yr 31–33, −0.75/yr 34+
```

Projections come from FanGraphs ZiPS. Salary and contract structure come from the FanGraphs Roster Resource API.

### 2. Net Trade Tier

Three components combine into a single Net Trade Tier (1–10):

| Component | Range | Description |
|---|---|---|
| Talent Tier | 1–10 | Discounted WAR production at market rate |
| Contract Adj | −3 to +3 | How team-friendly or burdensome the salary is |
| Severity Penalty | 0 to −3 | Fires on deeply negative surplus (albatross contracts) |

### 3. Historical Comps

The comps engine searches 282 verified trades by wWAR, age, years of control, and contract status. When salary is provided, it also scores by WAR-salary ratio and end-age. The closest matches anchor the expected return range.

**wWAR** is a Marcel-weighted average of the three prior seasons (5/4/3 weights, COVID slot skipped), matching how teams actually evaluate trade targets.

---

## Sample Output

```
$ python player_value.py "Corbin Carroll"

======================================================================
  CORBIN CARROLL — ARI | RF | Age 25 | pre-peak (improving)
======================================================================
  Contract : SIGNED
  AAV      : $13.88M
  Context  : Signed through 2031 — VERY team-friendly (41% of market). 6 yr(s) of control.

  Surplus Value Breakdown
  Year   Age   WAR    Market      Salary              Surplus    Disc.
  ──────────────────────────────────────────────────────────────────────
  2026   25    4.8   $ 33.9M  $ 10.0M  fangraphs    $ 23.9M  $ 23.9M
  2027   26    5.1   $ 35.7M  $ 12.0M  fangraphs    $ 23.7M  $ 22.6M
  2028   27    5.1   $ 35.7M  $ 14.0M  fangraphs    $ 21.7M  $ 19.7M
  2029   28    5.1   $ 35.7M  $ 28.0M  fangraphs    $  7.7M  $  6.7M
  2030   29    5.1   $ 35.7M  $ 28.0M  fangraphs    $  7.7M  $  6.3M
  2031   30    5.1   $ 35.7M  $ 28.0M  Club         $  7.7M  $  6.0M
  ──────────────────────────────────────────────────────────────────────
  Total Discounted Surplus  : $85.2M
  Trade Value (×0.875)      : $74.5M

  ┌─ Trade Value Assessment ───────────────────────────────────┐
  │  Talent Tier    : 10/10  franchise player                   │
  │  Contract       :  +1    below market                       │
  │  Net Trade Tier : 10     generational — franchise-defining  │
  └────────────────────────────────────────────────────────────┘
```

```
$ python comps.py --war 4.8 --age 25 --years 6 --status signed --position OF --salary 13.88

  1 comp found  |  avg return tier: 9.0/10 (elite package)

  #1  Christian Yelich  (MIA → MIL, Jan 2018)
       Profile: wWAR 4.1 | Age 26 | 4yr ctrl | SIGNED | $7.0M
       Return tier: 9/10
       Key pieces: Lewis Brinson, Isan Diaz, Monte Harrison, Jordan Yamamoto
       Yelich won NL MVP in year one with Milwaukee.
```

---

## The Trade Database

282 verified trades from 2019–2025. Each entry includes:

- **WAR history** — three prior seasons (Marcel-weighted into wWAR), availability grades, trend direction
- **Contract context** — status, salary, AAV, years of control remaining
- **Return package** — key pieces with prospect grades at time of trade, return tier 1–10

Return tiers are graded on prospect national rankings **at the time of trade** — not hindsight. A tier 8 in 2022 means those were nationally ranked prospects then, not what they became.

Trades span deadline and offseason transactions, all major positions, and the full range of contract situations from pre-arb to rental.

---

## Jupyter Notebook

`trade_value_engine.ipynb` walks through the model's methodology with three case studies:

- **Juan Soto (2022 and 2023)** — same player, two trades, one year apart: the rental discount in action
- **Mason Miller (2025)** — raw wWAR 1.7, but leverage-adjusted value explains why Oakland got the #3 overall prospect
- **Louie Varland (2025)** — 2.02 ERA but negative wWAR: when ERA and WAR tell different stories

Open in VS Code or Jupyter: `jupyter notebook trade_value_engine.ipynb`

---

## Installation

```bash
pip install pybaseball requests beautifulsoup4
```

Python 3.10+ required.

---

## Usage

### Player surplus value report

```bash
python player_value.py "Corbin Carroll"
python player_value.py "Gerrit Cole" --pitcher
python player_value.py "Mason Miller" --pitcher --relief-role closer
python player_value.py "Corbin Carroll" --comps          # run comps after report
python player_value.py "Corbin Carroll" --comps --trade-type deadline
```

### Find trade comparables directly

```bash
python comps.py --war 4.5 --age 27 --years 3 --status signed
python comps.py --war 1.5 --age 25 --years 5 --status pre-arb --position RP
python comps.py --war 4.9 --age 26 --years 4 --status signed --position OF --salary 7
```

### Add a trade to the database

```bash
python populate_trades.py "Manny Machado" 2018-07-18 BAL LAD rental 0 7
python populate_trades.py "Daulton Varsho" 2022-12-08 ARI TOR arb1 4 7 --salary 0.72
```

---

## Project Structure

```
player_value.py          Main entry point: WAR + contract + surplus + tiers
comps.py                 Historical trade comp search and scoring
populate_trades.py       CLI to add new trades to trades.csv
rebuild_trades_schema.py Canonical schema rebuild (run after WAR reference updates)
calc_dollar_per_war_auto.py  Automated $/WAR calibration from FA market

trades.csv               282-trade database (the core dataset)
trade_value_engine.ipynb Narrative analysis notebook with case studies
```

---

## Model Constants

| Constant | Value | Notes |
|---|---|---|
| $/WAR | $7.0M | Calibrated from 2022–2025 FA contracts |
| Discount rate | 5%/yr | |
| Control discount | 0.875× | |
| MLB minimum | $0.740M | |
| Arb rates | 40/60/80% of market | arb1/arb2/arb3 |
| Aging curve | +0.25 pre-27, flat 27–30, −0.50 31–33, −0.75 34+ | Per-year delta |

---

## Limitations

- **Injury history is not modeled.** The surplus table assumes a healthy player. Significant injury history lowers actual trade value and should be discounted manually.
- **ERA/WAR divergence for recent role changes.** A starter who converted to reliever carries their starter history in wWAR. The leverage adjustment helps, but one season of RP data is not enough to fully reprice.
- **Comps database is 2019–2025.** The pre-2019 market has different $/WAR rates and is not included.
- **All fWAR** (not bWAR). Results are not directly comparable to analyses using Baseball Reference WAR.
