# MLB Trade Value Engine

A tool for estimating fair trade return for any MLB player based on surplus production, contract context, and over 400 verified historical trades (2016–2026).

---

## Motivation

Juan Soto got traded twice in thirteen months for two completely different returns. Same player, same production, same team control window shrinking by exactly one year. If you can explain why those two packages looked so different, you understand what this project is trying to measure.

A few things distort how a trade gets judged in the moment. Rentals get overvalued in the reaction and undervalued in hindsight: the player already in the majors gets the headlines, while the prospect coming back can end up outproducing him over a full control window, the way Soto's two trades priced so differently once his control window shrank. Sell-high moves run the same distortion in reverse, called premature right up until the player declines and the return looks fair all along. Retrospectives also cherry-pick their examples, since the prospects who become the story are the ones who actually developed, while most traded prospects never do. And not every team is optimizing for pure value in the first place, a contender in a tight window will take a lighter return because the player fills a need right now, not because the deal was mispriced.

## How It Works

Trade value comes down to one question: how much production is a team getting above what they're paying for it, and for how long? That gap — surplus value — is what drives prospect returns. The model makes it explicit.

**Surplus = (Projected WAR × $/WAR) − Salary**, summed across all remaining control years with a 5% annual discount and a 0.875× controllability factor.

```
$/WAR             = $7.0M  (calibrated from 2022–2025 free agent contracts)
Discount rate     = 5%/yr
Control discount  = 0.875×
Aging curve       = +0.25/yr pre-27, flat 27–30, −0.50/yr 31–33, −0.75/yr 34+
```

Surplus comes from different places, and the source matters. A pre-arb player earning $750K on a 4-WAR season generates surplus almost entirely from being cheap. A signed player at $12M producing 5 WAR generates surplus from performance outpacing the contract. Both can arrive at the same dollar figure — but how they got there shapes how a trade market prices them.

$/WAR is calibrated from signed free agent contracts matched to 3-year trailing WAR averages. The controllability discount (0.875×) reflects a real market dynamic: a player under team control can't opt out, so the acquiring team accepts a slight discount relative to a freely available FA. The aging curve (+0.25 pre-27, flat 27–30, −0.50 through 33, −0.75 after) is applied uniformly — it doesn't account for individual profiles, which is a known limitation. Projections use Baseball Reference bWAR (annualized from YTD pace) as the primary source, falling back to local FanGraphs projection files (THE BAT X / ZiPS) when live data is unavailable.

**A note on WAR sources.** Current-year player WAR comes from Baseball Reference bWAR; the historical comps database (trades.csv) uses FanGraphs fWAR throughout. These are not the same number. bWAR uses RA9 (actual runs allowed, adjusted for team defense) for pitchers; fWAR uses FIP (strikeouts, walks, home runs only), which strips out contact quality and defense. Soft-contact, ground ball pitchers (Valdez, Webb) show higher bWAR than fWAR — actual run prevention exceeds what FIP predicts. High-strikeout pitchers (Cole, Skenes) often show higher fWAR. The gap is typically 0.5–1.5 WAR for pitchers, smaller (±0.3–0.5) for position players where the main difference is defensive metric (UZR vs. DRS). In practice, the tier output (1–10) is broad enough that this gap rarely shifts a result by more than one tier — but for borderline pitcher evaluations, treat the model tier as a range, not a point estimate. The output flags when bWAR is in use and warns when the pitcher gap may be material.

**wWAR** — the primary matching field for historical comps — is a recency-weighted average of the three seasons before the trade (Marcel-style 5/4/3 weights, most recent season heaviest). This mirrors how front offices actually evaluate trade targets: they care about who a player is now, not a career average. The 2020 COVID season slot is skipped entirely and weights are redistributed.

The model converts surplus into a **Net Trade Tier** (1–10) by combining three components:

| Component | Range | Description |
|---|---|---|
| Talent Tier | 1–10 | Discounted WAR production at market rate |
| Contract Adj | −3 to +3 | How team-friendly or burdensome the salary is |
| Severity Penalty | 0 to −3 | Fires on deeply negative surplus (albatross contracts) |

Tier thresholds are calibrated against the surplus values of the 368 verified trades in the database. Tier 9 represents the highest return grade in the historical data. The Soto trade (Abrams, Gore, Hassell, Wood, Susana) produced the highest return and would represent a Tier 10 in isolation, but drops to Tier 9 to account for Bell's inclusion in the package sent back the other way.

Historical comps search the database by wWAR, age, years of control, and contract status. When salary is provided, comp scoring also factors in WAR-salary ratio and end-age. The closest matches anchor the expected return range and give real-world grounding to what the surplus model projects. The database is concentrated in Tiers 1–6 — depth and mid-range moves make up the majority of real trades. For elite players (Tier 7+), the surplus calculation carries more weight than the comp matches.

---

## Sample Output

Three contrasting examples — arb pitcher, arb position player, and an overpaid veteran — showing the model across different player types and contract situations.

**1. Arb SP, one year of control (Logan Gilbert, SEA)**

```
$ python player_value.py "Logan Gilbert" --pitcher

======================================================================
  LOGAN GILBERT — ? | P | Age 29 | peak (flat)
======================================================================
  WAR      : 4.2 (2026 bWAR (pace))
  Contract : ARB
  AAV      : $10.93M
  Service  : 4.144 years

  Context  : Arb 3 of 3 — salary at 80% of $29.2M market value.
             No extension signed — trade value declines at each arb step.

  Surplus Value Breakdown
  ($/WAR = $7.0M · discount = 5%/yr · controllability = 0.875×)
  Year   Age   WAR      Market     Salary Type         Surplus    Disc.
  ──────────────────────────────────────────────────────────────────────
  2026   29    4.2   $   29.2M $ 10.927M ARB 3       $  18.3M $  18.3M
  ─────────────────────  free agent after 2026  ─────────────────────
  2027   30    4.2   $   29.2M $ 29.190M FA (est.)   $   0.0M $   0.0M
  2028   31    3.7   $   25.7M $ 25.690M UFA         $   0.0M $   0.0M
  ──────────────────────────────────────────────────────────────────────
  Total Discounted Surplus  : $18.3M
  Trade Value (×0.875)      : $16.0M
  Confidence Range          : $9.9M – $22.1M  (±1 WAR on Yr 1)

  ┌─ Trade Value Assessment ────────────────────────────────────┐
  │  Talent Tier    :  7/10  above-average starter                 │
  │  Dev Discount   :        (×0.60 dev discount applied)          │
  │  Contract       :  -1    slightly overpaid                     │
  │  Net Trade Tier :  6     solid return — quality top-100 package│
  └─────────────────────────────────────────────────────────────┘
```

**2. Arb 3B, two years of control (Isaac Paredes, CHC)**

```
$ python player_value.py "Isaac Paredes"

======================================================================
  ISAAC PAREDES — ? | 3B | Age 27 | peak (flat)
======================================================================
  WAR      : 3.9 (2026 bWAR (pace))
  Contract : ARB
  AAV      : $9.35M
  Service  : 4.160 years
  Options  : club option

  Context  : Arb 3 of 3 — salary at 80% of $27.2M market value.
             No extension signed — trade value declines at each arb step.

  Surplus Value Breakdown
  ($/WAR = $7.0M · discount = 5%/yr · controllability = 0.875×)
  Year   Age   WAR      Market     Salary Type         Surplus    Disc.
  ──────────────────────────────────────────────────────────────────────
  2026   27    3.9   $   27.2M $  9.350M ARB 3       $  17.8M $  17.8M
  2027   28    3.9   $   27.2M $ 13.350M ARB 4       $  13.8M $  13.2M
  ─────────────────────  free agent after 2027  ─────────────────────
  2028   29    3.9   $   27.2M $ 27.160M UFA         $   0.0M $   0.0M
  ──────────────────────────────────────────────────────────────────────
  Total Discounted Surplus  : $31.0M
  Trade Value (×0.875)      : $27.1M
  Confidence Range          : $15.1M – $39.0M  (±1 WAR on Yr 1)

  ┌─ Trade Value Assessment ────────────────────────────────────┐
  │  Talent Tier    :  6/10  solid starter                         │
  │  Dev Discount   :        (×0.60 dev discount applied)          │
  │  Contract       :   0    near market rate                      │
  │  Net Trade Tier :  6     solid return — quality top-100 package│
  └─────────────────────────────────────────────────────────────┘
```

**3. Signed veteran, aging contract (Xander Bogaerts, SD)**

```
$ python player_value.py "Xander Bogaerts"

======================================================================
  XANDER BOGAERTS — ? | SS | Age 33 | decline phase (-0.5/yr)
======================================================================
  WAR      : 2.0 (2026 bWAR (pace))
  Contract : SIGNED
  AAV      : $25.45M
  Service  : 9.042 years
  NTC      : Full no-trade clause

  Context  : Signed through 2034 at $25.45M AAV — above market
             (179% of current market value — aging risk). 8 yr(s) of control.

  Surplus Value Breakdown
  ($/WAR = $7.0M · discount = 5%/yr · controllability = 0.875×)
  Year   Age   WAR      Market     Salary Type         Surplus    Disc.
  ──────────────────────────────────────────────────────────────────────
  2026   33    2.0   $   13.9M $ 25.000M fangraphs   $ -11.1M $ -11.1M ◄
  2027   34    1.2   $    8.7M $ 25.000M fangraphs   $ -16.3M $ -15.5M ◄
  2028   35    0.5   $    3.4M $ 25.000M fangraphs   $ -21.6M $ -19.6M ◄
  2029   36    0.0   $    0.0M $ 25.000M fangraphs   $ -25.0M $ -21.6M ◄
  2030   37    0.0   $    0.0M $ 25.000M fangraphs   $ -25.0M $ -20.6M ◄
  2031   38    0.0   $    0.0M $ 25.000M fangraphs   $ -25.0M $ -19.6M ◄
  2032   39    0.0   $    0.0M $ 25.000M fangraphs   $ -25.0M $ -18.7M ◄
  2033   40    0.0   $    0.0M $ 25.000M fangraphs   $ -25.0M $ -17.8M ◄
  ──────────────────────────────────────────────────────────────────────
  Total Discounted Surplus  : $-144.5M
  Trade Value (×0.875)      : $-126.4M
  Underwater years          : 8 of 8 (marked ◄)

  ┌─ Trade Value Assessment ────────────────────────────────────┐
  │  Talent Tier    :  5/10  average MLB contributor               │
  │  Contract       :  -3    severely overpaid                     │
  │  Surplus Pen.   :  -3    catastrophic total surplus (-$126M)   │
  │  Underwater Pen.:  -3    8 yr(s) negative surplus              │
  │  Net Trade Tier :   —    salary relief — not a standard asset  │
  └─────────────────────────────────────────────────────────────┘
  [!] Tradeability limited: Full no-trade clause
```

---

## The Trade Database

Over 400 verified trades from 2015–2026, concentrated in 2021–2025 (the core of the dataset). Each entry includes:

- **WAR history** — three prior seasons (Marcel-weighted into wWAR), availability grades, trend direction
- **Contract context** — status, salary, AAV, years of control remaining
- **Return package** — key pieces with prospect grades at time of trade, return tier 1–10

Return tiers are graded on prospect national rankings **at the time of trade** — not hindsight. A tier 8 in 2022 means those were nationally ranked prospects then, not what they became.

| Tier | Return package |
|------|----------------|
| 1 | Cash, PTBNL, or pure salary dump |
| 2 | Single 40-FV depth piece |
| 3 | Two 40-FV pieces/one org top-20 |
| 4 | Org top-15 (45 FV) / two pieces with one cracking org top-20 |
| 5 | Org top-10 (45–50 FV) / two prospects, one org top-10 |
| 6 | Org top-5 / national top-100 fringe (50 FV) / projectable MLB-ready piece |
| 7 | Solid top-100 national (50–55 FV) / top-5 org + depth |
| 8 | 1–2 top-100 nationals + supporting pieces |
| 9 | Top-25 national as centerpiece / 2-3+ top-100 nationals / MLB-ready talent + org depth |
| 10 | Generational haul — rich prospect pool (top-10 national + second top-25 national + additional top-100 depth) AND young high-upside controllable MLB talent already proven in the majors |

**Tier distribution:** Most real trades are depth moves. The database reflects this: ~67% are Tier 1–3, ~20% Tier 4–6, ~10% Tier 7–9. This means comp matching is most reliable for mid-range and depth trades. For elite players (projected Tier 8+), treat the surplus calculation as the primary signal and comps as directional context.

---

## Jupyter Notebook

`trade_value_engine.ipynb` walks through the model's methodology with three case studies:

- **Juan Soto (2022 and 2023)** — same player, two trades, one year apart: the rental discount in action
- **Mason Miller (2025)** — raw wWAR 1.7, but leverage-adjusted value explains why Oakland got the #3 overall prospect
- **Christian Yelich (2018)** — 4 WAR at $7M/yr: why the contract subsidy forced Milwaukee to pay with their best prospect

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
python player_value.py "Logan Gilbert" --pitcher
python player_value.py "Isaac Paredes"
python player_value.py "Xander Bogaerts"
python player_value.py "Mason Miller" --pitcher --relief-role closer
python player_value.py "Logan Gilbert" --pitcher --comps    # run comps after report
python player_value.py "Logan Gilbert" --pitcher --comps --trade-type deadline
```

### Find trade comparables directly

```bash
python comps.py --war 4.5 --age 27 --years 3 --status signed
python comps.py --war 1.5 --age 25 --years 5 --status pre-arb --position RP
python comps.py --war 4.9 --age 26 --years 4 --status signed --position OF --salary 7
```

## Project Structure

```
player_value.py          Main entry point: WAR + contract + surplus + tiers
comps.py                 Historical trade comp search and scoring
populate_trades.py       CLI to add new trades to trades.csv
rebuild_trades_schema.py Canonical schema rebuild (run after WAR reference updates)
calc_dollar_per_war_auto.py  Automated $/WAR calibration from FA market

trades.csv               400+ trade database (the core dataset)
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

### How the discount factors interact

Two things reduce a player's trade value below raw surplus: when the production happens, and whether the acquiring team can walk away if it doesn't.

**Time discount (5%/yr).** A surplus dollar in year 3 is worth less than one today. The player might get hurt, decline, or simply not be that good anymore. The math: divide each year's surplus by (1.05)^n, where n is years from now. That formula is just working backwards — if you had that dollar today and invested it at 5%, it would grow to exactly that future amount by year n. Year 3 is worth 90.7 cents. Year 5 is worth 82.3 cents. The 5% rate is on the low end of what baseball research suggests — FanGraphs uses 7–10% — meaning this model gives more credit to future production than most industry frameworks.

**Controllability discount (0.875×).** The $/WAR market rate is set by free agents, who have options. Teams bid against each other, competition drives the price to full value. A team-controlled player has no leverage — he can't opt out if he outperforms, and the acquiring team can't exit if he declines. The trade market prices that asymmetry at 12.5% below equivalent free agent value. This applies once, as a flat multiplier on total trade value, not compounded per year.

Together: year-3 surplus is worth 0.907 × 0.875 = **79 cents on the dollar**.

The one case where this gets messy is arb players. Arb salaries reset toward market each year through the hearing process — if a player declines, his next salary adjusts down. So you're locked in to the player, but not the price. The model doesn't carve out a separate rate for this. Instead, arb players hit a lower tier ceiling than comparable pre-arb or signed players — the discount is built into the caps, not the formula.

---

## Limitations

- **Sparse high-tier comps.** Only 25 trades in the database land at Tier 7–9, and none reach Tier 10. For elite players, that means the surplus value calculation is doing most of the work. The comps are useful for directional context, but they're not precise enough to anchor a number on their own. Any young controllable star will give tier 10 (Bobby Witt Jr., Corbin Carroll, Kevin McGonigle, etc).
- **Injury history is not modeled.** The surplus table assumes a healthy player. Significant injury history lowers actual trade value and should be discounted manually.
- **ERA/WAR divergence for recent role changes.** If a starter converts to reliever, their starter history still lives in wWAR. The leverage adjustment corrects for some of this, but one season of relief data isn't enough to fully reprice them.
- **Aging curve is uniform.** The per-year WAR deltas don't distinguish player types, even though a contact hitter, a power hitter, and a pitcher all age differently in reality.
- **Mixed WAR sources** The historical comps database (trades.csv) uses Fangraphs fWAR throughout. Live player evaluation uses Baseball Reference bWAR (annualized from YTD pace), falling back to local fWAR projection when live data is unavailable.
---

## Further Reading

**WAR methodology**
- [What is WAR? — FanGraphs Library](https://library.fangraphs.com/misc/war/) — The foundation: what WAR measures, how replacement level is set, and why the stat exists.
- [fWAR vs. bWAR: What's the Difference? — FanGraphs Library](https://library.fangraphs.com/war/differences-fwar-rwar/) — The specific splits between FIP-based and RA9-based pitching WAR, and UZR vs. DRS for defense.
- [Baseball-Reference WAR Explained](https://www.baseball-reference.com/about/war_explained.shtml) — Baseball Reference's own documentation of bWAR, including the RA9 pitcher component this model uses as its primary WAR source.

**$/WAR and surplus value**
- [What Are Teams Paying Per WAR in Free Agency? — FanGraphs (2026)](https://blogs.fangraphs.com/what-are-teams-paying-for-a-win-in-free-agency-2026-edition/) — Annual calibration of the free agent market cost per win. The source for $/WAR benchmarks.
- [Methodology and Calculations of Dollars per WAR — Hardball Times](https://tht.fangraphs.com/methodology-and-calculations-of-dollars-per-war/) — Matt Swartz's foundational work on deriving $/WAR from FA contracts and why a linear relationship holds.
- [How Do Baseball Teams Discount the Future? — FanGraphs](https://blogs.fangraphs.com/how-do-baseball-teams-discount-the-future/) — Empirical analysis of the discount rate teams apply to future WAR. Finds ~10% gross, ~7% net of cost-of-win inflation — the basis for the comparison to this model's 5% rate.

**Prospect and trade valuation**
- [Introducing an Updated Method for Prospect Valuation — FanGraphs](https://blogs.fangraphs.com/introducing-an-updated-method-for-prospect-valuation/) — FanGraphs' surplus value framework for prospects: how team control years are discounted and what Future Value grades translate to in dollar terms.
- [The Details of Our New Prospect Valuation Methodology — FanGraphs](https://blogs.fangraphs.com/the-details-of-our-new-prospect-valuation-methodology/) — The updated version, with refinements to the 7% discount rate and grade-to-value mappings.
- [FanGraphs Trade Value Series (2026)](https://blogs.fangraphs.com/2026-trade-value-nos-1-10/) — FanGraphs' annual trade value rankings. Good benchmark for where this model's tiers land vs. industry consensus.

