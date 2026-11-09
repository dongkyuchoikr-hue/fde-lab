"""일별 시세(작업 폴더, 비공개) → docs/data/weekly.json (종목별 이번 주 통계 · 공개 범위 필터 적용).

사용:
  python3 -I scripts/build_weekly.py --eod "$WORK/eod" [--private-out "$WORK/weekly_full.json"]
        [--universe docs/data/universe.json] [--rules docs/data/rules.json]
        [--policy docs/data/source_policy.json] [--holidays docs/data/nyse_holidays.json]
        [--out docs/data/weekly.json] [--as-of YYYY-MM-DD] [--now YYYY-MM-DDTHH:MM:SSZ]
        [--publish-mode ranks_only|full] [--extra-tickers AAA,BBB]

- 기준일(as_of) = 가장 최근에 끝난 거래 주의 마지막 거래일(휴장일 파일 기준). --as-of 로 바꿀 수 있습니다(시험·fixture 용).
- 모든 수익률은 수정 종가(adj_close), 거래량은 원 거래량. 비율은 %(소수 둘째 자리).
- 공개 파일(--out)은 source_policy.json 의 publish_mode 를 따릅니다: ranks_only 면 가격·수익률 숫자를 빼고
  순위·조건 결과·방향·참/거짓만 남깁니다. 전체 숫자는 --private-out(작업 폴더, 커밋 안 함)에만 씁니다.
- 통계만 계산합니다. 점수·순위·선정은 build_picks.py, 지난주 결과는 build_results.py 가 이 파일을 읽어 만듭니다.
- 표준 출력에는 가격 숫자를 찍지 않습니다(공개 저장소의 Actions 로그는 공개).
종료 코드: 0 성공 · 1 인자·파일 오류 · 3 벤치마크 자료가 아직 기준일까지 없음(게시 전)
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fdelib import Calendar, avg_ranks, dump_json, is_num, load_json, parse_now, pct_from_rank, run_info, strip_private  # noqa: E402

MIN_DAYS_DEFAULT = 200


def pct(a, b):
    if not (is_num(a) and is_num(b)) or a == 0:
        return None
    return round((b / a - 1) * 100, 2)


def stdev_pct(returns: list[float]):
    if len(returns) < 5:
        return None
    m = sum(returns) / len(returns)
    return round(math.sqrt(sum((x - m) ** 2 for x in returns) / (len(returns) - 1)) * 100, 2)


def load_rows(eod_dir: str, ticker: str):
    p = os.path.join(eod_dir, f"{ticker}.json")
    if not os.path.exists(p):
        return None
    d = load_json(p)
    rows = sorted((r for r in d.get("rows", []) if is_num(r.get("adj_close")) and r.get("date")), key=lambda r: r["date"])
    return rows


def stats_for(rows: list[dict], as_of: str, week_start: str, bench_daily: dict | None, rules: dict) -> dict:
    """한 종목의 통계. rows 는 날짜 오름차순. 기준일 이후 행은 무시."""
    rows = [r for r in rows if r["date"] <= as_of]
    if not rows:
        return {"fresh": False, "last_date": None, "days_of_data": 0}
    last = rows[-1]
    fresh = last["date"] == as_of
    closes = [r["adj_close"] for r in rows]
    n = len(closes)
    out: dict = {"fresh": fresh, "last_date": last["date"], "days_of_data": n}
    if not fresh:
        return out

    week = [r for r in rows if week_start <= r["date"] <= as_of]
    before = [r for r in rows if r["date"] < week_start]
    daily = [(rows[i]["date"], closes[i] / closes[i - 1] - 1) for i in range(1, n)]

    out["last_close"] = round(last["close"], 2)
    if before:
        out["prev_close"] = round(before[-1]["close"], 2)
        out["ret_1w"] = pct(before[-1]["adj_close"], last["adj_close"])
    else:
        out["prev_close"] = None
        out["ret_1w"] = None
    out["ret_4w"] = pct(closes[-21], closes[-1]) if n > 20 else None

    f = rules["factors"]
    skip, length = f["trend"]["skip_days"], f["trend"]["length_days"]
    out["mom_6_1"] = pct(closes[-1 - length], closes[-1 - skip]) if n > length else None

    w60 = f["calm"]["window_days"]
    out["volatility_60d"] = stdev_pct([x for _, x in daily[-w60:]]) if len(daily) >= w60 else None
    out["volatility_20d"] = stdev_pct([x for _, x in daily[-20:]]) if len(daily) >= 20 else None
    out["vol_ratio_20_60"] = (round(out["volatility_20d"] / out["volatility_60d"], 2)
                              if is_num(out["volatility_20d"]) and is_num(out["volatility_60d"]) and out["volatility_60d"] > 0 else None)

    wdd = f["dd"]["window_days"]
    seg = closes[-wdd:]
    peak, mdd = seg[0], 0.0
    for c in seg:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    out["max_drawdown_12w"] = round(mdd * 100, 2) if len(seg) >= 20 else None

    window = closes[-250:]
    out["high_52w"] = round(max(window), 2)
    out["low_52w"] = round(min(window), 2)
    out["pct_from_52w_high"] = pct(max(window), closes[-1])
    out["high_window_days"] = len(window)

    ma200 = sum(closes[-200:]) / 200 if n >= 200 else None
    out["above_ma200"] = (closes[-1] > ma200) if ma200 else None

    prior_vols = [r["volume"] for r in before[-20:] if is_num(r.get("volume"))]
    week_vols = [r["volume"] for r in week if is_num(r.get("volume"))]
    out["vol_ratio"] = (round((sum(week_vols) / len(week_vols)) / (sum(prior_vols) / len(prior_vols)), 2)
                        if prior_vols and week_vols and sum(prior_vols) > 0 else None)

    # 최근 20거래일 중 최악의 하루
    recent = daily[-20:]
    worst = None
    if recent:
        d0, r0 = min(recent, key=lambda x: x[1])
        idx = next(i for i, r in enumerate(rows) if r["date"] == d0)
        prev_vols = [r["volume"] for r in rows[max(0, idx - 20):idx] if is_num(r.get("volume"))]
        vday = rows[idx].get("volume")
        vratio = round(vday / (sum(prev_vols) / len(prev_vols)), 2) if prev_vols and is_num(vday) and sum(prev_vols) > 0 else None
        bret = bench_daily.get(d0) if bench_daily else None
        worst = {"date": d0, "ret": round(r0 * 100, 2),
                 "bench_ret": round(bret * 100, 2) if is_num(bret) else None,
                 "market_down_too": (bret < -0.01) if is_num(bret) else None,
                 "vol_ratio_day": vratio,
                 "volume_spike": (vratio >= 2.0) if is_num(vratio) else None}
    out["worst_day_4w"] = worst

    big = max((abs(x) for _, x in daily[-61:]), default=0.0)
    out["max_abs_daily_ret_61d"] = round(big * 100, 2)
    out["suspect"] = ({"code": "BIG_JUMP", "why_ko": "최근 61거래일 중 하루 등락률 절댓값이 40%를 넘어 분할 미반영 또는 자료 오류로 의심됩니다"}
                      if big > 0.40 else None)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eod", required=True, help="fetch_eod.py 가 쓴 작업 폴더 (커밋하지 않음)")
    ap.add_argument("--universe", default="docs/data/universe.json")
    ap.add_argument("--rules", default="docs/data/rules.json")
    ap.add_argument("--policy", default="docs/data/source_policy.json")
    ap.add_argument("--holidays", default="docs/data/nyse_holidays.json")
    ap.add_argument("--out", default="docs/data/weekly.json")
    ap.add_argument("--private-out", default=None, help="전체 숫자 파일(작업 폴더). 기본: --eod 옆 weekly_full.json")
    ap.add_argument("--as-of", default=None, help="기준일 YYYY-MM-DD (기본: 달력으로 계산)")
    ap.add_argument("--now", default=None, help="시험용 현재 시각 UTC, 예 2026-11-09T06:00:00Z")
    ap.add_argument("--publish-mode", choices=["ranks_only", "full"], default=None, help="기본: source_policy.json")
    ap.add_argument("--extra-tickers", default="", help="유니버스 밖이지만 함께 계산할 종목(지난주 선정 등), 쉼표 구분")
    args = ap.parse_args(argv)

    universe = load_json(args.universe)
    rules = load_json(args.rules)
    policy = load_json(args.policy)
    cal = Calendar(load_json(args.holidays))
    mode = args.publish_mode or policy.get("publish_mode", "ranks_only")
    now = parse_now(args.now)

    as_of_d = dt.date.fromisoformat(args.as_of) if args.as_of else cal.expected_as_of(now)
    as_of = as_of_d.isoformat()
    week = cal.week_bounds(as_of_d)
    stamps = cal.stamps(as_of_d)

    bench_t = universe["benchmark"]["ticker"]
    uni = {x["ticker"]: x for x in universe["tickers"]}
    extra = [t.strip().upper() for t in args.extra_tickers.split(",") if t.strip()]
    tickers = list(uni) + [t for t in extra if t not in uni and t != bench_t]

    manifest = load_json(os.path.join(args.eod, "_manifest.json"), default={})

    bench_rows = load_rows(args.eod, bench_t)
    if not bench_rows:
        print(f"벤치마크 {bench_t} 자료가 없습니다 ({args.eod})", file=sys.stderr)
        return 1
    bench_rows = [r for r in bench_rows if r["date"] <= as_of]
    if not bench_rows or bench_rows[-1]["date"] != as_of:
        print(f"아직 게시 안 됨: {bench_t} 마지막 행 {bench_rows[-1]['date'] if bench_rows else None} ≠ 기준일 {as_of}", file=sys.stderr)
        return 3
    bench_daily = {bench_rows[i]["date"]: bench_rows[i]["adj_close"] / bench_rows[i - 1]["adj_close"] - 1 for i in range(1, len(bench_rows))}
    bench = stats_for(bench_rows, as_of, week["start"], None, rules)
    bench_rec = {"ticker": bench_t, "name_ko": universe["benchmark"].get("name_ko", bench_t), **bench}

    min_days = rules["gates"]["C1"].get("min_days", MIN_DAYS_DEFAULT)
    up, down = rules["dir_1w"]["up_pct"], rules["dir_1w"]["down_pct"]

    def direction(r):
        if not is_num(r):
            return None
        return "상승" if r > up else "하락" if r < down else "보합"

    bench_rec["dir_1w"] = direction(bench.get("ret_1w"))

    stocks, skipped = [], []
    for t in tickers:
        rows = load_rows(args.eod, t)
        meta = uni.get(t, {})
        base = {"ticker": t, "name_ko": meta.get("name_ko", t), "sector_ko": meta.get("sector_ko"), "in_universe": t in uni}
        if rows is None:
            skipped.append({"ticker": t, "why_ko": "시세 파일 없음 — 종목 코드 확인 필요(universe.json) 또는 수집 실패"})
            stocks.append({**base, "fresh": False, "last_date": None, "days_of_data": 0, "flags": ["DATA"],
                           "gates": {"C1": False, "C2": None, "C3": None, "C4": None}})
            continue
        s = stats_for(rows, as_of, week["start"], bench_daily, rules)
        stocks.append({**base, **s})

    # 후보 모집단 M: 기준일 자료 있음 · 거래일 ≥ 200 · 자료 이상 없음 · 유니버스 안
    def c1(s):
        return bool(s.get("fresh")) and s.get("days_of_data", 0) >= min_days and not s.get("suspect")

    ranked = [s for s in stocks if s["in_universe"] and c1(s)]
    m = len(ranked)
    r_trend = avg_ranks({s["ticker"]: s.get("mom_6_1") for s in ranked}, higher_better=True)
    r_calm = avg_ranks({s["ticker"]: s.get("volatility_60d") for s in ranked}, higher_better=False)
    r_dd = avg_ranks({s["ticker"]: s.get("max_drawdown_12w") for s in ranked}, higher_better=True)
    r_1w = avg_ranks({s["ticker"]: s.get("ret_1w") for s in ranked}, higher_better=True)
    fresh_in_uni = [s for s in stocks if s["in_universe"] and s.get("fresh") and not s.get("suspect") and is_num(s.get("ret_1w"))]
    uni_avg = round(sum(s["ret_1w"] for s in fresh_in_uni) / len(fresh_in_uni), 2) if fresh_in_uni else None
    bench_ret = bench.get("ret_1w")
    fl = rules["flags"]
    g = rules["gates"]

    for s in stocks:
        t = s["ticker"]
        fresh = bool(s.get("fresh"))
        s["gates"] = {
            "C1": c1(s) if s["in_universe"] else (fresh and s.get("days_of_data", 0) >= min_days and not s.get("suspect")),
            "C2": s.get("above_ma200") if fresh else None,
            "C3": (s["ret_1w"] <= g["C3"]["surge_1w_max_pct"]) if fresh and is_num(s.get("ret_1w")) else None,
            "C4": (s["worst_day_4w"]["ret"] > g["C4"]["crash_day_min_pct"]) if fresh and s.get("worst_day_4w") else None,
        }
        s["dir_1w"] = direction(s.get("ret_1w"))
        s["rel_1w"] = round(s["ret_1w"] - bench_ret, 2) if fresh and is_num(s.get("ret_1w")) and is_num(bench_ret) else None
        s["beat_bench_1w"] = (s["ret_1w"] > bench_ret) if fresh and is_num(s.get("ret_1w")) and is_num(bench_ret) else None
        s["beat_universe_1w"] = (s["ret_1w"] > uni_avg) if fresh and is_num(s.get("ret_1w")) and is_num(uni_avg) else None
        in_m = t in r_trend
        s["ranks"] = {"trend": r_trend.get(t), "calm": r_calm.get(t), "dd": r_dd.get(t), "ret_1w": r_1w.get(t)} if in_m else None
        s["pct"] = ({"trend": pct_from_rank(r_trend.get(t), m), "calm": pct_from_rank(r_calm.get(t), m), "dd": pct_from_rank(r_dd.get(t), m)}
                    if in_m else None)
        flags = []
        if not fresh or s.get("days_of_data", 0) < min_days:
            flags.append("DATA")
        if s.get("suspect"):
            flags.append("SUSPECT")
        if fresh:
            if s["gates"]["C2"] is False:
                flags.append("BELOW_MA200")
            if s["gates"]["C3"] is False:
                flags.append("SURGE")
            if s["gates"]["C4"] is False:
                flags.append("CRASH_DAY")
            if is_num(s.get("vol_ratio")) and s["vol_ratio"] >= fl["VOL_SPIKE"]["threshold"]:
                flags.append("VOL_SPIKE")
            if s.get("pct") and is_num(s["pct"].get("calm")) and s["pct"]["calm"] <= fl["VOLATILE"]["threshold"]:
                flags.append("VOLATILE")
            if is_num(s.get("vol_ratio_20_60")) and s["vol_ratio_20_60"] >= fl["VOL_UP"]["threshold"]:
                flags.append("VOL_UP")
            if is_num(s.get("max_drawdown_12w")) and s["max_drawdown_12w"] <= fl["BIG_DD"]["threshold"]:
                flags.append("BIG_DD")
            if is_num(s.get("pct_from_52w_high")) and s["pct_from_52w_high"] <= fl["FAR_FROM_HIGH"]["threshold"]:
                flags.append("FAR_FROM_HIGH")
        s["flags"] = flags

    bench_rec["flags"] = ["MARKET_BELOW_MA200"] if bench.get("above_ma200") is False else []
    fresh_count = sum(1 for s in stocks if s["in_universe"] and s.get("fresh"))

    out = {
        "schema": "weekly/2",
        "as_of": stamps,
        "week": week,
        "generated_at_utc": now.strftime("%Y-%m-%d %H:%M"),
        "run": run_info(),
        "provider": policy.get("provider"),
        "provider_name_ko": policy.get("provider_name_ko"),
        "publish_mode": mode,
        "adjusted": manifest.get("adjusted", True),
        "terms_url": policy.get("terms_url"),
        "attribution_ko": policy.get("attribution_ko"),
        "fetched_at_utc": manifest.get("fetched_at_utc"),
        "universe": {"name": universe.get("name"), "name_short_ko": universe.get("name_short_ko"), "version": universe.get("version"),
                     "verified_on": universe.get("verified_on"), "size": len(uni)},
        "benchmark": bench_rec,
        "M": m,
        "fresh_count": fresh_count,
        "universe_count": len(uni),
        "universe_avg_ret_1w": uni_avg,
        "stocks": stocks,
        "skipped": skipped,
    }

    private_out = args.private_out or os.path.join(os.path.dirname(os.path.abspath(args.eod.rstrip("/"))), "weekly_full.json")
    dump_json(private_out, out)
    dump_json(args.out, strip_private(out, mode))
    print(f"OK weekly.json: 유니버스 {len(uni)} · 최신 자료 {fresh_count} · 순위 대상 M={m} · 주 {week['start']}~{week['end']} "
          f"(거래일 {week['trading_days']}) · 기준일 {stamps['et']} ET / {stamps['kst']} KST · 공개 범위 {mode} · 제외 {len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
