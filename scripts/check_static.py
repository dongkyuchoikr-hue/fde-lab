"""정적 설정 파일 검사 — universe.json · nyse_holidays.json · source_policy.json · rules.json (check-deploy.yml 이 배포 전에 실행).

사용: python3 -I scripts/check_static.py [--universe docs/data/universe.json] [--holidays docs/data/nyse_holidays.json]
        [--policy docs/data/source_policy.json] [--rules docs/data/rules.json] [--today YYYY-MM-DD]
종료 코드: 0 통과 · 1 실패(이유를 " - " 줄로 출력) · 2 사용 오류(인자·파일 없음)

검사 항목:
- universe.json: schema universe/1 · 종목 정확히 30개 · 중복 없음 · 코드 형식 ^[A-Z][A-Z0-9.-]{0,9}$ · name_ko·sector_ko 비어 있지 않음
  · benchmark.ticker 가 있고 종목 목록에 없음 · source_urls 비어 있지 않고 모두 https:// · version 있음 · sector_cap 이 rules.json 과 같음
- nyse_holidays.json: schema holidays/1 · closed·half_days 날짜 형식·중복 없음·평일 · half_days 와 closed 겹치지 않음
  · coverage_until ≥ 오늘(--today 또는 UTC 오늘) + 60일 · close_time_et·half_day_close_time_et 는 HH:MM · source_url https://
- source_policy.json: schema source_policy/1 · provider · publish_mode(ranks_only|full) · terms_url https:// · terms_checked_on 날짜 · decision_ko
  · publish_mode_ko 에 ranks_only·full 두 키
- rules.json: schema rules/1 · version · frozen_on 날짜 · top_n ≥ 1 · sector_cap ≥ 1 · 0 < min_fresh_ratio ≤ 1 · factors(trend/calm/dd) 가중치 합 1.0(±0.001)
  · 요인마다 name_ko/formula/better · gates C1~C4 name_ko(C3 surge_1w_max_pct · C4 crash_day_min_pct 숫자) · flags 마다 name_ko·rule(문턱 쓰는 표시는 threshold 숫자)
  · dir_1w down_pct < up_pct · results.cost_one_way_pct 숫자 · lessons_priority ⊆ lessons 키
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fdelib import DATE_RE, is_num, load_json  # noqa: E402

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MODES = {"ranks_only", "full"}
UNIVERSE_SIZE = 30
FACTORS = ("trend", "calm", "dd")
GATES = ("C1", "C2", "C3", "C4")
THRESHOLD_FLAGS = ("VOL_SPIKE", "VOLATILE", "VOL_UP", "BIG_DD", "FAR_FROM_HIGH", "MANY_CHANGES")


def load_or_exit(path: str, what: str):
    """파일이 없으면 사용 오류(2), JSON 이 깨졌으면 FAIL(1) 로 끝냅니다."""
    try:
        d = load_json(path)
    except FileNotFoundError:
        print(f"사용 오류: {what} 파일이 없습니다 — {path}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(f"FAIL {path}\n - JSON 을 읽을 수 없음: {e.msg} (줄 {e.lineno})")
        sys.exit(1)
    if not isinstance(d, dict):
        print(f"FAIL {path}\n - 최상위가 객체가 아님")
        sys.exit(1)
    return d


def valid_date(s) -> bool:
    if not (isinstance(s, str) and DATE_RE.match(s)):
        return False
    try:
        dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def text(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def is_pos_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def check_universe(u: dict, rules: dict, err) -> None:
    if u.get("schema") != "universe/1":
        err(f"schema 가 universe/1 이 아님: {u.get('schema')!r}")
    rows = u.get("tickers")
    if not isinstance(rows, list):
        err("tickers 가 목록이 아님")
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    if len(rows) != UNIVERSE_SIZE:
        err(f"종목이 {len(rows)}개 — 정확히 {UNIVERSE_SIZE}개여야 함")
    tickers = [r.get("ticker") for r in rows]
    dup = sorted({t for t in tickers if tickers.count(t) > 1}, key=str)
    if dup:
        err(f"중복 종목: {dup}")
    for r in rows:
        t = r.get("ticker")
        if not (isinstance(t, str) and TICKER_RE.match(t)):
            err(f"종목 코드 형식 오류: {t!r}")
        for k in ("name_ko", "sector_ko"):
            if not text(r.get(k)):
                err(f"{t}: {k} 가 비어 있음")
    bt = (u.get("benchmark") or {}).get("ticker")
    if not text(bt):
        err("benchmark.ticker 가 없음")
    elif bt in tickers:
        err(f"benchmark.ticker({bt}) 가 종목 목록에도 들어 있음")
    urls = u.get("source_urls")
    if not (isinstance(urls, list) and urls):
        err("source_urls 가 비어 있음")
    elif not all(isinstance(x, dict) and str(x.get("url", "")).startswith("https://") for x in urls):
        err("source_urls 의 url 은 모두 https:// 로 시작해야 함")
    if not text(u.get("version")):
        err("version 이 없음")
    if u.get("sector_cap") is not None and u.get("sector_cap") != rules.get("sector_cap"):
        err(f"sector_cap({u.get('sector_cap')}) 이 rules.json({rules.get('sector_cap')}) 과 다름")


def check_holidays(h: dict, today: dt.date, err) -> None:
    if h.get("schema") != "holidays/1":
        err(f"schema 가 holidays/1 이 아님: {h.get('schema')!r}")
    sets: dict[str, set] = {}
    for k in ("closed", "half_days"):
        v = h.get(k)
        if not isinstance(v, list):
            err(f"{k} 가 목록이 아님")
            v = []
        bad = [x for x in v if not valid_date(x)]
        if bad:
            err(f"{k} 에 날짜 형식 오류: {bad}")
        good = [x for x in v if valid_date(x)]
        if len(set(good)) != len(good):
            err(f"{k} 에 중복 날짜가 있음")
        weekend = [x for x in good if dt.date.fromisoformat(x).weekday() >= 5]
        if weekend:
            err(f"{k} 에 주말 날짜(오타 의심): {weekend}")
        sets[k] = set(good)
    both = sorted(sets["closed"] & sets["half_days"])
    if both:
        err(f"half_days 와 closed 에 같이 있는 날짜: {both}")
    cu = h.get("coverage_until")
    if not valid_date(cu):
        err(f"coverage_until 날짜 형식 오류: {cu!r}")
    elif dt.date.fromisoformat(cu) < today + dt.timedelta(days=60):
        err(f"coverage_until({cu}) 이 오늘({today})+60일 보다 앞섬 — 다음 해 휴장일을 추가하세요")
    for k in ("close_time_et", "half_day_close_time_et"):
        if not (isinstance(h.get(k), str) and TIME_RE.match(h[k])):
            err(f"{k} 는 HH:MM 이어야 함: {h.get(k)!r}")
    if not str(h.get("source_url", "")).startswith("https://"):
        err("source_url 은 https:// 로 시작해야 함")


def check_policy(p: dict, err) -> None:
    if p.get("schema") != "source_policy/1":
        err(f"schema 가 source_policy/1 이 아님: {p.get('schema')!r}")
    for k in ("provider", "decision_ko"):
        if not text(p.get(k)):
            err(f"{k} 가 비어 있음")
    if p.get("publish_mode") not in MODES:
        err(f"publish_mode 는 ranks_only 또는 full 이어야 함: {p.get('publish_mode')!r}")
    if not str(p.get("terms_url", "")).startswith("https://"):
        err("terms_url 은 https:// 로 시작해야 함")
    if not valid_date(p.get("terms_checked_on")):
        err(f"terms_checked_on 날짜 형식 오류: {p.get('terms_checked_on')!r}")
    pk = p.get("publish_mode_ko")
    if not (isinstance(pk, dict) and all(text(pk.get(k)) for k in MODES)):
        err("publish_mode_ko 에 ranks_only 와 full 설명이 모두 있어야 함")


def check_rules(r: dict, err) -> None:
    if r.get("schema") != "rules/1":
        err(f"schema 가 rules/1 이 아님: {r.get('schema')!r}")
    if not text(r.get("version")):
        err("version 이 없음")
    if not valid_date(r.get("frozen_on")):
        err(f"frozen_on 날짜 형식 오류: {r.get('frozen_on')!r}")
    for k in ("top_n", "sector_cap"):
        if not is_pos_int(r.get(k)):
            err(f"{k} 는 1 이상 정수여야 함: {r.get(k)!r}")
    mf = r.get("min_fresh_ratio")
    if not (is_num(mf) and 0 < mf <= 1):
        err(f"min_fresh_ratio 는 0 초과 1 이하여야 함: {mf!r}")
    f = r.get("factors") or {}
    if not all(isinstance(f.get(k), dict) for k in FACTORS):
        err("factors 에 trend/calm/dd 가 모두 있어야 함")
    else:
        total = sum(f[k].get("weight") for k in FACTORS if is_num(f[k].get("weight")))
        if abs(total - 1.0) > 0.001:
            err(f"factors 가중치 합이 1.0 이 아님: {total}")
        for k in FACTORS:
            miss = [x for x in ("name_ko", "formula", "better") if not text(f[k].get(x))]
            if miss:
                err(f"factors.{k} 에 {'/'.join(miss)} 가 없음")
    g = r.get("gates") or {}
    for k in GATES:
        if not (isinstance(g.get(k), dict) and text(g[k].get("name_ko"))):
            err(f"gates.{k} 에 name_ko 가 없음")
    if not is_num((g.get("C3") or {}).get("surge_1w_max_pct")):
        err("gates.C3.surge_1w_max_pct 가 숫자가 아님")
    if not is_num((g.get("C4") or {}).get("crash_day_min_pct")):
        err("gates.C4.crash_day_min_pct 가 숫자가 아님")
    fl = r.get("flags")
    if not (isinstance(fl, dict) and fl):
        err("flags 가 비어 있음")
        fl = {}
    for code, spec in fl.items():
        if not (isinstance(spec, dict) and text(spec.get("name_ko")) and text(spec.get("rule"))):
            err(f"flags.{code} 에 name_ko·rule 이 있어야 함")
    for code in THRESHOLD_FLAGS:
        if not is_num((fl.get(code) or {}).get("threshold")):
            err(f"flags.{code}.threshold 가 숫자가 아님(빌더가 씀)")
    d1 = r.get("dir_1w") or {}
    if not (is_num(d1.get("up_pct")) and is_num(d1.get("down_pct")) and d1["down_pct"] < d1["up_pct"]):
        err("dir_1w 의 up_pct/down_pct 는 숫자이고 down_pct < up_pct 여야 함")
    rs = r.get("results") or {}
    if not is_num(rs.get("cost_one_way_pct")):
        err("results.cost_one_way_pct 가 숫자가 아님")
    lessons = rs.get("lessons") or {}
    extra = [x for x in rs.get("lessons_priority") or [] if x not in lessons]
    if extra:
        err(f"results.lessons_priority 에 lessons 에 없는 코드: {extra}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", default="docs/data/universe.json")
    ap.add_argument("--holidays", default="docs/data/nyse_holidays.json")
    ap.add_argument("--policy", default="docs/data/source_policy.json")
    ap.add_argument("--rules", default="docs/data/rules.json")
    ap.add_argument("--today", default=None, help="기준 오늘 날짜 YYYY-MM-DD (기본: UTC 오늘)")
    args = ap.parse_args(argv)
    try:
        today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.timezone.utc).date()
    except ValueError:
        ap.error(f"--today 형식 오류: {args.today}")
    u, h = load_or_exit(args.universe, "universe"), load_or_exit(args.holidays, "holidays")
    p, r = load_or_exit(args.policy, "source_policy"), load_or_exit(args.rules, "rules")

    errors: list[str] = []
    for path, fn, extra in ((args.universe, check_universe, (r,)), (args.holidays, check_holidays, (today,)),
                            (args.policy, check_policy, ()), (args.rules, check_rules, ())):
        name = os.path.basename(path)
        fn(load_or_exit(path, name), *extra, lambda m, name=name: errors.append(f"{name}: {m}"))
    if errors:
        print("FAIL 정적 설정 파일(universe · holidays · source_policy · rules)")
        for e in errors:
            print(" -", e)
        return 1
    print(f"OK 정적 설정: 유니버스 {len(u['tickers'])}종목(v{u['version']}) · 벤치마크 {u['benchmark']['ticker']} · "
          f"휴장일 {len(h['closed'])}일 · 단축 {len(h['half_days'])}일 (coverage_until {h['coverage_until']}, 오늘 {today}) · "
          f"공개 범위 {p['publish_mode']} · 규칙 {r['version']} (frozen_on {r['frozen_on']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
