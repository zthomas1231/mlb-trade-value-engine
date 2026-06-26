# MLB Trade Value Engine

A tool for estimating fair trade return for any MLB player based on surplus production, contract context, and 282 verified historical trades (2016–2026).

Input a player name. Get a surplus value breakdown, a Net Trade Tier (1–10), and the closest historical comps from the database.

---

## Motivation

MLB trades are priced by two things: how much production a player generates above their salary cost, and how many years a team controls that surplus. The market has a consistent internal logic — but it is rarely made explicit.

This project makes it explicit. The model translates a player's WAR projection, salary, contract length, and age into a surplus value figure, then finds the closest matching trades in a hand-built database to anchor the expected return range.

---

## How It Works

### 1. Surplus Value

**Surplus = (Projected WAR × $/WAR) − Salary**, summed across all control years with a 5% annual discount and a 0.875× controllability factor.

```
$/WAR             = $7.0M  (calibrated from 2022–2025 free agent contracts)
Discount rate     = 5%/yr
Control discount  = 0.875×
Aging curve       = +0.25/yr pre-27, flat 27–30, −0.50/yr 31–33, −0.75/yr 34+
```

**$/WAR calibration:** Derived from signed free agent contracts matched to 3-year trailing WAR averages (2022–2025 FA classes). See `calc_dollar_per_war_auto.py`.

**Controllability discount (0.875×):** A player under team control cannot opt out into free agency, which reduces the market's effective willingness to pay relative to a freely available FA. The 0.875 factor (a 12.5% haircut) approximates this discount, consistent with several published analyses of team control premium.

**Aging curve:** The per-year WAR deltas (+0.25, 0.00, −0.50, −0.75) are standard approximations used broadly in baseball economics literature. They are applied uniformly and do not account for individual aging profiles — a known limitation for late bloomers and early decliners.

Projections come from FanGraphs ZiPS. Salary and contract structure come from the FanGraphs Roster Resource API.

### 2. wWAR — The Primary Matching Field

Historical trade comps are anchored to **wWAR**: a recency-weighted average of the three seasons before the trade, using Marcel-style weights (5/4/3 — most recent season counts most). The 2020 COVID 60-game season slot is skipped and weights are redistributed.

This matches how front offices actually evaluate trade targets: recent performance weighted heavily, with a full three-year window to smooth single-season variance.

### 3. Net Trade Tier

Three components combine into a single Net Trade Tier (1–10):

| Component | Range | Description |
|---|---|---|
| Talent Tier | 1–10 | Discounted WAR production at market rate |
| Contract Adj | −3 to +3 | How team-friendly or burdensome the salary is |
| Severity Penalty | 0 to −3 | Fires on deeply negative surplus (albatross contracts) |

**Calibration note:** Tier thresholds are calibrated against the surplus values of the verified trades in the database. The highest return grade in the database is Tier 9 (Yelich to Milwaukee, 2018). The model can project Tier 10 for current players based on surplus value — this represents a theoretical ceiling not yet observed in the historical dataset.

### 4. Historical Comps

The comps engine searches 282 verified trades by wWAR, age, years of control, and contract status. When salary is provided, it also scores by WAR-salary ratio and end-age. The closest matches anchor the expected return range.

**Reliability by tier:** The database is concentrated in Tiers 1–6 (depth and mid-range moves, which represent the majority of real trades). High-tier comps (7–9) are sparse — 25 trades total. For elite players, the surplus value calculation carries more weight than the comp matches.

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

282 verified trades from 2016–2026, concentrated in 2021–2025 (the core of the dataset). Each entry includes:

- **WAR history** — three prior seasons (Marcel-weighted into wWAR), availability grades, trend direction
- **Contract context** — status, salary, AAV, years of control remaining
- **Return package** — key pieces with prospect grades at time of trade, return tier 1–10

Return tiers are graded on prospect national rankings **at the time of trade** — not hindsight. A tier 8 in 2022 means those were nationally ranked prospects then, not what they became.

**Tier distribution:** Most real trades are depth moves. The database reflects this: ~67% are Tier 1–3, ~20% Tier 4–6, ~10% Tier 7–9. This means comp matching is most reliable for mid-range and depth trades. For elite players (projected Tier 8+), treat the surplus calculation as the primary signal and comps as directional context.

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
| Discount rate | 5%/yr | Standard in baseball surplus value literature |
| Control discount | 0.875× | 12.5% haircut for non-opt-out team control |
| MLB minimum | $0.740M | 2025 MLB minimum salary |
| Arb rates | 40/60/80% of market | arb1/arb2/arb3 est. |
| Aging curve | +0.25 pre-27, flat 27–30, −0.50 31–33, −0.75 34+ | Per-year WAR delta, approximate |

---

## Limitations

- **Sparse high-tier comps.** The database has 25 trades at Tier 7–9 and none at Tier 10. For elite players, the surplus value calculation is the primary signal — comps provide directional context, not a precise anchor.
- **Injury history is not modeled.** The surplus table assumes a healthy player. Significant injury history lowers actual trade value and should be discounted manually.
- **ERA/WAR divergence for recent role changes.** A starter who converted to reliever carries their starter history in wWAR. The leverage adjustment helps, but one season of RP data is not enough to fully reprice a player.
- **Aging curve is uniform.** The per-year WAR deltas do not vary by player type. A contact hitter, power hitter, and pitcher age differently in practice.
- **Comps database is 2016–2026.** Pre-2016 market conditions differ enough to exclude.
- **All fWAR** (not bWAR). Results are not directly comparable to analyses using Baseball Reference WAR.
