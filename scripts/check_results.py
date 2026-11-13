"""docs/data/results/*.json 과 docs/data/track_record.json 검사 — build_results.py 다음, 커밋 전에 실행합니다.

사용: python3 -I scripts/check_results.py [--results-dir docs/data/results] [--track docs/data/track_record.json]
        [--frozen-dir docs/data/picks] [--rules docs/data/rules.json]
종료 코드: 0 통과(기록 파일도 결과 파일도 아직 없는 첫 주도 0) · 1 실패(이유를 " - " 줄로 출력) · 2 사용 오류

검사 항목:
- track_record.json: schema track/1 · publish_mode · rules_version == rules.version · weeks 가 pick_as_of 오름차순·중복 없음 · 행 status ∈ {확정, 보류, 공백}
  · 행 publish_mode(있으면) ∈ {ranks_only, full} 이고 결과 파일의 publish_mode 와 같음 · n_picks 정수 · start == 첫 행
  · summary(rows·n·weeks_beat_universe·weeks_beat_bench·weeks_net_beat_*·weeks_no_picks·weeks_pending·weeks_gap) 재계산 일치
- 행마다 results/{pick_as_of}.json 이 있고 status·result_as_of·beat_universe·beat_bench·pct_rank_among_singles 가 같음 · 기록 행 없는 결과 파일 없음
- 결과 파일: schema results/1 · pick_as_of == 파일 이름 · 동결 선정 picks/{pick_as_of}.json 이 있고 종목 집합이 같음(n_picks 일치)
  · result_as_of 는 pick_as_of 뒤의 날짜(공백만 null) · publish_mode · 종목 행 status ∈ {확정, 데이터 지연, 유니버스 제외(계속 추적), 공백} · beat_* 참/거짓/null
  · 확정+선정 있음 → basket(n == 결과 있는 종목 수 · pct_rank_among_singles 0~100 또는 null · beat_* 참/거짓/null), 지연·공백 행 없음
  · 보류 → 데이터 지연 행 있음, basket 없음 · 공백 → 모든 행 공백, basket 없음
- ranks_only: 결과 파일(파일의 publish_mode 기준)·기록 행(행의 publish_mode, 없으면 기록 파일의 것)·summary(기록 파일의 publish_mode) 어디에도 PRICE_FIELDS 키 없음
  · full: basket.ret_pct·net_ret_pct 와 기록 행의 basket_ret·basket_net_ret·universe_avg_ret·bench_ret_1w 가 숫자. 표준 출력에는 수익률 숫자를 찍지 않습니다.
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
BASKET_KEYS = ("beat_universe", "beat_bench", "net_beat_universe", "net_beat_bench", "pct_rank_among_singles")
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


def bool_or_none(v) -> bool:
    return v is None or isinstance(v, bool)


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
        if "publish_mode" in r and r.get("publish_mode") not in MODES:
            errors.append(f"기록 행 {k}: publish_mode 는 ranks_only 또는 full 이어야 함: {r.get('publish_mode')!r}")
        if not (isinstance(r.get("n_picks"), int) and not isinstance(r.get("n_picks"), bool) and r["n_picks"] >= 0):
            errors.append(f"기록 행 {k}: n_picks 는 0 이상 정수여야 함")
    if track.get("start") != (keys[0] if keys else None):
        errors.append("start 가 첫 행의 pick_as_of 와 다름")
    done = [r for r in weeks if r.get("status") == "확정" and (r.get("n_picks") or 0) > 0]
    want = {"rows": len(weeks), "n": len(done),
            "weeks_beat_universe": sum(1 for r in done if r.get("beat_universe") is True),
            "weeks_beat_bench": sum(1 for r in done if r.get("beat_bench") is True),
            "weeks_net_beat_universe": sum(1 for r in done if r.get("net_beat_universe") is True),
            "weeks_net_beat_bench": sum(1 for r in done if r.get("net_beat_bench") is True),
            "weeks_no_picks": sum(1 for r in weeks if r.get("status") == "확정" and r.get("n_picks", 0) == 0),
            "weeks_pending": sum(1 for r in weeks if r.get("status") == "보류"),
            "weeks_gap": sum(1 for r in weeks if r.get("status") == "공백")}
    sm = track.get("summary") or {}
    errors += [f"summary.{k}({sm.get(k)}) 가 재계산값({v}) 과 다름" for k, v in want.items() if sm.get(k) != v]
    return weeks


def check_result(res: dict, stem: str, row: dict, frozen_dir: str, errors: list[str]) -> None:
    def err(m: str) -> None:
        errors.append(f"results/{stem}.json: {m}")

    if res.get("schema") != "results/1":
        err(f"schema 가 results/1 이 아님: {res.get('schema')!r}")
    if res.get("pick_as_of") != stem:
        err(f"pick_as_of({res.get('pick_as_of')}) 가 파일 이름과 다름")
    status, mode, rao = res.get("status"), res.get("publish_mode"), res.get("result_as_of")
    if status not in ROW_STATUSES:
        err(f"status 는 확정/보류/공백 이어야 함: {status!r}")
    if status != row.get("status"):
        err(f"status({status}) 가 기록 행({row.get('status')}) 과 다름")
    if mode not in MODES:
        err(f"publish_mode 는 ranks_only 또는 full 이어야 함: {mode!r}")
    if "publish_mode" in row and row.get("publish_mode") != mode:
        err(f"기록 행의 publish_mode({row.get('publish_mode')}) 가 결과 파일({mode}) 과 다름")
    if status == "공백" and rao is not None:
        err("공백 인데 result_as_of 가 null 이 아님")
    if status != "공백" and not (DATE_RE.match(str(rao)) and str(rao) > stem):
        err(f"result_as_of 는 pick_as_of 뒤의 날짜여야 함: {rao!r}")
    if rao != row.get("result_as_of"):
        err("result_as_of 가 기록 행과 다름")
    if not isinstance(res.get("picks"), list):
        err("picks 가 목록이 아님")
    picks = [p for p in res.get("picks") or [] if isinstance(p, dict)] if isinstance(res.get("picks"), list) else []
    fp = os.path.join(frozen_dir, f"{stem}.json")
    try:
        frozen = load_json(fp, default=None)
    except json.JSONDecodeError:
        frozen = None
    if not isinstance(frozen, dict):
        err(f"동결 선정 {fp} 이 없거나 읽을 수 없음")
    else:
        ft = [p.get("ticker") for p in frozen.get("picks") or []]
        if set(ft) != {p.get("ticker") for p in picks}:
            err("종목 집합이 동결 선정과 다름")
        if row.get("n_picks") != len(ft):
            err(f"기록 행 n_picks({row.get('n_picks')}) 가 동결 선정 종목 수({len(ft)}) 와 다름")
    statuses = [p.get("status") for p in picks]
    for p in picks:
        if p.get("status") not in PICK_STATUSES:
            err(f"{p.get('ticker')}: 종목 status 오류: {p.get('status')!r}")
        if not (bool_or_none(p.get("beat_universe")) and bool_or_none(p.get("beat_bench"))):
            err(f"{p.get('ticker')}: beat_universe/beat_bench 는 참/거짓/null 이어야 함")
    basket = res.get("basket")
    if status == "확정" and picks:
        if not isinstance(basket, dict):
            err("확정(선정 있음)인데 basket 이 없음")
        else:
            n_out, pr = sum(1 for s in statuses if s in OUTCOME), basket.get("pct_rank_among_singles")
            if basket.get("n") != n_out:
                err(f"basket.n({basket.get('n')}) 이 결과 있는 종목 수({n_out}) 와 다름")
            if pr is not None and not (is_num(pr) and 0 <= pr <= 100):
                err("basket.pct_rank_among_singles 는 0~100 또는 null 이어야 함")
            if not all(bool_or_none(basket.get(k)) for k in ("beat_universe", "beat_bench", "net_beat_universe", "net_beat_bench")):
                err("basket.beat_*/net_beat_* 는 참/거짓/null 이어야 함")
            if mode == "full" and not all(is_num(basket.get(k)) for k in ("ret_pct", "net_ret_pct")):
                err("full 모드인데 basket.ret_pct/net_ret_pct 가 숫자가 아님")
        if any(s not in OUTCOME for s in statuses):
            err("확정 인데 데이터 지연·공백 종목 행이 있음")
    elif basket is not None:
        err("basket 은 확정(선정 있음)일 때만 있어야 함")
    if status == "보류" and "데이터 지연" not in statuses:
        err("보류 인데 데이터 지연 종목 행이 없음")
    if status == "공백" and any(s != "공백" for s in statuses):
        err("공백 인데 종목 행 status 가 공백이 아님")
    for k in BASKET_KEYS:
        if row.get(k) != (basket.get(k) if isinstance(basket, dict) else None):
            err(f"기록 행 {k} 가 basket 과 다름")
    leaks = price_leaks(res) if mode == "ranks_only" else []
    if leaks:
        err(f"ranks_only 인데 가격 칸이 {len(leaks)}곳 남아 있음: " + ", ".join(leaks[:6]))
    missing = [k for k in FULL_ROW_NUMS if not is_num(row.get(k))] if mode == "full" and status == "확정" and picks else []
    if missing:
        err(f"full 모드 확정 행인데 기록의 {'/'.join(missing)} 가 숫자가 아님")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", default="docs/data/results")
    ap.add_argument("--track", default="docs/data/track_record.json")
    ap.add_argument("--frozen-dir", default="docs/data/picks")
    ap.add_argument("--rules", default="docs/data/rules.json")
    args = ap.parse_args(argv)
    files = {os.path.basename(p)[:-5]: p for p in sorted(glob.glob(os.path.join(args.results_dir, "????-??-??.json")))
             if DATE_RE.match(os.path.basename(p)[:-5])}
    if not os.path.exists(args.track):
        if not files:
            print("OK results: 기록 없음(첫 주) — track_record.json 도 results/ 도 아직 없음")
            return 0
        print(f"FAIL {args.track}\n - 기록 파일이 없는데 결과 파일이 {len(files)}개 있음")
        return 1
    track, rules = load_or_exit(args.track, "track"), load_or_exit(args.rules, "rules")
    if not isinstance(track, dict):
        print(f"FAIL {args.track}\n - 최상위가 객체가 아님")
        return 1

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
        if isinstance(res, dict):
            check_result(res, stem, row, args.frozen_dir, errors)
        else:
            errors.append(f"results/{stem}.json: 최상위가 객체가 아님")
    errors += [f"results/{stem}.json: 기록 행이 없는 결과 파일" for stem in sorted(set(files) - seen)]
    tmode = track.get("publish_mode")
    leaks = price_leaks({k: v for k, v in track.items() if k != "weeks"}) if tmode == "ranks_only" else []
    for row in weeks:
        if row.get("publish_mode", tmode) == "ranks_only":
            leaks += [f"weeks[{row.get('pick_as_of')}].{p}" for p in price_leaks(row)]
    if leaks:
        errors.append(f"track_record.json: ranks_only 인데 가격 칸이 {len(leaks)}곳 남아 있음: " + ", ".join(leaks[:6]))
    if errors:
        print(f"FAIL {args.track}")
        print("\n".join(f" - {e}" for e in errors))
        return 1
    sm = track.get("summary") or {}
    print(f"OK results: 기록 {len(weeks)}행(확정 {sm.get('n')} · 선정 없음 {sm.get('weeks_no_picks')} · 보류 {sm.get('weeks_pending')} · "
          f"공백 {sm.get('weeks_gap')}) · 결과 파일 {len(files)}개 · 시작 {track.get('start')} · 공개 범위 {track.get('publish_mode')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
