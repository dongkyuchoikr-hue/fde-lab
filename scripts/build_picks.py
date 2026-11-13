"""weekly.json + rules.json → docs/data/picks.json (이번 주 조건에 가장 잘 맞는 종목 N) + 동결 사본 docs/data/picks/{기준일}.json

사용:
  python3 -I scripts/build_picks.py [--weekly docs/data/weekly.json] [--private "$WORK/weekly_full.json"]
        [--rules docs/data/rules.json] [--universe docs/data/universe.json] [--holidays docs/data/nyse_holidays.json]
        [--out docs/data/picks.json] [--frozen-dir docs/data/picks] [--no-freeze] [--now YYYY-MM-DDTHH:MM:SSZ]

규칙(rules.json v1): 조건 4개(C1 데이터 충분 · C2 200일 추세선 위 · C3 이번 주 급등 아님 · C4 최근 4주 급락일 없음)를 모두
통과한 종목을 점수(추세 40 · 잔잔함 40 · 낙폭 20, 백분위)로 줄 세우고 같은 업종 최대 2개로 위에서부터 5개.
상태: 확정 / 보류(최신 자료 부족 — 동결하지 않음, 다음 실행이 다시 계산) / 선정 없음(통과 0개) /
      지각(다음 거래일 장 시작 뒤 실행 — '참고'로만 보이고, 상태 지각 그대로 동결해 기록에 공백 행으로 남김).
이미 동결 파일이 있는 주를 다시 계산하면, 새 결과(상태가 보류여도)가 동결 파일과 같을 때만 통과합니다. 다르면 아무 파일도 쓰지 않고 1로 끝납니다
(fetch-prices 는 동결된 주의 재시도에서 이 스크립트를 부르지 않고 지난주 결과만 다시 계산합니다).
문장은 전부 규칙 템플릿(AI 아님). 문턱값·가중치는 rules.json 에서 읽어 문장에 넣습니다(숫자를 두 곳에 적지 않음).
공개 범위가 ranks_only 면 숫자 없이 순위·방향만 적습니다.
직전 선정(previous·changes 의 비교 대상) = 가장 최근의 확정·선정 없음 동결 파일(지각 파일은 건너뜀).
changes.buys = 새로 들어온 종목 수, changes.sells = 빠진 종목 수 → build_results 의 비용 차감 선(매수+매도 건수)에 씁니다.
종료 코드: 0 성공 · 1 오류(동결 파일이 이미 있고 내용이 다름 등)
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fdelib import Calendar, dump_json, is_num, load_json, parse_now, run_info, strip_private  # noqa: E402

CLOSING_KO = "이 종목이 뽑힌 이유는 과거 가격 숫자가 위 조건에 맞아서이지, 오를 것이라는 예측이 아닙니다. 이번 주 선정 ≠ 매수 권유. 비중·금액은 이 도구가 정하지 않습니다."
FROZEN_STATUSES = ("확정", "선정 없음", "지각")      # 동결 파일로 남기는 상태
PREV_STATUSES = ("확정", "선정 없음")                # 직전 선정으로 삼는 상태(지각은 '참고'라 비교 대상이 아님)


def g(x) -> str:
    """문턱값 표기: 10.0 → '10', -7.0 → '7'(부호는 문장이 말함), 1.5 → '1.5'."""
    return f"{abs(float(x)):g}"


def why_texts(rules: dict) -> dict[str, str]:
    """미선정 사유 문장 — 문턱값은 rules.json 에서."""
    gt, top_n, cap = rules["gates"], rules["top_n"], rules["sector_cap"]
    return {
        "DATA": f"C1 미충족 — 기준일 자료가 없거나 거래일 {gt['C1'].get('min_days', 200)}일 미만",
        "SUSPECT": "C1 미충족 — 자료 이상 의심",
        "BELOW_MA200": "C2 미충족 — 200일 추세선 아래",
        "SURGE": f"C3 — 이번 주 {g(gt['C3']['surge_1w_max_pct'])}% 넘게 올라 이번 주는 제외",
        "CRASH_DAY": f"C4 — 최근 4주에 하루 {g(gt['C4']['crash_day_min_pct'])}% 넘게 내린 날이 있어 제외",
        "SECTOR_CAP": f"업종 상한 — 같은 업종이 이미 {cap}개",
        "RANK": f"조건은 통과했지만 점수가 {top_n}위 밖",
        "DEFERRED": "이번 주 선정 보류(데이터 지연) — 조건은 통과",
    }


def rule_line(rules: dict) -> str:
    gt, f = rules["gates"], rules["factors"]
    w = {k: round(f[k]["weight"] * 100) for k in ("trend", "calm", "dd")}
    return (f"조건: 데이터 충분 · 200일 추세선 위 · 이번 주 +{g(gt['C3']['surge_1w_max_pct'])}% 급등 아님 · "
            f"최근 4주 −{g(gt['C4']['crash_day_min_pct'])}% 급락 없음 → 점수: 추세 {w['trend']} · 잔잔함 {w['calm']} · 낙폭 {w['dd']} → "
            f"상위 {rules['top_n']} · 같은 업종 최대 {rules['sector_cap']}개")


def rank_ko(r) -> str:
    if not is_num(r):
        return "?"
    if float(r).is_integer():
        return f"{int(r)}"
    return f"공동 {math.floor(r)}"


def nth_ko(r) -> str:
    """'3번째' 식 표기(동률은 '공동 3번째')."""
    return f"{rank_ko(r)}번째"


def flag_message(code: str, s: dict, mode: str, m: int, bench_t: str, rules: dict) -> str:
    full = mode == "full"
    fl, gt = rules["flags"], rules["gates"]
    w = s.get("worst_day_4w") or {}
    if code == "SURGE":
        head = f"이번 주 {s['ret_1w']:+.1f}% 올랐습니다" if full and is_num(s.get("ret_1w")) else f"이번 주 {g(gt['C3']['surge_1w_max_pct'])}% 넘게 올랐습니다"
        return head + " — 규칙은 방금 많이 오른 종목을 그 주에는 고르지 않습니다('방금 오른 것을 사라'가 되지 않도록). 다음 주 다시 봅니다."
    if code == "CRASH_DAY":
        day = w.get("date", "최근")
        drop = f"하루 {w['ret']:.1f}% 하락" if full and is_num(w.get("ret")) else f"하루 {g(gt['C4']['crash_day_min_pct'])}% 넘게 하락"
        if w.get("market_down_too") is True:
            mk = f"같은 날 시장({bench_t})도 {w['bench_ret']:+.1f}%" if full and is_num(w.get("bench_ret")) else f"같은 날 시장({bench_t})도 내린 날"
        elif w.get("market_down_too") is False:
            mk = f"같은 날 시장({bench_t})은 {w['bench_ret']:+.1f}%" if full and is_num(w.get("bench_ret")) else f"같은 날 시장({bench_t})은 크게 내리지 않음"
        else:
            mk = "같은 날 시장 자료 없음"
        if w.get("volume_spike") is True:
            vol = f"그날 거래량은 평소의 {w['vol_ratio_day']:.1f}배" if full and is_num(w.get("vol_ratio_day")) else f"그날 거래량은 평소의 {g(fl['VOL_SPIKE']['threshold'])}배 이상"
        else:
            vol = "그날 거래량은 평소 수준"
        return f"{day}에 {drop} — {mk} · {vol}. 급락 직후 종목은 이유가 정리될 때까지 쉬어 갑니다."
    if code == "BELOW_MA200":
        return "200일 평균 가격보다 아래 — 이번 주 조건 미충족. 이 조건은 과거에 큰 낙폭을 줄인 쪽으로 관찰됐을 뿐, 수익을 높이는 장치가 아니고 앞으로도 그렇다는 뜻이 아닙니다."
    if code == "DATA":
        if not s.get("fresh"):
            return f"기준일 자료가 아직 없어({s.get('last_date') or '자료 없음'}) 순위에서 뺐습니다. 숫자를 지어내거나 전날 값을 끌어오지 않습니다."
        return f"자료가 {s.get('days_of_data', 0)}거래일뿐이라({gt['C1'].get('min_days', 200)}일 필요) 순위에서 뺐습니다."
    if code == "SUSPECT":
        return "가격 자료에 분할 또는 오류로 보이는 하루 큰 변동이 있어 이번 주 순위에서 뺐습니다. 다음 주 전체 재수집 때 다시 확인합니다."
    if code == "VOL_SPIKE":
        head = f"이번 주 거래량이 평소의 {s['vol_ratio']:.1f}배" if full and is_num(s.get("vol_ratio")) else f"이번 주 거래량이 평소의 {g(fl['VOL_SPIKE']['threshold'])}배 이상"
        return head + " — 무슨 일이 있었습니다(실적·공시·지수 편입 등). 좋은 일인지 나쁜 일인지는 거래량만으로 알 수 없습니다."
    if code == "VOLATILE":
        head = f"평소 하루 ±{s['volatility_60d']:.1f}% 움직임" if full and is_num(s.get("volatility_60d")) else f"{m}개 중 출렁임 상위 {g(fl['VOLATILE']['threshold'])}%"
        return head + " — 하루 −5%도 이 종목에선 '평소'일 수 있습니다."
    if code == "VOL_UP":
        head = f"최근 한 달 출렁임이 평소의 {s['vol_ratio_20_60']:.1f}배" if full and is_num(s.get("vol_ratio_20_60")) else f"최근 한 달 출렁임이 평소의 {g(fl['VOL_UP']['threshold'])}배 이상"
        return head + " — 조용하던 종목이 흔들리기 시작한 신호입니다."
    if code == "BIG_DD":
        head = f"최근 3개월 고점에서 최대 {s['max_drawdown_12w']:.1f}%" if full and is_num(s.get("max_drawdown_12w")) else f"최근 3개월 고점에서 {g(fl['BIG_DD']['threshold'])}% 넘게"
        return head + " — 들고 있었다면 그만큼 떨어진 것을 보는 구간이 있었습니다."
    if code == "FAR_FROM_HIGH":
        head = f"52주 고점(수정 종가) 대비 {s['pct_from_52w_high']:.1f}%" if full and is_num(s.get("pct_from_52w_high")) else f"52주 고점(수정 종가)보다 {g(fl['FAR_FROM_HIGH']['threshold'])}% 넘게 아래"
        return head + " — '싸 보인다'와 '싸다'는 다릅니다."
    return code


def reasons_for(s: dict, w: dict, mode: str) -> list[str]:
    full = mode == "full"
    m = w["M"]
    r = s["ranks"]
    bench = w.get("benchmark") or {}
    out = []
    line = f"추세 — 한 달 전 가격이 반년 전보다 얼마나 올랐나: {m}개 중 {rank_ko(r['trend'])}위"
    if full and is_num(s.get("mom_6_1")):
        line += f" ({s['mom_6_1']:+.1f}%"
        if is_num(bench.get("mom_6_1")):
            line += f", 같은 기간 시장 {bench.get('ticker', '')} {bench['mom_6_1']:+.1f}%"
        line += ")"
    out.append(line + ". 최근 한 달은 되돌림이 잦았던 구간이라 계산에서 뺐습니다.")
    line = f"잔잔함 — 평소 하루 움직임이 작은 순서로 {m}개 중 {nth_ko(r['calm'])}"
    if full and is_num(s.get("volatility_60d")):
        line += f" (최근 60거래일 하루 평균 ±{s['volatility_60d']:.1f}%)"
    out.append(line + ".")
    line = f"낙폭 — 최근 3개월 고점에서 바닥까지 내려간 폭이 작은 순서로 {m}개 중 {nth_ko(r['dd'])}"
    if full and is_num(s.get("max_drawdown_12w")):
        line += f" (최대 {s['max_drawdown_12w']:.1f}%)"
    out.append(line + ". 작은 낙폭이 안전을 뜻하지는 않습니다.")
    return out


def reference_line(s: dict, w: dict, mode: str) -> str:
    bench = w.get("benchmark") or {}
    if mode == "full" and is_num(s.get("ret_1w")):
        parts = [f"이번 주 {s['ret_1w']:+.1f}%" + (f" ({bench.get('ticker', '시장')} {bench['ret_1w']:+.1f}%)" if is_num(bench.get("ret_1w")) else "")]
        if is_num(s.get("pct_from_52w_high")):
            parts.append(f"52주 고점 대비 {s['pct_from_52w_high']:.1f}%")
        if is_num(s.get("vol_ratio")):
            parts.append(f"거래량 평소의 {s['vol_ratio']:.1f}배")
        if is_num(s.get("last_close")):
            parts.append(f"이번주 종가 ${s['last_close']:,.2f}")
        return " · ".join(parts)
    parts = [f"이번 주 {s.get('dir_1w') or '?'}"]
    if s.get("beat_bench_1w") is not None:
        parts.append(f"시장({bench.get('ticker', '')})보다 " + ("나음" if s["beat_bench_1w"] else "못함"))
    parts.append("거래량 " + ("급증" if "VOL_SPIKE" in s.get("flags", []) else "평소 수준"))
    parts.append("200일 추세선 위")
    return " · ".join(parts)


def score_of(s: dict, rules: dict):
    p = s.get("pct")
    if not p or not all(is_num(p.get(k)) for k in ("trend", "calm", "dd")):
        return None
    f = rules["factors"]
    return round(f["trend"]["weight"] * p["trend"] + f["calm"]["weight"] * p["calm"] + f["dd"]["weight"] * p["dd"], 1)


def latest_frozen(frozen_dir: str, before: str, statuses=PREV_STATUSES):
    """before 이전 기준일의 동결 파일 중 가장 최근 것(statuses 에 드는 상태만 — 지각 파일은 건너뜀)."""
    files = sorted(glob.glob(os.path.join(frozen_dir, "????-??-??.json")))
    for p in reversed(files):
        stem = os.path.basename(p)[:-5]
        if not (re.match(r"^\d{4}-\d{2}-\d{2}$", stem) and stem < before):
            continue
        d = load_json(p)
        if d.get("status") in statuses:
            return d
    return None


def same_frozen(a: dict, b: dict) -> bool:
    ign = {"run", "generated_at_utc"}
    return {k: v for k, v in a.items() if k not in ign} == {k: v for k, v in b.items() if k not in ign}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weekly", default="docs/data/weekly.json")
    ap.add_argument("--private", default=None, help="build_weekly 의 --private-out (있으면 full 모드 숫자에 씀)")
    ap.add_argument("--rules", default="docs/data/rules.json")
    ap.add_argument("--universe", default="docs/data/universe.json")
    ap.add_argument("--holidays", default="docs/data/nyse_holidays.json")
    ap.add_argument("--out", default="docs/data/picks.json")
    ap.add_argument("--frozen-dir", default="docs/data/picks")
    ap.add_argument("--no-freeze", action="store_true")
    ap.add_argument("--now", default=None)
    args = ap.parse_args(argv)

    w = load_json(args.private) if args.private and os.path.exists(args.private) else load_json(args.weekly)
    rules = load_json(args.rules)
    universe = load_json(args.universe)
    cal = Calendar(load_json(args.holidays))
    now = parse_now(args.now)
    mode = w.get("publish_mode", "ranks_only")
    bench_t = (w.get("benchmark") or {}).get("ticker") or "시장"
    as_of = w["as_of"]["date"]
    as_of_d = dt.date.fromisoformat(as_of)
    m = w["M"]
    top_n, cap = rules["top_n"], rules["sector_cap"]
    uni_count = w.get("universe_count") or len(universe["tickers"])
    min_fresh = math.ceil(rules["min_fresh_ratio"] * uni_count)
    why_ko = why_texts(rules)
    cost_pct = rules["results"]["cost_one_way_pct"]

    stocks = [s for s in w["stocks"] if s.get("in_universe")]
    for s in stocks:
        s["score"] = score_of(s, rules)
    scored = [s for s in stocks if is_num(s.get("score"))]
    scored.sort(key=lambda s: (-s["score"], s["ranks"]["calm"] if s.get("ranks") else 1e9, s["ticker"]))
    for i, s in enumerate(scored, 1):
        s["rank_all"] = i

    prev = latest_frozen(args.frozen_dir, as_of)
    prev_tickers = [p["ticker"] for p in prev.get("picks", [])] if prev else []

    late = cal.is_late(as_of_d, now)
    fresh_count = w.get("fresh_count", 0)
    deferred = fresh_count < min_fresh
    gate_summary = {
        "M": m, "fresh_count": fresh_count, "universe_count": uni_count,
        "above_ma200": sum(1 for s in stocks if s["gates"].get("C2") is True),
        "surge_excluded": sum(1 for s in stocks if s["gates"].get("C3") is False),
        "crash_excluded": sum(1 for s in stocks if s["gates"].get("C4") is False),
        "data_excluded": sum(1 for s in stocks if s["gates"].get("C1") is not True),
    }
    eligible = [s for s in scored if all(s["gates"].get(k) is True for k in ("C1", "C2", "C3", "C4"))]
    gate_summary["passed"] = len(eligible)

    picks, not_selected, sector_count = [], [], {}
    if not deferred:
        for s in eligible:
            sec = s.get("sector_ko") or "기타"
            if len(picks) >= top_n:
                not_selected.append((s, "RANK"))
                continue
            if sector_count.get(sec, 0) >= cap:
                not_selected.append((s, "SECTOR_CAP"))
                continue
            sector_count[sec] = sector_count.get(sec, 0) + 1
            picks.append(s)
    picked = {s["ticker"] for s in picks}
    for s in stocks:
        if s["ticker"] in picked or any(s is x for x, _ in not_selected):
            continue
        code = next((c for c in ("SUSPECT", "DATA", "BELOW_MA200", "SURGE", "CRASH_DAY") if c in s.get("flags", [])), None)
        if code is None and s["gates"].get("C1") is not True:
            code = "DATA"
        if code is None:
            # 조건은 다 통과한 종목이 여기 오는 경우: 선정을 보류한 주(DEFERRED). 선정을 채운 주라면 위 루프에서 RANK/SECTOR_CAP 을 받음.
            code = "DEFERRED" if deferred else "RANK"
        not_selected.append((s, code))

    if late:
        status, msg = "지각", "이번 주 선정은 다음 거래일 장 시작 뒤에 계산돼 '미리 골랐다'고 할 수 없습니다. 참고로만 보이고 기록에는 공백으로 남깁니다."
    elif deferred:
        status, msg = "보류", f"데이터 지연 — {uni_count}개 중 {fresh_count}개만 기준일 자료가 있어 이번 주 선정을 보류합니다(필요 {min_fresh}개). 아래는 지난주 선정(유지 중)."
    elif not picks:
        status, msg = "선정 없음", f"이번 주 조건에 맞는 종목이 없습니다(통과 0/{m}). 아무것도 고르지 않는 것도 이 규칙의 정상 결과입니다."
    else:
        status = "확정"
        if len(picks) < top_n:
            capped = sum(1 for _, c in not_selected if c == "SECTOR_CAP")
            head = (f"이번 주 조건을 통과한 종목은 {len(eligible)}개이고, 같은 업종 최대 {cap}개 규칙으로 {capped}개를 빼서 선정은 {len(picks)}개입니다"
                    if capped else f"이번 주 조건을 통과한 종목이 {len(eligible)}개뿐입니다")
            msg = (head + f" — {m}종목 중 추세선 위 {gate_summary['above_ma200']}개 · "
                   f"급등 제외 {gate_summary['surge_excluded']}개 · 급락 제외 {gate_summary['crash_excluded']}개 · 데이터 지연·확인 필요 {gate_summary['data_excluded']}개")
        else:
            msg = f"조건 4개를 통과한 {len(eligible)}개 중 점수 순으로 {top_n}개(같은 업종 최대 {cap}개)."

    pick_rows = []
    for i, s in enumerate(picks, 1):
        flags = [{"code": c, "name_ko": rules["flags"][c]["name_ko"], "message_ko": flag_message(c, s, mode, m, bench_t, rules)}
                 for c in s.get("flags", []) if c in rules["flags"]]
        row = {
            "rank": i, "ticker": s["ticker"], "name_ko": s.get("name_ko"), "sector_ko": s.get("sector_ko"),
            "score": s["score"], "rank_all": s["rank_all"], "pct": s["pct"], "ranks": s["ranks"],
            "state": "유지" if s["ticker"] in prev_tickers else "신규",
            "reasons_ko": reasons_for(s, w, mode),
            "reference_ko": reference_line(s, w, mode),
            "flags": flags,
            "closing_ko": CLOSING_KO,
            "values": {k: s.get(k) for k in ("ret_1w", "mom_6_1", "volatility_60d", "max_drawdown_12w", "pct_from_52w_high", "vol_ratio", "last_close")},
        }
        pick_rows.append(row)

    ns_rows = []
    for s, code in not_selected:
        ns_rows.append({"ticker": s["ticker"], "name_ko": s.get("name_ko"), "sector_ko": s.get("sector_ko"),
                        "score": s.get("score"), "rank_all": s.get("rank_all"), "why_code": code, "why_ko": why_ko.get(code, code),
                        "flags": s.get("flags", []), "dir_1w": s.get("dir_1w")})
    ns_rows.sort(key=lambda r: (r["rank_all"] is None, r["rank_all"] or 0, r["ticker"]))

    added = sorted(t for t in picked if t not in prev_tickers)
    dropped = []
    for t in prev_tickers:
        if t not in picked:
            cur = next((r for r in ns_rows if r["ticker"] == t), None)
            dropped.append({"ticker": t, "now_code": cur["why_code"] if cur else "GONE", "now_ko": cur["why_ko"] if cur else "유니버스에서 빠짐"})
    kept = sorted(t for t in picked if t in prev_tickers)
    changes = {"added": added, "dropped": dropped, "kept": kept,
               "buys": len(added), "sells": len(dropped),
               "changed_count": max(len(added), len(dropped)) if prev else 0,
               "prev_as_of": prev["as_of"]["date"] if prev else None}

    basket_flags = []
    if status == "확정" and prev and changes["changed_count"] >= rules["flags"]["MANY_CHANGES"]["threshold"]:
        trades = changes["buys"] + changes["sells"]
        tail = ("기록의 '비용 차감' 열이 그 차이입니다" if mode == "full"
                else "비용을 뺀 뒤에도 평균보다 나았는지는 다음 주 복기의 '비용 뺀 뒤' 칸에 남습니다")
        basket_flags.append({"code": "MANY_CHANGES",
                             "message_ko": f"직전 선정과 비교해 {changes['buys']}개가 새로 들어오고 {changes['sells']}개가 빠졌습니다 — 매주 따라 사고팔면 "
                                           f"수수료·환전 비용이 쌓입니다(가정 매매 {trades}건, 건마다 편도 {g(cost_pct)}%). {tail}."})
    if (w.get("benchmark") or {}).get("above_ma200") is False:
        basket_flags.append({"code": "MARKET_BELOW_MA200", "message_ko": f"시장 전체({bench_t})가 200일 추세선 아래입니다. 이런 때는 조건을 통과하는 종목이 줄거나 0개가 됩니다 — 쉬어 가는 것도 선택입니다."})
    if w["week"].get("short"):
        basket_flags.append({"code": "SHORT_WEEK", "message_ko": f"이번 주 미국 장 거래일 {w['week']['trading_days']}일(휴장·단축) — 거래량 배수와 주간 등락이 평소보다 작게 나올 수 있습니다."})

    out = {
        "schema": "picks/1",
        "as_of": w["as_of"],
        "week": w["week"],
        "generated_at_utc": now.strftime("%Y-%m-%d %H:%M"),
        "run": run_info(),
        "rules_version": rules["version"],
        "rules_frozen_on": rules["frozen_on"],
        "universe_version": (w.get("universe") or {}).get("version"),
        "publish_mode": mode,
        "provider": w.get("provider"),
        "status": status,
        "status_message_ko": msg,
        "late": late,
        "gate_summary": gate_summary,
        "market": {"ticker": (w.get("benchmark") or {}).get("ticker"), "above_ma200": (w.get("benchmark") or {}).get("above_ma200"),
                   "dir_1w": (w.get("benchmark") or {}).get("dir_1w")},
        "previous": {"as_of": prev["as_of"]["date"], "status": prev.get("status"), "tickers": prev_tickers,
                     "picks": [{k: p.get(k) for k in ("rank", "ticker", "name_ko", "sector_ko", "score")} for p in prev.get("picks", [])]} if prev else None,
        "picks": pick_rows,
        "not_selected": ns_rows,
        "changes": changes,
        "sector_mix": sector_count,
        "basket_flags": basket_flags,
        "rule_line_ko": rule_line(rules),
        "disclaimer_ko": "투자 조언이 아닙니다. 이 목록은 과거 가격 숫자가 미리 정한 규칙에 맞았다는 뜻이지 앞으로 오를 종목이라는 뜻이 아니며, 수익을 예측하지 않습니다.",
    }
    public = strip_private(out, mode)

    # 동결 파일이 이미 있는 주: 새 결과가 같을 때만 통과(상태가 보류여도 비교) — 다르면 아무것도 쓰지 않고 끝냄
    frozen_path = os.path.join(args.frozen_dir, f"{as_of}.json")
    froze = False
    if not args.no_freeze and os.path.exists(frozen_path):
        old = load_json(frozen_path)
        if not same_frozen(old, public):
            print(f"동결 파일 {frozen_path} (상태 {old.get('status')}) 이 이미 있고 새로 계산한 결과(상태 {status})와 다릅니다 — "
                  "지난 선정은 바꾸지 않습니다(규칙). picks.json 도 쓰지 않았습니다. 수동 확인 필요", file=sys.stderr)
            return 1
        print(f"동결 파일 이미 있음(내용 동일): {frozen_path}")
    dump_json(args.out, public)
    if not args.no_freeze and status in FROZEN_STATUSES and not os.path.exists(frozen_path):
        dump_json(frozen_path, public)
        froze = True
    print(f"OK picks.json: 상태 {status} · 선정 {len(pick_rows)}개 {[p['ticker'] for p in pick_rows]} · 통과 {len(eligible)}/{m} · "
          f"변경 {changes['changed_count']}(매수 {changes['buys']} · 매도 {changes['sells']}) · 동결 {'기록' if froze else '안 함'} · 공개 범위 {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
