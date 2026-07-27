#!/usr/bin/env python3
"""
deadline_board.py — batch trade value board for deadline research.

Usage:
    # Player names as args (quote multi-word names; inline flags supported)
    python deadline_board.py "Corbin Carroll" "Gerrit Cole --pitcher" "Mason Miller --pitcher --relief-role closer"

    # From a file (one player per line, same inline-flag syntax)
    python deadline_board.py --file players.txt

    # With comps + export
    python deadline_board.py --file players.txt --comps --csv board.csv

    # Deadline trades only for comps
    python deadline_board.py --file players.txt --comps --trade-type deadline

players.txt format (one per line, # = comment):
    # 2026 deadline targets
    Corbin Carroll
    Gerrit Cole --pitcher
    Mason Miller --pitcher --relief-role closer
    Luis Robert --age 27
    Shane Baz --pitcher --war 2.8
"""

import sys
import argparse
from pathlib import Path

import player_value as pv
from comps import print_comps, RETURN_TIER_LABELS

_NET_TIER_LABELS = pv._NET_TIER_LABELS if hasattr(pv, "_NET_TIER_LABELS") else {}


def _parse_player_line(line):
    """'Name [--flags]' → (name_str, kwargs_dict)"""
    tokens = line.strip().split()
    kwargs = {}
    name_parts = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "--pitcher":
            kwargs["is_pitcher"] = True
        elif t in ("--relief-role", "--relief_role") and i + 1 < len(tokens):
            kwargs["relief_role"] = tokens[i + 1]
            i += 1
        elif t == "--age" and i + 1 < len(tokens):
            kwargs["age_override"] = int(tokens[i + 1])
            i += 1
        elif t == "--war" and i + 1 < len(tokens):
            kwargs["war_override"] = float(tokens[i + 1])
            i += 1
        elif t == "--gs" and i + 1 < len(tokens):
            kwargs["gs"] = int(tokens[i + 1])
            i += 1
        elif t == "--g" and i + 1 < len(tokens):
            kwargs["g"] = int(tokens[i + 1])
            i += 1
        elif t == "--years" and i + 1 < len(tokens):
            kwargs["years_override"] = int(tokens[i + 1])
            i += 1
        elif t == "--aav" and i + 1 < len(tokens):
            kwargs["aav_override"] = float(tokens[i + 1])
            i += 1
        elif t == "--spotrac":
            kwargs["use_spotrac"] = True
        elif not t.startswith("-"):
            name_parts.append(t)
        i += 1
    return " ".join(name_parts), kwargs


def _print_board(results, show_comps=False):
    ok     = [r for r in results if not r.get("error")]
    failed = [r for r in results if r.get("error")]
    ok.sort(key=lambda r: r["tiers"]["net_tier"], reverse=True)

    sep = "=" * 100
    print(f"\n{sep}")
    print(f"  DEADLINE BOARD -- {len(ok)} player(s)  |  July 31, 2026")
    print(f"  {'Player':<28} {'Age':>3}  {'WAR':>4}  {'Ctrl':>4}  {'Status':<10}  {'$/yr':>6}  {'Tier':>4}  Summary")
    print(f"  {'-'*96}")

    for r in ok:
        t      = r["tiers"]
        tier   = t["net_tier"]
        years  = len(r["control_rows"])
        status = r["contract"]["status"].upper()[:9]
        salary = r["contract"].get("aav") or 0
        if tier <= 3:
            tier_display = "    —  "
            label = "salary relief — not a standard trade asset"[:35]
        else:
            tier_display = f"{tier:>4}/10"
            label = _NET_TIER_LABELS.get(tier, "")[:35]
        print(
            f"  {r['player_name']:<28} {r['current_age']:>3}  {r['war_y1']:>4.1f}  {years:>4}  "
            f"{status:<10}  ${salary:>4.1f}M  {tier_display}  {label}"
        )

    if failed:
        print(f"\n  FAILED ({len(failed)}): {', '.join(r['player_name'] + ': ' + r['error'] for r in failed)}")
    print(f"{sep}\n")

    if show_comps:
        for r in ok:
            if not r.get("comp_query"):
                continue
            print(f"\n{'-'*70}")
            print(f"  {r['player_name'].upper()}")
            q = r["comp_query"]
            print_comps(
                r["comps"], q["war"], q["age"], q["years"],
                q["status"], q["position"], q["salary"],
                expanded_mult=r["comp_expanded_mult"],
            )


def main():
    ap = argparse.ArgumentParser(description="Batch trade value board for deadline research")
    ap.add_argument("players", nargs="*",
                    help="Player names (quote each; append --pitcher, --relief-role, etc. inline)")
    ap.add_argument("--file", metavar="PATH",
                    help="Text file with one player per line (same inline-flag syntax)")
    ap.add_argument("--comps", action="store_true",
                    help="Run comparables for each player")
    ap.add_argument("--csv", metavar="PATH",
                    help="Export board to CSV (appends if file exists)")
    ap.add_argument("--trade-type", choices=["deadline", "offseason"],
                    help="Filter comps to this trade type only")
    ap.add_argument("--min-comps", type=int, default=3,
                    help="Auto-expand comp windows until this many found (default: 3)")
    args = ap.parse_args()

    lines = []
    if args.file:
        raw = Path(args.file).read_text(encoding="utf-8").splitlines()
        lines += [l.strip() for l in raw if l.strip() and not l.strip().startswith("#")]
    lines += args.players

    if not lines:
        ap.print_help()
        sys.exit(1)

    results = []
    for line in lines:
        name, kwargs = _parse_player_line(line)
        if not name:
            continue
        print(f"  Evaluating: {name}...", end="", flush=True)
        r = pv.evaluate_player(
            name,
            run_comps=args.comps,
            trade_type=args.trade_type,
            min_comps=args.min_comps,
            quiet=True,
            **kwargs,
        )
        net_tier = r['tiers']['net_tier']
        status_str = f"ERROR: {r['error']}" if r.get("error") else (
            "salary relief" if net_tier <= 3 else f"tier {net_tier}/10"
        )
        print(f" {status_str}")
        results.append(r)

    _print_board(results, show_comps=args.comps)

    if args.csv:
        pv.write_result_csv(results, args.csv)


if __name__ == "__main__":
    main()
