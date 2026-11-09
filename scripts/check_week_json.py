"""docs/data/week.json 검사 — check-deploy.yml 에서 배포 전에 실행합니다.

사용: python3 -I scripts/check_week_json.py docs/data/week.json
통과하면 종료 코드 0, 하나라도 어긋나면 1 과 이유를 출력합니다.
"""
import json
import math
import re
import sys

REQUIRED_TEXT = ["ticker", "week_start", "week_end", "source_url", "as_of_et", "as_of_kst"]
REQUIRED_NUM = ["prev_close", "last_close", "change_pct"]
OPTIONAL_NUM = ["volume_ratio"]  # 없거나 null 이면 카드에서 거래량 줄을 숨깁니다
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


def main(path: str) -> int:
    errors = []
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"FAIL {path}: JSON을 읽을 수 없음 - {e}")
        return 1
    if not isinstance(d, dict):
        print("FAIL: 최상위가 객체가 아님")
        return 1

    for k in REQUIRED_TEXT:
        if not isinstance(d.get(k), str) or not d[k].strip():
            errors.append(f"{k}: 비어 있거나 문자열이 아님")
    for k in REQUIRED_NUM:
        v = d.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            errors.append(f"{k}: 숫자가 아님 ({v!r})")

    for k in OPTIONAL_NUM:
        v = d.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)):
            errors.append(f"{k}: 숫자 또는 null 이어야 함 ({v!r})")

    if not errors:
        for k in ("week_start", "week_end"):
            if not DATE.match(d[k]):
                errors.append(f"{k}: YYYY-MM-DD 형식이 아님 ({d[k]})")
        for k in ("as_of_et", "as_of_kst"):
            if not STAMP.match(d[k]):
                errors.append(f"{k}: 'YYYY-MM-DD HH:MM' 형식이 아님 ({d[k]})")
        if d["week_start"] > d["week_end"]:
            errors.append("week_start 가 week_end 보다 늦음")
        if d["prev_close"] <= 0 or d["last_close"] <= 0:
            errors.append("종가는 0보다 커야 함")
        if d.get("volume_ratio") is not None and d["volume_ratio"] <= 0:
            errors.append("volume_ratio 는 0보다 커야 함")
        expected = (d["last_close"] / d["prev_close"] - 1) * 100
        if abs(expected - d["change_pct"]) > 0.05:
            errors.append(f"change_pct {d['change_pct']} 가 종가로 계산한 {expected:.2f} 와 다름")
        if not d["source_url"].startswith("https://"):
            errors.append("source_url 은 https:// 로 시작해야 함")

    if errors:
        print(f"FAIL {path}")
        for e in errors:
            print(" -", e)
        return 1
    print(
        f"OK {d['ticker']} {d['week_start']}~{d['week_end']} "
        f"{d['change_pct']:+.2f}% "
        + (f"vol x{d['volume_ratio']:.2f} " if d.get("volume_ratio") is not None else "vol 없음 ")
        + f"기준일 {d['as_of_et']} ET"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "docs/data/week.json"))
