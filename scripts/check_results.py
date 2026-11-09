"""docs/data/results/*.json 과 docs/data/track_record.json 검사 — build_results.py 다음, 커밋 전에 실행합니다.

사용: python3 -I scripts/check_results.py [--results-dir docs/data/results] [--track docs/data/track_record.json]
        [--frozen-dir docs/data/picks] [--rules docs/data/rules.json]
종료 코드: 0 통과(기록 파일도 결과 파일도 아직 없는 첫 주도 0) · 1 실패(이유를 " - " 줄로 출력) · 2 사용 오류

검사 항목:
- track_record.json: schema track/1 · publish_mode · rules_version == rules.version · weeks 가 pick_as_of 오름차순·중복 없음
  · 행 status ∈ {확정, 보류, 공백} · n_picks 정수 · start == 첫 행 · summary(rows·n·weeks_beat_*·weeks_no_picks·weeks_pending·weeks_gap) 재계산 일치
- 행마다 results/{pick_as_of}.json 이 있고 status·result_as_of·beat_universe·beat_bench·pct_rank_among_singles 가 같음 · 기록 행 없는 결과 파일 없음
- 결과 파일: schema results/1 · pick_as_of == 파일 이름 · 동결 선정 picks/{pick_as_of}.json 이 있고 종목 집합이 같음(n_picks 일치)
  · result_as_of 는 pick_as_of 뒤의 날짜(공백만 null) · publish_mode · 종목 행 status ∈ {확정, 데이터 지연, 유니버스 제외(계속 추적), 공백} · beat_* 참/거짓/null
  · 확정+선정 있음 → basket(n == 결과 있는 종목 수 · pct_rank_among_singles 0~100 또는 null · beat_* 참/거짓/null), 지연·공백 행 없음
  · 보류 → 데이터 지연 행 있음, basket 없음 · 공백 → 모든 행 공백, basket 없음
- ranks_only: 기록·결과 파일 어디에도 PRICE_FIELDS 키 없음 · full(결과 파일의 publish_mode 기준): basket.ret_pct·net_ret_pct 와
  기록 행의 basket_ret·basket_net_ret·universe_avg_ret·bench_ret_1w 가 숫자
표준 출력에는 수익률 숫자를 찍지 않습니다(개수·날짜만).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fdelib import DATE_RE, PRICE_FIELDS, is_num, load_json  # noqa: E402

MODES = {"ranks_only", "full"}
ROW_STATUSES = {"확정", "보류", "공백"}
PICK_STATUSES = {"확정", "데이터 지연", "유니버스 제외(계속 추적)", "공백"}
OUTCOME = {"확정", "유니버스 제외(계속 추적)"}
BASKET_KEYS = ("beat_universe", "beat_bench", "pct_rank_among_singles")
FULL_ROW_NUMS = ("basket_ret", "basket_net_ret", "universe_avg_ret", "bench_ret_1w")


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


def report(path: str, errors: list[str]) -> int:
    print(f"FAIL {path}")
    for e in errors:
        print(" -", e)
    return 1


def price_leaks(obj, path: str = "") -> list[str]:
    """PRICE_FIELDS 키가 남아 있는 경로 목록(값은 모으지 않음)."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if k in PRICE_FIELDS:
                out.append(p)
            out += price_leaks(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            tag = v.get("ticker") if isinstance(v, dict) and isinstance(v.get("ticker"), str) else i
            out += price_leaks(v, f"{path}[{tag}]")
    return out


def bool_or_none(v) -> bool:
    return v is None or isinstance(v, bool)


def result_files(results_dir: str) -> dict[str, str]:
    out = {}
    for p in sorted(glob.glob(os.path.join(results_dir, "????-??-??.json"))):
        stem = os.path.basename(p)[:-5]
        if DATE_RE.match(stem):
            out[stem] = p
    return out


def check_track(track: dict, rules: dict, errors: list[str]) -> list[dict]:
    if track.get("schema") != "track/1":
        errors.append(f"schema 가 track/1 이 아님: {track.get('schema')!r}")
    if track.get("publish_mode") not in MODES:
        errors.append(f"publish_mode 는 ranks_only 또는 full 이어야 함: {track.get('publish_mode')!r}")
    if track.get("rules_version") != rules.get("version"):
        errors.append(f"rules_version({track.get('rules_version')}) 이 rules.json 의 version({rules.get('version')}) 과 다름")
    weeks = track.get("weeks")
    if not isinstance(weeks, list):
        errors.append("weeks 가 목록이 아님")
        return []
    weeks = [r for r in weeks if isinstance(r, dict)]
    keys = [str(r.get("pick_as_of")) for r in weeks]
    if any(not DATE_RE.match(k) for k in keys):
        errors.append("weeks 의 pick_as_of 형식 오류(YYYY-MM-DD)")
    if keys != sorted(set(keys)):
        errors.append("weeks 는 pick_as_of 오름차순이고 중복이 없어야 함")
    for r, k in zip(weeks, keys):
        if r.get("status") not in ROW_STATUSES:
            errors.append(f"기록 행 {k}: status 는 확정/보류/공백 이어야 함: {r.get('status')!r}")
        n = r.get("n_picks")
        if not (isinstance(n, int) and not isinstance(n, bool) and n >= 0):
            errors.append(f"기록 행 {k}: n_picks 는 0 이상 정수여야 함")
    if track.get("start") != (keys[0] if keys else None):
        errors.append("start 가 첫 행의 pick_as_of 와 다름")
    done = [r for r in weeks if r.get("status") == "확정" and (r.get("n_picks") or 0) > 0]
    want = {"rows": len(weeks), "n": len(done),
            "weeks_beat_universe": sum(1 for r in done if r.get("beat_universe") is True),
            "weeks_beat_bench": sum(1 for r in done if r.get("beat_bench") is True),
            "weeks_no_picks": sum(1 for r in weeks if r.get("status") == "확정" and r.get("n_picks", 0) == 0),
            "weeks_pending": sum(1 for r in weeks if r.get("status") == "보류"),
            "weeks_gap": sum(1 for r in weeks if r.get("status") == "공백")}
    sm = track.get("summary") or {}
    for k, v in want.items():
        if sm.get(k) != v:
            errors.append(f"summary.{k}({sm.get(k)}) 가 재계산값({v}) 과 다름")
    return weeks


def check_basket(b: dict, statuses: list, mode, tag: str, errors: list[str]) -> None:
    n_out = sum(1 for s in statuses if s in OUTCOME)
    if b.get("n") != n_out:
        errors.append(f"{tag}: basket.n({b.get('n')}) 이 결과 있는 종목 수({n_out}) 와 다름")
    pr = b.get("pct_rank_among_singles")
    if pr is not None and not (is_num(pr) and 0 <= pr <= 100):
        errors.append(f"{tag}: basket.pct_rank_among_singles 는 0~100 또는 null 이어야 함")
    if not (bool_or_none(b.get("beat_universe")) and bool_or_none(b.get("beat_bench"))):
        errors.append(f"{tag}: basket.beat_universe/beat_bench 는 참/거짓/null 이어야 함")
    if mode == "full" and not all(is_num(b.get(k)) for k in ("ret_pct", "net_ret_pct")):
        errors.append(f"{tag}: full 모드인데 basket.ret_pct/net_ret_pct 가 숫자가 아님")


def check_result(res: dict, stem: str, row: dict, frozen_dir: str, errors: list[str]) -> None:
    tag = f"results/{stem}.json"
    if res.get("schema") != "results/1":
        errors.append(f"{tag}: schema 가 results/1 이 아님: {res.get('schema')!r}")
    if res.get("pick_as_of") != stem:
        errors.append(f"{tag}: pick_as_of({res.get('pick_as_of')}) 가 파일 이름과 다름")
    status, mode = res.get("status"), res.get("publish_mode")
    if status not in ROW_STATUSES:
        errors.append(f"{tag}: status 는 확정/보류/공백 이어야 함: {status!r}")
    if status != row.get("status"):
        errors.append(f"{tag}: status({status}) 가 기록 행({row.get('status')}) 과 다름")
    if mode not in MODES:
        errors.append(f"{tag}: publish_mode 는 ranks_only 또는 full 이어야 함: {mode!r}")
    rao = res.get("result_as_of")
    if status == "공백":
        if rao is not None:
            errors.append(f"{tag}: 공백 인데 result_as_of 가 null 이 아님")
    elif not (DATE_RE.match(str(rao)) and str(rao) > stem):
        errors.append(f"{tag}: result_as_of 는 pick_as_of 뒤의 날짜여야 함: {rao!r}")
    if rao != row.get("result_as_of"):
        errors.append(f"{tag}: result_as_of 가 기록 행과 다름")
    picks = res.get("picks")
    if not isinstance(picks, list):
        errors.append(f"{tag}: picks 가 목록이 아님")
    picks = [p for p in picks if isinstance(p, dict)] if isinstance(picks, list) else []
    fp = os.path.join(frozen_dir, f"{stem}.json")
    try:
        frozen = load_json(fp, default=None)
    except json.JSONDecodeError:
        frozen = None
    if not isinstance(frozen, dict):
        errors.append(f"{tag}: 동결 선정 {fp} 이 없거나 읽을 수 없음")
    else:
        ft = [p.get("ticker") for p in frozen.get("picks") or []]
        if set(ft) != {p.get("ticker") for p in picks}:
            errors.append(f"{tag}: 종목 집합이 동결 선정과 다름")
        if row.get("n_picks") != len(ft):
            errors.append(f"{tag}: 기록 행 n_picks({row.get('n_picks')}) 가 동결 선정 종목 수({len(ft)}) 와 다름")
    statuses = [p.get("status") for p in picks]
    for p in picks:
        if p.get("status") not in PICK_STATUSES:
            errors.append(f"{tag} {p.get('ticker')}: 종목 status 오류: {p.get('status')!r}")
        if not (bool_or_none(p.get("beat_universe")) and bool_or_none(p.get("beat_bench"))):
            errors.append(f"{tag} {p.get('ticker')}: beat_universe/beat_bench 는 참/거짓/null 이어야 함")
    basket = res.get("basket")
    if status == "확정" and picks:
        if not isinstance(basket, dict):
            errors.append(f"{tag}: 확정(선정 있음)인데 basket 이 없음")
        else:
            check_basket(basket, statuses, mode, tag, errors)
        if any(s not in OUTCOME for s in statuses):
            errors.append(f"{tag}: 확정 인데 데이터 지연·공백 종목 행이 있음")
    elif basket is not None:
        errors.append(f"{tag}: basket 은 확정(선정 있음)일 때만 있어야 함")
    if status == "보류" and "데이터 지연" not in statuses:
        errors.append(f"{tag}: 보류 인데 데이터 지연 종목 행이 없음")
    if status == "공백" and any(s != "공백" for s in statuses):
        errors.append(f"{tag}: 공백 인데 종목 행 status 가 공백이 아님")
    for k in BASKET_KEYS:
        want = basket.get(k) if isinstance(basket, dict) else None
        if row.get(k) != want:
            errors.append(f"{tag}: 기록 행 {k} 가 basket 과 다름")
    if mode == "ranks_only":
        leaks = price_leaks(res)
        if leaks:
            errors.append(f"{tag}: ranks_only 인데 가격 칸이 {len(leaks)}곳 남아 있음: " + ", ".join(leaks[:6]))
    elif mode == "full" and status == "확정" and picks:
        missing = [k for k in FULL_ROW_NUMS if not is_num(row.get(k))]
        if missing:
            errors.append(f"{tag}: full 모드 확정 행인데 기록의 {'/'.join(missing)} 가 숫자가 아님")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", default="docs/data/results")
    ap.add_argument("--track", default="docs/data/track_record.json")
    ap.add_argument("--frozen-dir", default="docs/data/picks")
    ap.add_argument("--rules", default="docs/data/rules.json")
    args = ap.parse_args(argv)
    files = result_files(args.results_dir)
    if not os.path.exists(args.track):
        if not files:
            print("OK results: 기록 없음(첫 주) — track_record.json 도 results/ 도 아직 없음")
            return 0
        return report(args.track, [f"기록 파일이 없는데 결과 파일이 {len(files)}개 있음"])
    track, rules = load_or_exit(args.track, "track"), load_or_exit(args.rules, "rules")
    if not isinstance(track, dict):
        return report(args.track, ["최상위가 객체가 아님"])

    errors: list[str] = []
    weeks = check_track(track, rules, errors)
    seen = set()
    for row in weeks:
        stem = str(row.get("pick_as_of"))
        seen.add(stem)
        if stem not in files:
            errors.append(f"기록 행 {stem}: results/{stem}.json 이 없음")
            continue
        try:
            res = load_json(files[stem])
        except json.JSONDecodeError as e:
            errors.append(f"results/{stem}.json: JSON 을 읽을 수 없음({e.msg})")
            continue
        if not isinstance(res, dict):
            errors.append(f"results/{stem}.json: 최상위가 객체가 아님")
            continue
        check_result(res, stem, row, args.frozen_dir, errors)
    for stem in sorted(set(files) - seen):
        errors.append(f"results/{stem}.json: 기록 행이 없는 결과 파일")
    if track.get("publish_mode") == "ranks_only":
        leaks = price_leaks(track)
        if leaks:
            errors.append(f"track_record.json: ranks_only 인데 가격 칸이 {len(leaks)}곳 남아 있음: " + ", ".join(leaks[:6]))
    if errors:
        return report(args.track, errors)
    sm = track.get("summary") or {}
    print(f"OK results: 기록 {len(weeks)}행(확정 {sm.get('n')} · 선정 없음 {sm.get('weeks_no_picks')} · 보류 {sm.get('weeks_pending')} · "
          f"공백 {sm.get('weeks_gap')}) · 결과 파일 {len(files)}개 · 시작 {track.get('start')} · 공개 범위 {track.get('publish_mode')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
