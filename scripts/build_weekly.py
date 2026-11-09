"""docs/data/eod/*.json(일별 시세) → docs/data/weekly.json(종목별 이번 주 통계).

사용: python3 -I scripts/build_weekly.py [--eod docs/data/eod] [--universe docs/data/universe.json] [--out docs/data/weekly.json]

- "이번 주" = 데이터의 마지막 거래일이 속한 월~금(미국 장). prev_end = 그 주 직전 마지막 거래일.
- 수익률은 수정 종가(adj_close) 기준, 거래량은 원 거래량. 모든 비율은 %(소수 둘째 자리).
- 통계만 계산합니다. 점수·순위·추천은 build_picks.py 가 이 파일을 읽어 따로 만듭니다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import math
import os
import sys

TRADING_DAYS = {"1w": 5, "4w": 20, "12w": 60, "26w": 130, "52w": 250}


def pct(a: float, b: float) -> float | None:
    """a 대비 b 의 변화율(%)."""
    if a is None or b is None or a == 0:
        return None
    return round((b / a - 1) * 100, 2)


def week_bounds(last_date: str) -> tuple[str, str]:
    d = dt.date.fromisoformat(last_date)
    monday = d - dt.timedelta(days=d.weekday())
    friday = monday + dt.timedelta(days=4)
    return monday.isoformat(), friday.isoformat()


def load_universe(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        u = json.load(f)
    return {x["ticker"]: x for x in u.get("tickers", [])}


def stats_for(rows: list[dict], week_start: str, week_end: str) -> dict | None:
    rows = sorted(rows, key=lambda r: r["date"])
    week = [r for r in rows if week_start <= r["date"] <= week_end]
    before = [r for r in rows if r["date"] < week_start]
    if not week or not before:
        return None
    last = week[-1]
    prev = before[-1]
    closes = [r["adj_close"] for r in rows if r["date"] <= last["date"]]
    vols = [r["volume"] for r in rows if r["date"] <= last["date"]]
    n = len(closes)

    def ret_over(days: int) -> float | None:
        return pct(closes[-1 - days], closes[-1]) if n > days else None

    # 거래량: 이번 주 일평균 ÷ 직전 20거래일 일평균
    prior_vols = [r["volume"] for r in before[-20:]]
    vol_ratio = None
    if prior_vols and sum(prior_vols) > 0:
        vol_ratio = round((sum(r["volume"] for r in week) / len(week)) / (sum(prior_vols) / len(prior_vols)), 2)

    # 변동성: 최근 20거래일 일별 수익률 표준편차(%)
    daily = [closes[i] / closes[i - 1] - 1 for i in range(max(1, n - 20), n)]
    volatility_20d = None
    if len(daily) >= 5:
        m = sum(daily) / len(daily)
        volatility_20d = round(math.sqrt(sum((x - m) ** 2 for x in daily) / (len(daily) - 1)) * 100, 2)

    # 52주 고점·저점 대비
    window = closes[-250:]
    high_52w, low_52w = max(window), min(window)

    # 최근 12주 최대 낙폭(%)
    w12 = closes[-61:]
    peak, mdd = w12[0], 0.0
    for c in w12:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    max_drawdown_12w = round(mdd * 100, 2)

    # 최근 4주 중 하루 최대 하락
    recent = [r for r in rows if r["date"] <= last["date"]][-21:]
    worst = None
    for i in range(1, len(recent)):
        r1 = recent[i]["adj_close"] / recent[i - 1]["adj_close"] - 1
        if worst is None or r1 < worst[1]:
            worst = (recent[i]["date"], r1)

    def ma(days: int) -> float | None:
        return sum(closes[-days:]) / days if n >= days else None

    ma50, ma200 = ma(50), ma(200)
    return {
        "last_date": last["date"],
        "last_close": round(last["close"], 2),
        "prev_close": round(prev["close"], 2),
        "ret_1w": pct(prev["adj_close"], last["adj_close"]),
        "ret_4w": ret_over(TRADING_DAYS["4w"]),
        "ret_12w": ret_over(TRADING_DAYS["12w"]),
        "ret_26w": ret_over(TRADING_DAYS["26w"]),
        "ret_52w": ret_over(TRADING_DAYS["52w"]),
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "pct_from_52w_high": pct(high_52w, last["adj_close"]),
        "vol_ratio": vol_ratio,
        "volatility_20d": volatility_20d,
        "max_drawdown_12w": max_drawdown_12w,
        "worst_day_4w": {"date": worst[0], "ret": round(worst[1] * 100, 2)} if worst else None,
        "above_ma50": (last["adj_close"] > ma50) if ma50 else None,
        "above_ma200": (last["adj_close"] > ma200) if ma200 else None,
        "days_of_data": n,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eod", default="docs/data/eod")
    ap.add_argument("--universe", default="docs/data/universe.json")
    ap.add_argument("--out", default="docs/data/weekly.json")
    args = ap.parse_args(argv)

    files = sorted(p for p in glob.glob(os.path.join(args.eod, "*.json")) if not os.path.basename(p).startswith("_"))
    if not files:
        print(f"{args.eod} 에 시세 파일이 없습니다", file=sys.stderr)
        return 1
    manifest_path = os.path.join(args.eod, "_manifest.json")
    manifest = json.load(open(manifest_path, encoding="utf-8")) if os.path.exists(manifest_path) else {}
    universe = load_universe(args.universe)
    with open(args.universe, encoding="utf-8") if os.path.exists(args.universe) else open(os.devnull) as f:
        uni_meta = json.load(f) if os.path.exists(args.universe) else {}

    data = {}
    for p in files:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("rows"):
            data[d["ticker"]] = d

    last_date = max(d["rows"][-1]["date"] for d in data.values())
    week_start, week_end = week_bounds(last_date)
    benchmark = uni_meta.get("benchmark")

    stocks, skipped = [], []
    for t, d in sorted(data.items()):
        s = stats_for(d["rows"], week_start, week_end)
        if s is None or s["last_date"] != last_date:
            skipped.append({"ticker": t, "why": "이번 주 데이터 없음 또는 마지막 날짜 불일치"})
            continue
        meta = universe.get(t, {})
        s = {"ticker": t, "name_ko": meta.get("name_ko", t), "sector_ko": meta.get("sector_ko"),
             "is_benchmark": t == benchmark, "source_url": d.get("source_url"), **s}
        stocks.append(s)

    prev_end = max(r["date"] for d in data.values() for r in d["rows"] if r["date"] < week_start)
    as_of_et = f"{last_date} 16:00"
    as_of_kst = f"{(dt.date.fromisoformat(last_date) + dt.timedelta(days=1)).isoformat()} 06:00"
    out = {
        "schema": "weekly/1",
        "as_of": {"date": last_date, "et": as_of_et, "kst": as_of_kst},
        "week": {"start": week_start, "end": week_end, "prev_end": prev_end},
        "provider": manifest.get("provider"),
        "adjusted": manifest.get("adjusted"),
        "terms": manifest.get("terms"),
        "fetched_at_utc": manifest.get("fetched_at_utc"),
        "universe": {"name": uni_meta.get("name"), "source": uni_meta.get("source"), "as_of": uni_meta.get("as_of")},
        "benchmark": benchmark,
        "stocks": stocks,
        "skipped": skipped,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"OK weekly.json: {len(stocks)}종목 · 주 {week_start}~{week_end} · 기준일 {as_of_et} ET · 제외 {len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
