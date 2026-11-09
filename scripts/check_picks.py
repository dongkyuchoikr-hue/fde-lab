"""docs/data/picks.json 검사 — fetch-prices.yml 이 build_picks.py 다음, 커밋 전에 실행합니다.

사용: python3 -I scripts/check_picks.py [docs/data/picks.json] [--weekly docs/data/weekly.json] [--rules docs/data/rules.json]
        [--frozen-dir docs/data/picks]
종료 코드: 0 통과 · 1 실패(이유를 " - " 줄로 출력) · 2 사용 오류(인자·파일 없음)

검사 항목:
- schema picks/1 · as_of·week·publish_mode 가 weekly.json 과 같음 · rules_version == rules.version · status ∈ {확정, 보류, 선정 없음, 지각}
- 선정 종목마다: weekly 의 유니버스 종목이고 C1~C4 모두 통과 · pct/ranks/sector_ko 가 weekly 와 같음 · score == 가중 백분위 합(±0.1)
  · rank 1..n 순서 · 점수 내림차순 · n ≤ top_n · 업종별 ≤ sector_cap(sector_mix 일치) · reasons_ko 3문장 · reference_ko · closing_ko
  · state 는 지난 선정(previous.tickers)에 있으면 유지, 아니면 신규 · flags 는 {code, name_ko, message_ko} 목록(코드는 weekly 의 flags 안)
- 상태: late → 지각 · fresh_count < ceil(min_fresh_ratio × universe_count) → 보류(선정 비움) · 선정 0개 → 선정 없음(통과 0) · 그 밖 → 확정
  · gate_summary(M·fresh_count·universe_count·passed) 를 weekly 로 재계산해 일치
- not_selected: 선정되지 않은 유니버스 종목을 정확히 한 번씩 · why_code 는 알려진 코드 · changes: added ∪ kept == 선정, dropped == 지난 선정 − 선정
- 동결 사본 {frozen-dir}/{as_of}.json 이 있고 status 가 확정·선정 없음 이면 run·generated_at_utc 만 빼고 같아야 함
- ranks_only: PRICE_FIELDS 키(values 포함)가 어디에도 없음(재귀). 표준 출력에는 가격 숫자를 찍지 않습니다.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fdelib import DATE_RE, PRICE_FIELDS, is_num, load_json  # noqa: E402

STATUSES = {"확정", "보류", "선정 없음", "지각"}
WHY_CODES = {"DATA", "SUSPECT", "BELOW_MA200", "SURGE", "CRASH_DAY", "SECTOR_CAP", "RANK"}
GATES = ("C1", "C2", "C3", "C4")
IGNORE_FROZEN = {"run", "generated_at_utc"}


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


def text(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def check_pick(p: dict, i: int, s: dict | None, rules: dict, prev_tickers: set, errors: list[str]) -> None:
    t = p.get("ticker", "?")
    tag = f"선정 {i} {t}"
    if p.get("rank") != i:
        errors.append(f"{tag}: rank 가 {i} 이어야 함: {p.get('rank')!r}")
    if not s or s.get("in_universe") is not True:
        errors.append(f"{tag}: weekly.json 의 유니버스 종목이 아님")
        return
    if not all((s.get("gates") or {}).get(k) is True for k in GATES):
        errors.append(f"{tag}: 조건 C1~C4 를 모두 통과하지 않음")
    if any(p.get(k) != s.get(k) for k in ("pct", "ranks", "sector_ko")):
        errors.append(f"{tag}: pct/ranks/sector_ko 가 weekly.json 과 다름")
    pct, f = s.get("pct") or {}, rules["factors"]
    if not all(is_num(pct.get(k)) for k in ("trend", "calm", "dd")):
        errors.append(f"{tag}: weekly.json 에 pct(trend/calm/dd) 숫자가 없음")
    elif not is_num(p.get("score")) or abs(sum(f[k]["weight"] * pct[k] for k in ("trend", "calm", "dd")) - p["score"]) > 0.1:
        errors.append(f"{tag}: score 가 백분위 가중합과 0.1 넘게 다름")
    r = p.get("reasons_ko")
    if not (isinstance(r, list) and len(r) == 3 and all(text(x) for x in r)):
        errors.append(f"{tag}: reasons_ko 는 비어 있지 않은 문장 3개여야 함")
    errors += [f"{tag}: {k} 가 비어 있음" for k in ("reference_ko", "closing_ko") if not text(p.get(k))]
    want = "유지" if t in prev_tickers else "신규"
    if p.get("state") != want:
        errors.append(f"{tag}: state 는 {want} 여야 함: {p.get('state')!r}")
    fl = p.get("flags")
    if not (isinstance(fl, list) and all(isinstance(x, dict) and all(text(x.get(k)) for k in ("code", "name_ko", "message_ko")) for x in fl)):
        errors.append(f"{tag}: flags 는 {{code, name_ko, message_ko}} 목록이어야 함")
    elif not {x["code"] for x in fl} <= set(s.get("flags") or []):
        errors.append(f"{tag}: flags 코드가 weekly.json 의 flags 에 없음")


def check_list(d: dict, rules: dict, errors: list[str]) -> list[dict]:
    picks = d.get("picks")
    if not isinstance(picks, list):
        errors.append("picks 가 목록이 아님")
        return []
    picks = [p for p in picks if isinstance(p, dict)]
    if len(picks) > rules["top_n"]:
        errors.append(f"선정이 {len(picks)}개 — top_n({rules['top_n']}) 초과")
    scores = [p.get("score") for p in picks]
    if any(not is_num(x) for x in scores):
        errors.append("선정 종목의 score 가 숫자가 아님")
    elif any(a < b for a, b in zip(scores, scores[1:])):
        errors.append("선정 종목의 점수가 내림차순이 아님")
    if len({p.get("ticker") for p in picks}) != len(picks):
        errors.append("선정 종목에 중복이 있음")
    mix = Counter(p.get("sector_ko") or "기타" for p in picks)
    over = [f"{k}({v})" for k, v in mix.items() if v > rules["sector_cap"]]
    if over:
        errors.append(f"업종 상한({rules['sector_cap']}) 초과: {', '.join(over)}")
    if d.get("sector_mix") != dict(mix):
        errors.append("sector_mix 가 선정 종목의 업종별 수와 다름")
    return picks


def check_status(d: dict, w: dict, rules: dict, picks: list[dict], errors: list[str]) -> int:
    status, late = d.get("status"), d.get("late")
    if status not in STATUSES:
        errors.append(f"status 는 확정/보류/선정 없음/지각 중 하나여야 함: {status!r}")
    if not isinstance(late, bool):
        errors.append("late 는 참/거짓이어야 함")
    in_uni = [s for s in w.get("stocks") or [] if s.get("in_universe") is True]
    passed = sum(1 for s in in_uni if all((s.get("gates") or {}).get(k) is True for k in GATES))
    gs = d.get("gate_summary") or {}
    for k, v in (("M", w.get("M")), ("fresh_count", w.get("fresh_count")), ("universe_count", w.get("universe_count")), ("passed", passed)):
        if gs.get(k) != v:
            errors.append(f"gate_summary.{k}({gs.get(k)}) 가 weekly.json 재계산값({v}) 과 다름")
    fresh, uni_count = w.get("fresh_count") or 0, w.get("universe_count") or 0
    min_fresh = math.ceil(rules["min_fresh_ratio"] * uni_count)
    expect = "지각" if late is True else "보류" if fresh < min_fresh else "선정 없음" if not picks else "확정"
    if status != expect:
        errors.append(f"status 가 {status!r} 인데 규칙상 {expect!r} 이어야 함(late={late} · 최신 자료 {fresh}/{uni_count}, 필요 {min_fresh} · 선정 {len(picks)}개)")
    if status == "보류" and picks:
        errors.append("보류 인데 선정 목록이 비어 있지 않음")
    if status == "선정 없음" and passed != 0:
        errors.append(f"선정 없음 인데 조건 통과 종목이 {passed}개")
    if status == "확정" and not picks:
        errors.append("확정 인데 선정 목록이 비어 있음")
    return passed


def check_not_selected(d: dict, w: dict, picks: list[dict], errors: list[str]) -> None:
    ns = d.get("not_selected")
    if not isinstance(ns, list):
        errors.append("not_selected 가 목록이 아님")
        return
    want = {s.get("ticker") for s in w.get("stocks") or [] if s.get("in_universe") is True} - {p.get("ticker") for p in picks}
    tickers = [r.get("ticker") for r in ns if isinstance(r, dict)]
    for label, bad in (("중복 종목", {t for t in tickers if tickers.count(t) > 1}), ("빠진 유니버스 종목", want - set(tickers)),
                       ("있으면 안 되는 종목(선정됐거나 유니버스 밖)", set(tickers) - want)):
        if bad:
            errors.append(f"not_selected 에 {label}: {sorted(bad, key=str)}")
    bad_codes = {r.get("why_code") for r in ns if isinstance(r, dict) and r.get("why_code") not in WHY_CODES}
    if bad_codes:
        errors.append(f"not_selected.why_code 에 알 수 없는 코드: {sorted(bad_codes, key=str)}")


def check_changes(d: dict, picks: list[dict], errors: list[str]) -> None:
    picked = {p.get("ticker") for p in picks}
    ch, prev = d.get("changes") or {}, d.get("previous") or {}
    if set(ch.get("added") or []) | set(ch.get("kept") or []) != picked:
        errors.append("changes.added ∪ changes.kept 가 선정 목록과 다름")
    if {x.get("ticker") for x in ch.get("dropped") or [] if isinstance(x, dict)} != set(prev.get("tickers") or []) - picked:
        errors.append("changes.dropped 가 (지난 선정 − 이번 선정) 과 다름")
    if prev and (ch.get("prev_as_of") != prev.get("as_of") or not str(prev.get("as_of")) < str((d.get("as_of") or {}).get("date"))):
        errors.append("previous.as_of 는 as_of.date 이전 날짜이고 changes.prev_as_of 와 같아야 함")


def check_frozen(d: dict, frozen_dir: str, errors: list[str]) -> str:
    if d.get("status") not in ("확정", "선정 없음"):
        return "동결 대상 아님"
    fp = os.path.join(frozen_dir, f"{(d.get('as_of') or {}).get('date')}.json")
    if not os.path.exists(fp):
        return "동결 사본 없음"
    try:
        frozen = load_json(fp)
    except json.JSONDecodeError:
        errors.append(f"동결 사본 {fp} 의 JSON 을 읽을 수 없음")
        return "동결 사본 오류"
    if {k: v for k, v in frozen.items() if k not in IGNORE_FROZEN} != {k: v for k, v in d.items() if k not in IGNORE_FROZEN}:
        errors.append(f"동결 사본 {fp} 이 picks.json 과 다름(run·generated_at_utc 제외) — 지난 선정은 바꾸지 않음")
    return "동결 사본 일치"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default="docs/data/picks.json")
    ap.add_argument("--weekly", default="docs/data/weekly.json")
    ap.add_argument("--rules", default="docs/data/rules.json")
    ap.add_argument("--frozen-dir", default="docs/data/picks")
    args = ap.parse_args(argv)
    d, w, rules = load_or_exit(args.path, "picks"), load_or_exit(args.weekly, "weekly"), load_or_exit(args.rules, "rules")
    if not isinstance(d, dict) or not isinstance(w, dict):
        print(f"FAIL {args.path}\n - picks.json 또는 weekly.json 의 최상위가 객체가 아님")
        return 1

    errors: list[str] = []
    if d.get("schema") != "picks/1":
        errors.append(f"schema 가 picks/1 이 아님: {d.get('schema')!r}")
    errors += [f"{k} 가 weekly.json 과 다름" for k in ("as_of", "week", "publish_mode") if d.get(k) != w.get(k)]
    if d.get("rules_version") != rules.get("version"):
        errors.append(f"rules_version({d.get('rules_version')}) 이 rules.json 의 version({rules.get('version')}) 과 다름")
    stocks = {s.get("ticker"): s for s in w.get("stocks") or [] if isinstance(s, dict)}
    picks = check_list(d, rules, errors)
    prev_tickers = set((d.get("previous") or {}).get("tickers") or [])
    for i, p in enumerate(picks, 1):
        check_pick(p, i, stocks.get(p.get("ticker")), rules, prev_tickers, errors)
    passed = check_status(d, w, rules, picks, errors)
    check_not_selected(d, w, picks, errors)
    check_changes(d, picks, errors)
    note = check_frozen(d, args.frozen_dir, errors)
    leaks = price_leaks(d) if d.get("publish_mode") == "ranks_only" else []
    if leaks:
        errors.append(f"ranks_only 인데 가격 칸이 {len(leaks)}곳 남아 있음: " + ", ".join(leaks[:8]) + (" …" if len(leaks) > 8 else ""))
    if errors:
        print(f"FAIL {args.path}")
        print("\n".join(f" - {e}" for e in errors))
        return 1
    print(f"OK picks.json: 상태 {d['status']} · 선정 {len(picks)}개 {[p['ticker'] for p in picks]} · 통과 {passed}/{w.get('M')} · "
          f"기준일 {d['as_of']['date']} · 공개 범위 {d['publish_mode']} · {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
