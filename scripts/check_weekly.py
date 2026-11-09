"""docs/data/weekly.json 검사 — fetch-prices.yml 이 커밋 전에 실행합니다.

사용: python3 -I scripts/check_weekly.py [docs/data/weekly.json]
통과 0, 실패 1. 검사: 스키마 버전, 기준일·주 범위 형식, 종목별 필수 숫자, 비율 상식 범위, 종가와 1주 수익률 일치(수정 종가가 아닌 경우만).
"""
import json
import math
import re
import sys

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
NUM_REQUIRED = ["last_close", "prev_close", "ret_1w", "high_52w", "low_52w", "pct_from_52w_high", "max_drawdown_12w"]
NUM_OPTIONAL = ["ret_4w", "ret_12w", "ret_26w", "ret_52w", "vol_ratio", "volatility_20d"]


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def main(path: str) -> int:
    errors = []
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"FAIL {path}: {e}")
        return 1
    if d.get("schema") != "weekly/1":
        errors.append(f"schema 가 weekly/1 이 아님: {d.get('schema')!r}")
    as_of, week = d.get("as_of") or {}, d.get("week") or {}
    for k in ("date",):
        if not DATE.match(str(as_of.get(k, ""))):
            errors.append(f"as_of.{k} 형식 오류")
    for k in ("et", "kst"):
        if not STAMP.match(str(as_of.get(k, ""))):
            errors.append(f"as_of.{k} 형식 오류")
    for k in ("start", "end", "prev_end"):
        if not DATE.match(str(week.get(k, ""))):
            errors.append(f"week.{k} 형식 오류")
    if not errors and not (week["prev_end"] < week["start"] <= as_of["date"] <= week["end"]):
        errors.append("날짜 순서 오류: prev_end < start <= as_of.date <= end 여야 함")
    if not d.get("terms"):
        errors.append("terms(출처 이용 조건) 없음")

    stocks = d.get("stocks")
    if not isinstance(stocks, list) or not stocks:
        errors.append("stocks 가 비어 있음")
        stocks = []
    seen = set()
    for s in stocks:
        t = s.get("ticker", "?")
        if t in seen:
            errors.append(f"{t}: 중복")
        seen.add(t)
        for k in NUM_REQUIRED:
            if not is_num(s.get(k)):
                errors.append(f"{t}.{k}: 숫자가 아님 ({s.get(k)!r})")
        for k in NUM_OPTIONAL:
            if s.get(k) is not None and not is_num(s.get(k)):
                errors.append(f"{t}.{k}: 숫자 또는 null 이어야 함")
        if s.get("last_date") != as_of.get("date"):
            errors.append(f"{t}.last_date 가 as_of.date 와 다름")
        if is_num(s.get("ret_1w")) and abs(s["ret_1w"]) > 60:
            errors.append(f"{t}.ret_1w {s['ret_1w']}% 는 상식 범위 밖 (데이터 오류 의심)")
        if is_num(s.get("pct_from_52w_high")) and s["pct_from_52w_high"] > 0.01:
            errors.append(f"{t}.pct_from_52w_high 가 양수 ({s['pct_from_52w_high']}) — 52주 고점 계산 오류")
        if is_num(s.get("vol_ratio")) and s["vol_ratio"] <= 0:
            errors.append(f"{t}.vol_ratio 가 0 이하")
        if not d.get("adjusted") and is_num(s.get("last_close")) and is_num(s.get("prev_close")):
            expect = (s["last_close"] / s["prev_close"] - 1) * 100
            if abs(expect - s["ret_1w"]) > 0.05:
                errors.append(f"{t}.ret_1w {s['ret_1w']} 가 종가로 계산한 {expect:.2f} 와 다름")

    if errors:
        print(f"FAIL {path}")
        for e in errors:
            print(" -", e)
        return 1
    print(f"OK weekly.json: {len(stocks)}종목 · 기준일 {as_of['et']} ET · 주 {week['start']}~{week['end']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "docs/data/weekly.json"))
