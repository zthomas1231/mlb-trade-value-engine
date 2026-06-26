"""
WAR grading utilities — weighted average, peak, availability rating.
See WAR_REFERENCE.md for methodology and grade scale definitions.
"""

# Grade thresholds: (min_war, grade). Checked top-down, first match wins.
_WAR_GRADES = [
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

# Availability thresholds: (min_pct, grade)
_AVAIL_GRADES = [
    (0.957, "A+"),
    (0.895, "A"),
    (0.802, "B"),
    (0.679, "C"),
    (0.556, "D"),
]

_SP_AVAIL = [(31, "A+"), (28, "A"), (25, "B"), (20, "C"), (15, "D")]
_RP_AVAIL = [(65, "A+"), (58, "A"), (50, "B"), (40, "C"), (30, "D")]


def weighted_war(wars: list) -> float:
    """
    3-year Marcel-weighted WAR. wars = [y0, y1, y2] most-recent first.
    None entries are treated as missing and weights redistributed.
    """
    full_weights = [5, 4, 3]
    pairs = [(w, fw) for w, fw in zip(wars[:3], full_weights) if w is not None]
    if not pairs:
        return 0.0
    total_weight = sum(fw for _, fw in pairs)
    return round(sum(w * fw for w, fw in pairs) / total_weight, 2)


def war_grade(war: float) -> str:
    for threshold, grade in _WAR_GRADES:
        if war >= threshold:
            return grade
    return "F"


def availability_rating(games: int, role: str) -> tuple:
    """
    Returns (availability_pct_or_ratio, grade).
    role: "hitter", "sp", or "rp"
    """
    role = role.lower()
    if role == "sp":
        pct = round(games / 30, 3)
        for threshold, grade in _SP_AVAIL:
            if games >= threshold:
                return pct, grade
        return pct, "F"
    if role == "rp":
        pct = round(games / 65, 3)
        for threshold, grade in _RP_AVAIL:
            if games >= threshold:
                return pct, grade
        return pct, "F"
    # hitter
    pct = round(games / 162, 3)
    for threshold, grade in _AVAIL_GRADES:
        if pct >= threshold:
            return pct, grade
    return pct, "F"


def war_profile(wars: list, games: int, role: str) -> dict:
    """
    wars: [y0, y1, y2] most-recent first (None for missing years)
    games: GP (hitter), GS (SP), or G (RP)
    role: "hitter", "sp", or "rp"
    """
    wwar = weighted_war(wars)
    valid = [w for w in wars if w is not None]
    peak = max(valid) if valid else 0.0
    avail_pct, avail_grade = availability_rating(games, role)
    return {
        "wWAR":        wwar,
        "wWAR_grade":  war_grade(wwar),
        "peak":        round(peak, 2),
        "peak_grade":  war_grade(peak),
        "avail_pct":   avail_pct,
        "avail_grade": avail_grade,
        "role":        role,
    }


if __name__ == "__main__":
    import sys
    # Quick CLI: python war_grades.py <y0> <y1> <y2> <games> <role>
    # Example:   python war_grades.py 4.8 3.9 4.2 148 hitter
    if len(sys.argv) < 5:
        print("usage: war_grades.py <war_y0> <war_y1> <war_y2> <games> <role>")
        print("       role: hitter | sp | rp")
        sys.exit(1)
    wars_in = [float(x) if x.lower() != "none" else None for x in sys.argv[1:4]]
    games_in = int(sys.argv[4])
    role_in = sys.argv[5] if len(sys.argv) > 5 else "hitter"
    p = war_profile(wars_in, games_in, role_in)
    print(f"Weighted WAR : {p['wWAR']:5.2f}  ({p['wWAR_grade']})")
    print(f"Peak WAR     : {p['peak']:5.2f}  ({p['peak_grade']})")
    print(f"Availability : {p['avail_pct']:.1%}  ({p['avail_grade']})  [{role_in}]")
