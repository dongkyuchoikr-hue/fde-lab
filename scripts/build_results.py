"""지난주 동결 선정 × 이번 주 시세 → docs/data/results/{선정 기준일}.json + docs/data/track_record.json 한 행 추가.

사용:
  python3 -I scripts/build_results.py [--weekly docs/data/weekly.json] [--private "$WORK/weekly_full.json"]
        [--rules docs/data/rules.json] [--frozen-dir docs/data/picks] [--results-dir docs/data/results]
        [--track docs/data/track_record.json] [--now YYYY-MM-DDTHH:MM:SSZ]

- 지난주 = weekly.json 의 week.prev_end 를 기준일로 가진 동결 파일(picks/{날짜}.json). 없으면 아무것도 쓰지 않습니다(첫 주).
- 종목 수익률 = 이번 주 수집본의 ret_1w(같은 시계열의 두 날짜). 바구니 = 선정 종목 단순 평균(동일 비중).
- 기준선 둘: 순위 대상 M개(C1 통과 종목)의 동일비중 평균, 시장(벤치마크, universe.json).
  비용 차감 선 = 편도 cost_one_way_pct × (매수 + 매도 건수) ÷ 선정 종목 수(보통 5) — 건수는 동결 파일의 changes.buys + changes.sells
  (첫 주 = 매수 5건 · 보통 주 = 바뀐 종목당 매도 1 + 매수 1 · '선정 없음' 다음 주 = 매수 5건).
  '선정 없음' 주로 넘어갈 때 판 종목의 매도 비용은 세지 않습니다(그 주엔 바구니가 없어 뺄 곳이 없음) — 가정 문장에 적혀 있습니다.
- 선정 종목 중 하나라도 기준일 자료가 없으면 결과 '보류'(바구니 계산 안 함). 일·월 자동 재시도가 다시 계산하고, 그 뒤에는
  금요일 장 마감 전 수동 실행(Run workflow)만 다시 계산합니다 — 기준일이 다음 주로 넘어가면 공백이 됩니다.
  더 오래된 동결 선정에 확정 결과가 없으면 '공백'으로 남기고 2주 수익률로 메우지 않습니다(보류였다가 끝내 자료가 다 모이지 않은 경우는 그 이유를 적음).
  상태 지각으로 동결된 주도 '공백'(참고용 선정이라 성적을 내지 않음).
- 기록은 추가만 합니다. 확정된 행은 바꾸지 않습니다(바꾸려 하면 오류). 공백도 끝난 결과라 처음 적은 이유 그대로 두고 다시 계산하지 않습니다.
  보류 결과는 내용이 같으면 파일을 다시 쓰지 않습니다.
- 공개 범위 ranks_only 면 수익률 숫자 대신 '나았나' 참/거짓(비용을 뺀 뒤 기준도 함께)과 단일 종목 중 백분위만 공개합니다.
  기록 행마다 그때의 publish_mode 를 적어 두어, 나중에 공개 범위가 바뀌어도 옛 행을 그 행의 기준으로 검사할 수 있게 합니다.
종료 코드: 0 성공(쓸 것이 없어도 0) · 1 오류
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fdelib import dump_json, is_num, load_json, parse_now, run_info, strip_private  # noqa: E402

ASSUMPTIONS_KO = ("가정: 기준일(금요일) 종가 → 다음 기준일 종가로 계산 — 금요일 종가는 토·일에 고른 뒤라 실제로 살 수 없는 가격입니다 · "
                  "선정 종목 동일 비중 · 비용 차감 선 = 편도 0.3% × (매수+매도 건수) ÷ 선정 종목 수(보통 5) (예시 값 · 첫 주는 매수 5건 · '선정 없음' 주로 넘어갈 때 판 종목의 비용은 세지 않음) · "
                  "세금·환전·호가 차이 제외 · 배당은 수정 종가에 포함 · USD 기준 · 실제 매매가 아닌 가상 계산입니다.")
IGNORE_SAME = {"run", "generated_at_utc"}


def frozen_files(frozen_dir: str) -> list[tuple[str, str]]:
    out = []
    for p in sorted(glob.glob(os.path.join(frozen_dir, "????-??-??.json"))):
        d = os.path.basename(p)[:-5]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            out.append((d, p))
    return out


def same_except(a, b, ignore=IGNORE_SAME) -> bool:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return a == b
    return {k: v for k, v in a.items() if k not in ignore} == {k: v for k, v in b.items() if k not in ignore}


def lesson_texts(code: str, ctx: dict, mode: str) -> str:
    full = mode == "full"
    t = ctx.get("ticker", "")
    flag = ctx.get("flag", "")
    b = ctx.get("bench_t", "시장")
    if code == "L1":
        head = f"{t}의 {ctx['ret']:.1f}%는" if full and is_num(ctx.get("ret")) else f"{t}의 큰 하락(7% 넘게)은"
        return f"교훈: {head} 위험 표시 「{flag}」가 붙어 있던 종목에서 나왔습니다. 표시가 맞혔다는 뜻은 아니지만, 표시가 뜻하는 '흔들릴 수 있음'이 이번 주에 일어났습니다."
    if code == "L1b":
        head = f"{ctx['ret']:+.1f}% 올랐습니다" if full and is_num(ctx.get("ret")) else "5% 넘게 올랐습니다"
        return f"교훈: {t}는 위험 표시 「{flag}」가 있었는데도 {head}. 표시는 예측이 아니라 주의입니다 — 한 주 결과는 원래 이렇게 흩어집니다."
    if code == "L2":
        head = f"{ctx['ret']:.1f}% 빠졌습니다" if full and is_num(ctx.get("ret")) else "7% 넘게 빠졌습니다"
        return f"교훈: {t}는 위험 표시가 하나도 없었는데 {head}. 과거 숫자가 좋아도 한 주 뒤는 알 수 없습니다 — 그래서 한 종목이 아니라 5종목으로 나눕니다."
    if code == "L4":
        head = f"시장 전체({b})가 {ctx['bench']:.1f}% 빠진 주였습니다" if full and is_num(ctx.get("bench")) else f"시장 전체({b})가 3% 넘게 빠진 주였습니다"
        return f"교훈: {head}. 이런 주엔 무엇을 골랐든 대부분 내립니다 — '내 종목만 왜?'보다 '시장이 왜?'를 먼저 보세요."
    if code == "L5":
        head = f"교훈: {ctx.get('pick_as_of', '')} 선정은 그 직전 선정보다 {ctx.get('changed', 0)}개 바뀌었습니다(가정 매매 {ctx.get('trades', 0)}건)."
        if full:
            return head + " 매주 따라 사고팔면 비용이 수익을 갉아먹습니다 — 기록의 '비용 차감' 열이 그 차이입니다."
        return head + " 매주 따라 사고팔면 비용이 수익을 갉아먹습니다 — 비용을 빼고도 평균보다 나았는지가 복기의 '비용 뺀 뒤' 칸입니다."
    if code == "L0":
        return "교훈: 지난주엔 조건을 통과한 종목이 없어 아무것도 고르지 않았습니다. 쉬는 것도 규칙의 정상 결과이고, 그 주의 기록은 '선정 없음'으로 남습니다."
    return "교훈: 평범한 주였습니다. 본 이유를 한 줄 적어 두는 습관은 평범한 주에 만들어집니다 — 카드의 「복사」로 메모장에 붙여 두세요."


def pick_stub(p: dict, status: str) -> dict:
    """결과를 낼 수 없는 종목 행(공백·데이터 지연)의 공통 모양."""
    return {"ticker": p["ticker"], "name_ko": p.get("name_ko"), "sector_ko": p.get("sector_ko"), "status": status, "dir_1w": None,
            "beat_universe": None, "beat_bench": None, "rank_1w": None,
            "flags_at_pick": [f["code"] if isinstance(f, dict) else f for f in p.get("flags", [])],
            "reason_short_ko": reason_short_of(p), "in_universe_now": None}


def reason_short_of(p: dict, P: dict | None = None):
    m = (P or {}).get("gate_summary", {}).get("M", "?") if P else "?"
    return f"점수 {p.get('score')} · {m}개 중 {p.get('rank_all')}위" if is_num(p.get("score")) else None


def gap_result(pick_as_of: str, P: dict, msg: str, mode: str, bench: dict, now: dt.datetime) -> dict:
    rows = []
    for p in P.get("picks", []):
        r = pick_stub(p, "공백")
        r["reason_short_ko"] = reason_short_of(p, P)
        rows.append(r)
    return {"schema": "results/1", "pick_as_of": pick_as_of, "result_as_of": None, "holding": None,
            "status": "공백", "status_message_ko": msg, "publish_mode": mode, "M": None,
            "rules_version": P.get("rules_version"), "pick_status": P.get("status"),
            "market": {"ticker": bench.get("ticker"), "dir_1w": None},
            "picks": rows, "basket": None, "lessons": [], "lessons_ko": [], "assumptions_ko": ASSUMPTIONS_KO,
            "generated_at_utc": now.strftime("%Y-%m-%d %H:%M"), "run": run_info()}


def track_row(pick_as_of: str, result_as_of, status: str, mode: str, n_picks: int, basket, changed_count, lessons: list[str]) -> dict:
    row = {"pick_as_of": pick_as_of, "result_as_of": result_as_of, "status": status, "publish_mode": mode, "n_picks": n_picks,
           "beat_universe": basket["beat_universe"] if basket else None,
           "beat_bench": basket["beat_bench"] if basket else None,
           "net_beat_universe": basket["net_beat_universe"] if basket else None,
           "net_beat_bench": basket["net_beat_bench"] if basket else None,
           "pct_rank_among_singles": basket["pct_rank_among_singles"] if basket else None,
           "changed_count": changed_count, "lessons": lessons}
    if basket:
        row.update({"basket_ret": basket["ret_pct"], "basket_net_ret": basket["net_ret_pct"],
                    "universe_avg_ret": basket["universe_avg_ret_pct"], "bench_ret_1w": basket["bench_ret_pct"]})
    return row


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weekly", default="docs/data/weekly.json")
    ap.add_argument("--private", default=None)
    ap.add_argument("--rules", default="docs/data/rules.json")
    ap.add_argument("--frozen-dir", default="docs/data/picks")
    ap.add_argument("--results-dir", default="docs/data/results")
    ap.add_argument("--track", default="docs/data/track_record.json")
    ap.add_argument("--now", default=None)
    args = ap.parse_args(argv)

    w = load_json(args.private) if args.private and os.path.exists(args.private) else load_json(args.weekly)
    rules = load_json(args.rules)
    now = parse_now(args.now)
    mode = w.get("publish_mode", "ranks_only")
    rr = rules["results"]
    result_as_of = w["as_of"]["date"]
    prev_end = w["week"]["prev_end"]
    stocks = {s["ticker"]: s for s in w["stocks"]}
    bench = w.get("benchmark") or {}
    m = w.get("M", 0)

    track = load_json(args.track, default=None) or {
        "schema": "track/1", "rules_version": rules["version"], "publish_mode": mode, "start": None,
        "assumptions_ko": ASSUMPTIONS_KO, "min_weeks_to_read": rr.get("min_weeks_to_read", 26), "weeks": [], "summary": {},
    }
    rows = {r["pick_as_of"]: r for r in track.get("weeks", [])}
    wrote = []

    frozen = frozen_files(args.frozen_dir)
    if not frozen:
        print("지난주 동결 선정 없음 — 결과를 계산하지 않습니다(첫 주)")
        return 0

    for pick_as_of, path in frozen:
        if pick_as_of >= result_as_of:
            continue
        P = load_json(path)
        res_path = os.path.join(args.results_dir, f"{pick_as_of}.json")
        existing = load_json(res_path, default=None)
        if existing and existing.get("status") in ("확정", "공백"):
            continue   # 확정·공백은 끝난 결과 — 공백은 처음 적은 이유를 그대로 둠(다음 실행이 더 일반적인 이유로 덮어쓰지 않게)
        ch = P.get("changes") or {}
        picks = P.get("picks", [])

        gap_msg = None
        if P.get("status") == "지각":
            gap_msg = f"{pick_as_of} 선정은 다음 거래일 장 시작 뒤에 계산된 '지각' 선정이라 성적을 내지 않습니다(공백)."
        elif pick_as_of != prev_end and existing and existing.get("status") == "보류":
            gap_msg = (f"{pick_as_of} 선정의 다음 주 자료가 끝내 다 모이지 않아(보류) 결과를 내지 못했습니다 — 공백으로 남기고 "
                       "2주 수익률로 메우지 않습니다.")
        elif pick_as_of != prev_end:
            gap_msg = f"{pick_as_of} 선정의 다음 주 수집이 없어 결과를 계산하지 않았습니다(공백). 2주 수익률로 메우지 않습니다."
        if gap_msg:
            res = gap_result(pick_as_of, P, gap_msg, mode, bench, now)
            public = strip_private(res, mode)
            if not (existing and same_except(existing, public)):
                dump_json(res_path, public)
                wrote.append(f"{pick_as_of}: 공백")
            old = rows.get(pick_as_of)
            if not (old and old.get("status") == "공백"):
                rows[pick_as_of] = track_row(pick_as_of, None, "공백", mode, len(picks), None,
                                             None if P.get("status") == "지각" else ch.get("changed_count"), [])
            continue

        hold = {"from": pick_as_of, "to": result_as_of, "trading_days": w["week"].get("trading_days"), "half_day": w["week"].get("half_day")}
        uni_avg = w.get("universe_avg_ret_1w")
        bench_ret = bench.get("ret_1w")
        # 단일 종목 비교군 = 순위 대상 M개(C1 통과) — 화면의 'M종목 평균'·'단일 종목 M개' 라벨과 같은 집합
        singles = [s["ret_1w"] for s in w["stocks"] if s.get("in_universe") and (s.get("gates") or {}).get("C1") is True and is_num(s.get("ret_1w"))]
        rows_out, rets, all_fresh = [], [], True
        for p in picks:
            s = stocks.get(p["ticker"])
            flags_at_pick = [f["code"] if isinstance(f, dict) else f for f in p.get("flags", [])]
            reason_short = reason_short_of(p, P)
            if s is None or not s.get("fresh") or not is_num(s.get("ret_1w")):
                all_fresh = False
                stub = pick_stub(p, "데이터 지연")
                stub["reason_short_ko"] = reason_short
                stub["in_universe_now"] = bool(s and s.get("in_universe"))
                rows_out.append(stub)
                continue
            r = s["ret_1w"]
            rets.append(r)
            rows_out.append({
                "ticker": p["ticker"], "name_ko": p.get("name_ko"), "sector_ko": p.get("sector_ko"),
                "status": "확정" if s.get("in_universe") else "유니버스 제외(계속 추적)",
                "dir_1w": s.get("dir_1w"),
                "beat_universe": (r > uni_avg) if is_num(uni_avg) else None,
                "beat_bench": (r > bench_ret) if is_num(bench_ret) else None,
                "rank_1w": (s.get("ranks") or {}).get("week"),
                "flags_at_pick": flags_at_pick,
                "reason_short_ko": reason_short,
                "in_universe_now": bool(s.get("in_universe")),
                "ret_pct": r,
                "vs_universe_pp": round(r - uni_avg, 2) if is_num(uni_avg) else None,
                "vs_bench_pp": round(r - bench_ret, 2) if is_num(bench_ret) else None,
                "entry_adj_close": None, "exit_adj_close": None,
            })

        basket = None
        lessons: list[str] = []
        lesson_rows: list[str] = []
        changed = ch.get("changed_count") or 0
        if "buys" in ch or "sells" in ch:
            trades = int(ch.get("buys") or 0) + int(ch.get("sells") or 0)
        else:  # buys/sells 가 없는 옛 동결 파일
            trades = len(picks) if not ch.get("prev_as_of") else changed * 2
        if P.get("status") == "선정 없음":
            status, msg = "확정", "지난주는 '선정 없음'이었습니다 — 바구니 계산 없이 기록에 0개 행으로 남습니다."
            if ch.get("sells"):
                msg += f" 그 주로 넘어가며 판 {ch['sells']}종목의 매도 비용은 세지 않습니다(바구니가 없어 뺄 곳이 없음)."
            lessons = ["L0"]
            lesson_rows = [lesson_texts("L0", {}, mode)]
        elif not picks:
            status, msg = "확정", "지난주 선정 목록이 비어 있어 계산할 것이 없습니다."
        elif all_fresh and rets:
            status, msg = "확정", f"{pick_as_of} 선정 {len(rets)}종목의 {hold['from']} → {hold['to']} 결과(가상 계산)."
            basket_ret = round(sum(rets) / len(rets), 2)
            cost = round(rr["cost_one_way_pct"] * trades / max(len(picks), 1), 2)
            net = round(basket_ret - cost, 2)
            below = sum(1 for x in singles if x < basket_ret)
            basket = {
                "n": len(rets),
                "beat_universe": (basket_ret > uni_avg) if is_num(uni_avg) else None,
                "beat_bench": (basket_ret > bench_ret) if is_num(bench_ret) else None,
                "net_beat_universe": (net > uni_avg) if is_num(uni_avg) else None,
                "net_beat_bench": (net > bench_ret) if is_num(bench_ret) else None,
                "pct_rank_among_singles": round(below / len(singles) * 100, 1) if singles else None,
                "trades_assumed": trades,
                "ret_pct": basket_ret,
                "net_ret_pct": net,
                "cost_pct": cost,
                "universe_avg_ret_pct": uni_avg,
                "bench_ret_pct": bench_ret,
            }
            # 교훈 고르기(우선순위, 최대 2개)
            cand = []
            for row in rows_out:
                r = row.get("ret_pct")
                if not is_num(r):
                    continue
                if r <= -7 and row["flags_at_pick"]:
                    cand.append(("L1", {"ticker": row["ticker"], "ret": r, "flag": rules["flags"].get(row["flags_at_pick"][0], {}).get("name_ko", row["flags_at_pick"][0])}))
                if r >= 5 and row["flags_at_pick"]:
                    cand.append(("L1b", {"ticker": row["ticker"], "ret": r, "flag": rules["flags"].get(row["flags_at_pick"][0], {}).get("name_ko", row["flags_at_pick"][0])}))
                if r <= -7 and not row["flags_at_pick"]:
                    cand.append(("L2", {"ticker": row["ticker"], "ret": r}))
            if is_num(bench_ret) and bench_ret <= -3:
                cand.append(("L4", {"bench": bench_ret, "bench_t": bench.get("ticker", "시장")}))
            if changed >= rules["flags"]["MANY_CHANGES"]["threshold"]:
                cand.append(("L5", {"changed": changed, "trades": trades, "pick_as_of": pick_as_of}))
            order = {c: i for i, c in enumerate(rr["lessons_priority"])}
            cand.sort(key=lambda x: order.get(x[0], 99))
            chosen = cand[:2] or [("L6", {})]
            lessons = [c for c, _ in chosen]
            lesson_rows = [lesson_texts(c, ctx, mode) for c, ctx in chosen]
        else:
            status, msg = "보류", "선정 종목 중 기준일 자료가 없는 종목이 있어 결과를 보류합니다. 일·월 자동 재시도가 다시 계산하고, 그 뒤에도 보류면 금요일 장 마감 전에 Actions → fetch-prices → Run workflow 로 다시 계산할 수 있습니다(그때까지 안 되면 다음 주에 공백으로 남습니다)."

        res = {
            "schema": "results/1", "pick_as_of": pick_as_of, "result_as_of": result_as_of, "holding": hold,
            "status": status, "status_message_ko": msg, "publish_mode": mode, "M": m,
            "rules_version": P.get("rules_version"), "pick_status": P.get("status"),
            "market": {"ticker": bench.get("ticker"), "dir_1w": bench.get("dir_1w")},
            "picks": rows_out, "basket": basket,
            "lessons": lessons, "lessons_ko": lesson_rows,
            "assumptions_ko": ASSUMPTIONS_KO,
            "generated_at_utc": now.strftime("%Y-%m-%d %H:%M"), "run": run_info(),
        }
        public = strip_private(res, mode)
        if existing and same_except(existing, public):
            print(f"{pick_as_of}: 결과 {status} — 지난 실행과 내용이 같아 다시 쓰지 않음")
        else:
            dump_json(res_path, public)
            wrote.append(f"{pick_as_of}: {status}")

        row = strip_private(track_row(pick_as_of, result_as_of, status, mode, len(picks), basket, ch.get("changed_count"), lessons), mode)
        old = rows.get(pick_as_of)
        if old and old.get("status") == "확정" and not same_except(old, row, ignore=set()):
            print(f"기록 행 {pick_as_of} 이 이미 확정돼 있고 내용이 다릅니다 — 기록은 바꾸지 않습니다", file=sys.stderr)
            return 1
        rows[pick_as_of] = row

    # 기록 다시 묶기(추가만) + 요약
    weeks = [rows[k] for k in sorted(rows)]
    track["weeks"] = weeks
    track["start"] = weeks[0]["pick_as_of"] if weeks else None
    track["publish_mode"] = mode
    track["assumptions_ko"] = ASSUMPTIONS_KO   # 설명 문장은 최신으로(행은 그대로 — check_frozen 은 행만 비교)
    done = [r for r in weeks if r["status"] == "확정" and r.get("n_picks", 0) > 0]
    summary = {
        "rows": len(weeks), "n": len(done),
        "weeks_beat_universe": sum(1 for r in done if r.get("beat_universe") is True),
        "weeks_beat_bench": sum(1 for r in done if r.get("beat_bench") is True),
        "weeks_net_beat_universe": sum(1 for r in done if r.get("net_beat_universe") is True),
        "weeks_net_beat_bench": sum(1 for r in done if r.get("net_beat_bench") is True),
        "weeks_no_picks": sum(1 for r in weeks if r["status"] == "확정" and r.get("n_picks", 0) == 0),
        "weeks_pending": sum(1 for r in weeks if r["status"] == "보류"),
        "weeks_gap": sum(1 for r in weeks if r["status"] == "공백"),
        "min_weeks_to_read": rr.get("min_weeks_to_read", 26),
    }
    full_rows = [r for r in done if r.get("publish_mode", mode) == "full" and all(is_num(r.get(k)) for k in ("basket_ret", "basket_net_ret", "universe_avg_ret", "bench_ret_1w"))]
    if mode == "full" and full_rows:
        idx = {"basket_ret": 100.0, "basket_net_ret": 100.0, "universe_avg_ret": 100.0, "bench_ret_1w": 100.0}
        series = []
        for r in full_rows:
            for k in idx:
                idx[k] = round(idx[k] * (1 + r[k] / 100), 2)
            series.append({"pick_as_of": r["pick_as_of"], **idx})
        summary["cum"] = {k: round(v - 100, 2) for k, v in idx.items()}
        summary["idx"] = series
        summary["cum_weeks"] = len(full_rows)
    track["summary"] = summary
    dump_json(args.track, track)
    print("OK results: " + (", ".join(wrote) if wrote else "새로 쓴 결과 없음") + f" · 기록 {summary['rows']}행(확정 {summary['n']}) · 공개 범위 {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
