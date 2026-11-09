"""docs/data/weekly.json 검사 — fetch-prices.yml 이 build_weekly.py 다음, 커밋 전에 실행합니다.

사용: python3 -I scripts/check_weekly.py [docs/data/weekly.json] [--rules docs/data/rules.json] [--universe docs/data/universe.json]
종료 코드: 0 통과 · 1 실패(이유를 " - " 줄로 출력) · 2 사용 오류(인자·파일 없음)

검사 항목:
- schema weekly/2 · as_of(date/et/kst) 형식, et 가 date 로 시작 · week: prev_end < start <= end == as_of.date · trading_days 1~5
- publish_mode(ranks_only|full) · provider · terms_url(https) · attribution_ko
- universe.size == universe_count == 유니버스 종목 수 == universe.json 종목 수 · 유니버스 종목이 모두 stocks(또는 skipped)에 있음 · sector_ko 일치
- benchmark: ticker(universe.json 과 같음) · fresh 참/거짓(fresh 면 last_date == as_of.date)
- 종목별: 중복 없음 · fresh 참/거짓 · fresh 면 last_date == as_of.date, days_of_data ≥ 1 · gates C1~C4 참/거짓/null · dir_1w · flags 는 rules.flags 코드
- C1 통과 유니버스 종목: ranks·pct(trend/calm/dd) 숫자, 순위 1~M, 백분위 0~100 · 그 밖은 ranks/pct 없음 · M·fresh_count 재계산 일치
- ranks_only: 문서 어디에도 PRICE_FIELDS 키 없음(재귀) · full: C1 통과 종목의 mom_6_1·volatility_60d·max_drawdown_12w·ret_1w 숫자, |ret_1w| ≤ 60
표준 출력에는 가격 숫자를 찍지 않습니다(개수·날짜만).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fdelib import DATE_RE, PRICE_FIELDS, STAMP_RE, is_num, load_json  # noqa: E402

MODES = {"ranks_only", "full"}
DIRS = {"상승", "하락", "보합", None}
GATES = ("C1", "C2", "C3", "C4")
FULL_NUMS = ("mom_6_1", "volatility_60d", "max_drawdown_12w", "ret_1w")


def load_or_exit(path: str, what: str):
    """파일이 없으면 사용 오류(2), JSON 이 깨졌으면 FAIL(1) 로 끝냅니다."""
    try:
        return load_json(path)
    except FileNotFoundError:
        print(f"사용 오류: {what} 파일이 없습니다 — {path}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(f"FAIL {path}\n - JSON 을 읽을 수 없음: {e.msg} (줄 {e.lineno})")
        sys.exit(1)


def price_leaks(obj, path: str = "") -> list[str]:
    """PRICE_FIELDS 키가 남아 있는 경로 목록(값은 모으지 않음). 목록 안의 종목은 [티커] 로 표시."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            out += ([p] if k in PRICE_FIELDS else []) + price_leaks(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += price_leaks(v, f"{path}[{v.get('ticker', i) if isinstance(v, dict) else i}]")
    return out


def is_int_at_least(v, lo: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= lo


def check_header(d: dict, errors: list[str]) -> None:
    if d.get("schema") != "weekly/2":
        errors.append(f"schema 가 weekly/2 가 아님: {d.get('schema')!r}")
    as_of, week = d.get("as_of") or {}, d.get("week") or {}
    if not DATE_RE.match(str(as_of.get("date", ""))):
        errors.append("as_of.date 형식 오류(YYYY-MM-DD)")
    errors += [f"as_of.{k} 형식 오류(YYYY-MM-DD HH:MM)" for k in ("et", "kst") if not STAMP_RE.match(str(as_of.get(k, "")))]
    if not str(as_of.get("et", "")).startswith(str(as_of.get("date"))):
        errors.append("as_of.et 가 as_of.date 로 시작하지 않음")
    errors += [f"week.{k} 형식 오류(YYYY-MM-DD)" for k in ("start", "end", "prev_end") if not DATE_RE.match(str(week.get(k, "")))]
    dates = (week.get("prev_end"), week.get("start"), week.get("end"), as_of.get("date"))
    if all(DATE_RE.match(str(x)) for x in dates) and not (dates[0] < dates[1] <= dates[2] == dates[3]):
        errors.append("날짜 순서 오류: week.prev_end < week.start <= week.end == as_of.date 여야 함")
    if not (is_int_at_least(week.get("trading_days"), 1) and week["trading_days"] <= 5):
        errors.append(f"week.trading_days 는 1~5 정수여야 함: {week.get('trading_days')!r}")
    if d.get("publish_mode") not in MODES:
        errors.append(f"publish_mode 는 ranks_only 또는 full 이어야 함: {d.get('publish_mode')!r}")
    errors += [f"{k} 가 비어 있음" for k in ("provider", "attribution_ko") if not (isinstance(d.get(k), str) and d[k].strip())]
    if not str(d.get("terms_url", "")).startswith("https://"):
        errors.append("terms_url 은 https:// 로 시작해야 함")


def check_universe(d: dict, universe: dict, stocks: list[dict], in_uni: list[dict], errors: list[str]) -> None:
    uni = {x.get("ticker"): x for x in universe.get("tickers", [])}
    size = (d.get("universe") or {}).get("size")
    if not (size == d.get("universe_count") == len(in_uni) == len(uni)):
        errors.append(f"universe.size({size}) · universe_count({d.get('universe_count')}) · 유니버스 종목 수({len(in_uni)}) · "
                      f"universe.json 종목 수({len(uni)}) 가 서로 다름")
    present = {s.get("ticker") for s in stocks} | {x.get("ticker") for x in d.get("skipped") or [] if isinstance(x, dict)}
    errors += [f"{t}: universe.json 에 있지만 stocks 에도 skipped 에도 없음" for t in uni if t not in present]
    for s in in_uni:
        t = s.get("ticker")
        if t not in uni:
            errors.append(f"{t}: in_universe 인데 universe.json 에 없음")
        elif s.get("sector_ko") != uni[t].get("sector_ko"):
            errors.append(f"{t}: sector_ko 가 universe.json 과 다름")
    bench, bt = d.get("benchmark"), (universe.get("benchmark") or {}).get("ticker")
    if not isinstance(bench, dict) or not bench.get("ticker") or not isinstance(bench.get("fresh"), bool):
        errors.append("benchmark 에 ticker 와 fresh(참/거짓)가 있어야 함")
        return
    if bench["ticker"] != bt:
        errors.append(f"benchmark.ticker({bench['ticker']}) 가 universe.json 의 벤치마크({bt}) 와 다름")
    if bench["fresh"] and bench.get("last_date") != (d.get("as_of") or {}).get("date"):
        errors.append("benchmark 가 fresh 인데 last_date 가 as_of.date 와 다름")


def check_ranks(s: dict, m: int, errors: list[str]) -> None:
    t = s.get("ticker", "?")
    for name, lo, hi in (("ranks", 1, m), ("pct", 0, 100)):
        obj = s.get(name)
        if not isinstance(obj, dict):
            errors.append(f"{t}: C1 통과 종목인데 {name} 가 없음")
            continue
        bad = [k for k in ("trend", "calm", "dd") if not (is_num(obj.get(k)) and lo <= obj[k] <= hi)]
        if bad:
            errors.append(f"{t}: {name}.{'/'.join(bad)} 는 {lo}~{hi} 범위의 숫자여야 함")


def check_stock(s: dict, as_of: str, m: int, flag_codes: set[str], errors: list[str]) -> None:
    t = s.get("ticker", "?")
    fresh, gates, flags = s.get("fresh"), s.get("gates"), s.get("flags")
    if not isinstance(fresh, bool):
        errors.append(f"{t}: fresh 가 참/거짓이 아님")
        return
    if not isinstance(gates, dict) or not set(GATES) <= set(gates) or not all(gates[k] is None or isinstance(gates[k], bool) for k in GATES):
        errors.append(f"{t}: gates 는 C1~C4 각각 참/거짓/null 이어야 함")
        gates = {}
    if s.get("dir_1w") not in DIRS:
        errors.append(f"{t}: dir_1w 는 상승/하락/보합/null 이어야 함: {s.get('dir_1w')!r}")
    if not (isinstance(flags, list) and all(isinstance(c, str) for c in flags) and set(flags) <= flag_codes):
        errors.append(f"{t}: flags 는 rules.flags 에 있는 코드 목록이어야 함: {flags!r}")
    if fresh:
        if s.get("last_date") != as_of:
            errors.append(f"{t}: fresh 인데 last_date({s.get('last_date')}) 가 as_of.date({as_of}) 와 다름")
        if not is_int_at_least(s.get("days_of_data"), 1):
            errors.append(f"{t}: fresh 인데 days_of_data 가 1 이상 정수가 아님")
    else:
        if gates.get("C1") is True:
            errors.append(f"{t}: fresh 가 아닌데 C1 통과로 표시됨")
        ld = s.get("last_date")
        if ld is not None and not (DATE_RE.match(str(ld)) and str(ld) < as_of):
            errors.append(f"{t}: fresh 가 아니면 last_date 는 null 또는 as_of.date 이전 날짜여야 함")
    if s.get("in_universe") is True and gates.get("C1") is True:
        check_ranks(s, m, errors)
    elif s.get("ranks") is not None or s.get("pct") is not None:
        errors.append(f"{t}: 순위 대상(유니버스·C1 통과)이 아닌데 ranks/pct 가 있음")


def check_mode(d: dict, in_uni: list[dict], errors: list[str]) -> None:
    mode = d.get("publish_mode")
    leaks = price_leaks(d) if mode == "ranks_only" else []
    if leaks:
        errors.append(f"ranks_only 인데 가격 칸이 {len(leaks)}곳 남아 있음: " + ", ".join(leaks[:8]) + (" …" if len(leaks) > 8 else ""))
    if mode != "full":
        return
    for s in in_uni:
        if s.get("fresh") is True and (s.get("gates") or {}).get("C1") is True:
            missing = [k for k in FULL_NUMS if not is_num(s.get(k))]
            if missing:
                errors.append(f"{s.get('ticker')}: full 모드인데 {'/'.join(missing)} 가 숫자가 아님")
            if is_num(s.get("ret_1w")) and abs(s["ret_1w"]) > 60:
                errors.append(f"{s.get('ticker')}: ret_1w 절댓값이 60% 를 넘음 — 자료 오류 의심")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default="docs/data/weekly.json")
    ap.add_argument("--rules", default="docs/data/rules.json")
    ap.add_argument("--universe", default="docs/data/universe.json")
    args = ap.parse_args(argv)
    d, rules, universe = load_or_exit(args.path, "weekly"), load_or_exit(args.rules, "rules"), load_or_exit(args.universe, "universe")
    if not isinstance(d, dict):
        print(f"FAIL {args.path}\n - 최상위가 객체가 아님")
        return 1

    errors: list[str] = []
    check_header(d, errors)
    stocks = d.get("stocks")
    if not isinstance(stocks, list) or not stocks:
        errors.append("stocks 가 비어 있음")
    stocks = [s for s in stocks if isinstance(s, dict)] if isinstance(stocks, list) else []
    tickers = [s.get("ticker") for s in stocks]
    dup = sorted({t for t in tickers if tickers.count(t) > 1}, key=str)
    if dup:
        errors.append(f"stocks 에 중복 종목: {dup}")
    in_uni = [s for s in stocks if s.get("in_universe") is True]
    check_universe(d, universe, stocks, in_uni, errors)
    m_calc = sum(1 for s in in_uni if (s.get("gates") or {}).get("C1") is True)
    fresh_calc = sum(1 for s in in_uni if s.get("fresh") is True)
    if d.get("M") != m_calc:
        errors.append(f"M({d.get('M')}) 이 C1 통과 유니버스 종목 수({m_calc}) 와 다름")
    if d.get("fresh_count") != fresh_calc:
        errors.append(f"fresh_count({d.get('fresh_count')}) 가 fresh 유니버스 종목 수({fresh_calc}) 와 다름")
    as_of, flag_codes = str((d.get("as_of") or {}).get("date")), set(rules.get("flags") or {})
    for s in stocks:
        check_stock(s, as_of, m_calc, flag_codes, errors)
    check_mode(d, in_uni, errors)
    if errors:
        print(f"FAIL {args.path}")
        print("\n".join(f" - {e}" for e in errors))
        return 1
    week = d["week"]
    print(f"OK weekly.json: 유니버스 {d['universe_count']} · 최신 자료 {d['fresh_count']} · 순위 대상 M={d['M']} · "
          f"주 {week['start']}~{week['end']} (거래일 {week['trading_days']}) · 기준일 {d['as_of']['et']} ET · 공개 범위 {d['publish_mode']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
