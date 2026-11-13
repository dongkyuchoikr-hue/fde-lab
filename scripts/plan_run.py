"""fetch-prices 실행 계획 — 기대 기준일, 받을 종목 목록, 이미 최신인지(no-op)를 계산해 key=value 로 출력합니다.

사용: python3 -I scripts/plan_run.py [--now YYYY-MM-DDTHH:MM:SSZ] [--tickers A,B(시험용 덮어쓰기)] [--as-of YYYY-MM-DD(시험용)]
출력(표준 출력, GITHUB_OUTPUT 에 그대로 붙일 수 있음):
  as_of=2026-11-06  week_end=2026-11-06  age_days=3  provider=eodhd  publish_mode=ranks_only
  tickers=SPY,AAPL,...  prev_pick_as_of=2026-10-30|none  already=true|false  reason=...
- 받을 종목 = 벤치마크 + 유니버스 30 + 직전 동결 선정(유니버스에서 빠진 종목도 결과 계산을 위해).
- already=true 조건: weekly.json·picks.json 이 이미 그 기준일이고 선정 상태가 확정/선정 없음/지각(동결되는 상태)이며,
  직전 선정의 결과 파일이 확정 또는 공백으로 끝나 있음(또는 직전 선정 없음).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fdelib import Calendar, load_json, parse_now  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--now", default=None)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--data", default="docs/data")
    args = ap.parse_args(argv)

    d = args.data
    universe = load_json(os.path.join(d, "universe.json"))
    policy = load_json(os.path.join(d, "source_policy.json"))
    cal = Calendar(load_json(os.path.join(d, "nyse_holidays.json")))
    now = parse_now(args.now)
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else cal.expected_as_of(now)
    week = cal.week_bounds(as_of)

    frozen = []
    for p in sorted(glob.glob(os.path.join(d, "picks", "????-??-??.json"))):
        name = os.path.basename(p)[:-5]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", name) and name < as_of.isoformat():
            frozen.append((name, p))
    prev = load_json(frozen[-1][1]) if frozen else None
    prev_as_of = prev["as_of"]["date"] if prev else None

    if args.tickers.strip():
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = [universe["benchmark"]["ticker"]] + [x["ticker"] for x in universe["tickers"]]
        for p in (prev or {}).get("picks", []):
            if p["ticker"] not in tickers:
                tickers.append(p["ticker"])

    already, reason = False, "새 기준일"
    weekly = load_json(os.path.join(d, "weekly.json"), default=None)
    picks = load_json(os.path.join(d, "picks.json"), default=None)
    if weekly and picks and weekly.get("as_of", {}).get("date") == as_of.isoformat() and picks.get("as_of", {}).get("date") == as_of.isoformat() \
            and picks.get("status") in ("확정", "선정 없음", "지각"):
        res_ok = True
        if prev_as_of == week["prev_end"]:
            res = load_json(os.path.join(d, "results", f"{prev_as_of}.json"), default=None)
            res_ok = bool(res and res.get("status") in ("확정", "공백"))
        if res_ok:
            already, reason = True, "weekly.json·picks.json 이 이미 이 기준일로 확정돼 있음"
        else:
            reason = "선정은 확정됐지만 직전 선정의 결과가 아직 확정되지 않음"
    elif weekly and weekly.get("as_of", {}).get("date") == as_of.isoformat():
        reason = f"선정 상태가 {picks.get('status') if picks else '없음'} — 다시 계산"

    age_days = (now.date() - as_of).days
    print(f"as_of={as_of.isoformat()}")
    print(f"week_end={week['end']}")
    print(f"age_days={age_days}")
    print(f"provider={policy.get('provider')}")
    print(f"publish_mode={policy.get('publish_mode')}")
    print(f"tickers={','.join(tickers)}")
    print(f"prev_pick_as_of={prev_as_of or 'none'}")
    print(f"already={'true' if already else 'false'}")
    print(f"reason={reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
