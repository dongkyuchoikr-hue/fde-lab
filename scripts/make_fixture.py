"""합성 fixture 생성 — docs/data/fixtures/ (지어낸 값 · publish_mode full).

실제 빌더(build_weekly → build_results → build_picks)를 지어낸 일별 시세로 3주(기준일 2026-10-23 · 10-30 · 11-06) 돌려
(1) 숫자까지 공개하는 화면(?data=fixtures)이 어떻게 보이는지, (2) 파이프라인이 결정적인지(CI 의 --check)를 보여 줍니다.

사용:
  python3 -I scripts/make_fixture.py [--out docs/data/fixtures]   # 임시 폴더에 만든 뒤 --out 폴더를 통째로 바꿉니다
  python3 -I scripts/make_fixture.py --check                      # 다시 만들어 --out 과 바이트 단위 비교 (다르면 종료 코드 1)

- 모든 숫자는 random.Random(20261106) 으로 지어낸 값입니다. 종목 S01~S30('예시 n')·벤치마크 MKT 는 실제 회사·ETF 와 무관합니다.
- 일별 시세 원본은 임시 폴더에만 만들고 지웁니다 — 실제 파이프라인도 원본을 커밋하지 않습니다.
- 일부러 넣은 사례 (a)~(h) 는 make_series() 의 주석과 fixtures/README.md 에 있고, 생성 끝에 하나씩 확인해 찍습니다.
  표준 라이브러리만 씁니다. 종료 코드: 0 성공 · 1 비교 실패/안전장치 · 2 사례 미재현.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fdelib import Calendar, dump_json, load_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "docs", "data")
SEED = 20261106
END = dt.date(2026, 11, 6)      # 마지막 거래일 = 3주차 기준일
N_DAYS = 330                    # 거래일 수(약 16개월 — 52주 고점·200일선 계산에 충분)
FIXED_DATE = "2026-11-09"
SECTORS = ["정보기술", "금융", "헬스케어", "산업재", "경기소비재", "필수소비재", "커뮤니케이션", "에너지", "소재"]
TICKERS = [f"S{i:02d}" for i in range(1, 31)]
WEEKS = [("2026-10-23", "2026-10-24T03:20:00Z"), ("2026-10-30", "2026-10-31T03:20:00Z"), ("2026-11-06", "2026-11-07T03:20:00Z")]
TERMS_EN = "synthetic data — no provider terms apply"

# 종목별 (연 추세 drift, 연 변동성 vol) — 지어낸 값. drift 가 크고 vol 이 작으면 '조건에 잘 맞는' 쪽, drift 가 음수면 200일선 아래로 가는 쪽.
# (g) 정보기술 S01·S10·S19 를 가장 강하게 → 3번째가 업종 상한(2개)에 걸림.  (c) 음수 drift 10종목 → BELOW_MA200 8개 이상.
# (a) S07 도 강하게 → 1주차에 뽑힌 뒤 10-28 급락 → 2주차 결과에 교훈 L2, 선정 변경(dropped: CRASH_DAY)이 남음.
PARAMS: dict[str, tuple[float, float]] = {
    "S01": (0.45, 0.10), "S02": (0.20, 0.18), "S03": (0.12, 0.20), "S04": (-0.15, 0.30), "S05": (-0.30, 0.28),
    "S06": (0.22, 0.16), "S07": (0.60, 0.10), "S08": (-0.35, 0.30), "S09": (0.18, 0.17), "S10": (0.60, 0.09),
    "S11": (0.15, 0.20), "S12": (0.08, 0.22), "S13": (-0.20, 0.25), "S14": (0.25, 0.19), "S15": (-0.25, 0.26),
    "S16": (0.12, 0.15), "S17": (-0.40, 0.32), "S18": (0.05, 0.25), "S19": (0.36, 0.14), "S20": (0.17, 0.21),
    "S21": (-0.28, 0.24), "S22": (0.10, 0.12), "S23": (0.14, 0.18), "S24": (-0.22, 0.27), "S25": (0.10, 0.20),
    "S26": (-0.33, 0.29), "S27": (0.10, 0.16), "S28": (0.02, 0.33), "S29": (0.16, 0.19), "S30": (-0.30, 0.27),
    "MKT": (0.12, 0.14),
}
ADJ_FACTOR = {"S04": 0.98, "S16": 0.98}   # 두 종목만 adj_close = close × 0.98 (수정 종가 ≠ 원 종가인 경우를 무해하게 보여 줌)

README_KO = """# fixtures — 합성 예시 데이터 (지어낸 값)

`scripts/make_fixture.py` 가 만든 publish_mode **full** 예시입니다. 모든 숫자는 지어낸 값이며 실제 종목·가격과 무관합니다
(종목 S01~S30 = '예시 1~30', 벤치마크 MKT = '예시 시장 ETF'). 화면에서 보려면 주소 뒤에 `?data=fixtures` 를 붙입니다.

- 다시 만들기: `python3 -I scripts/make_fixture.py`
- 결정성 검사(CI): `python3 -I scripts/make_fixture.py --check` — 다시 만들어 이 폴더와 바이트 단위로 비교합니다.
- 일별 시세 원본은 임시 폴더에서만 만들고 지웁니다(실제 파이프라인과 같음). 실제 rules.json·nyse_holidays.json 을 그대로 씁니다.

파일: universe.json · source_policy.json · weekly.json(기준일 2026-11-06) · picks.json · picks/{기준일}.json(동결 3주) ·
results/{선정 기준일}.json(2주) · track_record.json

일부러 넣은 사례 (3주차 weekly.json / picks.json 기준):
- (a) S07 — 2026-10-28 하루 −9%, 그날 거래량 3배 → CRASH_DAY (C4) · worst_day_4w
- (b) S12 — 마지막 주 +14% → SURGE (C3)
- (c) 8종목 이상이 200일 평균 아래(음의 추세) → BELOW_MA200 (C2)
- (d) S25 — 거래일 150일뿐 → DATA (C1 미충족, 자료는 최신)
- (e) S30 — 마지막 날 2026-11-06 행 없음(11-05 까지) → fresh=false · DATA (3주차에만)
- (f) S18 — 40거래일 전 하루 +65% → SUSPECT (BIG_JUMP)
- (g) 정보기술 3종목이 상위권 → 3번째는 SECTOR_CAP 으로 not_selected
- (h) S03 — 마지막 주 거래량 3배 → VOL_SPIKE · S22 — 조용하던 40일 뒤 최근 20일 출렁임 3배(추세는 그대로) → VOL_UP
- S04·S16 — adj_close = close × 0.98 (수정 종가와 원 종가가 다른 경우의 예)
"""


def trading_days(cal: Calendar, end: dt.date, n: int) -> list[dt.date]:
    """end 에서 거꾸로 거래일(평일 − 휴장일 파일) n개."""
    days, d = [], end
    while len(days) < n:
        if cal.is_trading_day(d):
            days.append(d)
        d -= dt.timedelta(days=1)
    return days[::-1]


def gauss(rng: random.Random) -> float:
    """random.gauss 대신 Box-Muller — rng.random() 의 열은 파이썬 버전이 바뀌어도 같다고 보장돼 결정적입니다."""
    u1, u2 = rng.random(), rng.random()
    return math.sqrt(-2.0 * math.log(1.0 - u1)) * math.cos(2.0 * math.pi * u2)


def make_series(t: str, days: list[dt.date], rng: random.Random) -> list[dict]:
    """기하 랜덤워크 + 일부러 넣은 사례. 종목마다 뽑는 난수 개수가 같아(2 + 2·N_DAYS) 매개변수를 바꿔도 다른 종목은 안 바뀝니다."""
    drift, vol = PARAMS[t]
    mu, sig = drift / 252, vol / math.sqrt(252)
    price = rng.uniform(20.0, 300.0)
    base_volume = 10 ** rng.uniform(5.5, 7.3)
    zs = [gauss(rng) for _ in days]
    vz = [gauss(rng) for _ in days]
    if t == "S22":                                          # (h) 최근 20일 잡음을 평균을 빼(추세는 그대로) 3배, 그 앞 40일은 0.3배 → vol_20d/vol_60d ≥ 1.5 → VOL_UP
        m = sum(zs[-20:]) / 20
        zs = zs[:-60] + [0.3 * z for z in zs[-60:-20]] + [3.0 * (z - m) for z in zs[-20:]]
    n, final_week = len(days), set(days[-5:])
    rows = []
    for i, d in enumerate(days):
        r, vmult = mu + sig * zs[i], 1.0
        if t == "S07" and d == dt.date(2026, 10, 28):     # (a) 최근 20거래일 안의 하루 −9% + 그날 거래량 3배 → C4 CRASH_DAY
            r, vmult = math.log(0.91), 3.0
        elif t == "S12" and d in final_week:                # (b) 마지막 주 5일 합쳐 +14% → C3 SURGE
            r = math.log(1.14) / 5
        elif t == "S18" and i == n - 41:                    # (f) 40거래일 전 하루 +65% → SUSPECT(BIG_JUMP)
            r = math.log(1.65)
        if t == "S03" and d in final_week:                  # (h) 마지막 주 거래량 3배 → vol_ratio ≥ 2 → VOL_SPIKE
            vmult = 3.0
        if i:
            price *= math.exp(r)
        close = round(price, 4)
        rows.append({"date": d.isoformat(), "close": close, "adj_close": round(close * ADJ_FACTOR.get(t, 1.0), 4),
                     "volume": int(base_volume * math.exp(0.35 * vz[i]) * vmult)})
    if t in ("S25", "S30"):   # (d) S25 거래일 150일뿐 → C1 미충족(DATA), 자료는 최신 · (e) S30 마지막 날 2026-11-06 행 없음 → 3주차에만 fresh=false(DATA)
        rows = rows[-150:] if t == "S25" else rows[:-1]
    return rows


def universe_doc() -> dict:
    return {"schema": "universe/1", "name": "합성 예시 유니버스 30종목 (지어낸 값)", "name_short_ko": "예시 30", "version": "fixture-1",
            "verified_on": FIXED_DATE,
            "source_urls": [{"url": "https://github.com/dongkyuchoikr-hue/fde-lab", "what_ko": "scripts/make_fixture.py 가 만든 합성 데이터", "license": "저장소와 동일"}],
            "benchmark": {"ticker": "MKT", "name_ko": "예시 시장 ETF", "what_ko": "합성 벤치마크"}, "sector_cap": 2,
            "tickers": [{"ticker": t, "name_ko": f"예시 {int(t[1:])}", "sector_ko": SECTORS[(int(t[1:]) - 1) % 9]} for t in TICKERS],
            "synthetic": True, "note_ko": "지어낸 값입니다. 실제 종목·가격과 무관합니다."}


def policy_doc(real: dict) -> dict:
    return {"schema": "source_policy/1", "provider": "synthetic", "provider_name_ko": "합성 예시 데이터 (지어낸 값)", "publish_mode": "full",
            "publish_mode_ko": real.get("publish_mode_ko"),
            "terms_url": "https://github.com/dongkyuchoikr-hue/fde-lab/blob/main/docs/data/README.md", "terms_checked_on": FIXED_DATE,
            "terms_quote_en": TERMS_EN, "terms_quote_ko": "합성 데이터 — 공급자 이용 조건 없음",
            "decision_ko": "숫자를 전부 보여 주는 화면 예시와 검사용으로 지어낸 값을 씁니다.", "decided_on": FIXED_DATE,
            "attribution_ko": "합성 예시 데이터 (지어낸 값) · scripts/make_fixture.py", "synthetic": True}


def run(script: str, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}   # run_info() 가 Actions 실행 id 를 넣지 않게(결정성)
    subprocess.run([sys.executable, "-I", os.path.join(HERE, script), *args], check=True, cwd=ROOT, env=env)


def last_frozen_tickers(frozen_dir: str, before: str) -> list[str]:
    """지난주 동결 파일의 선정 종목 — 실제 워크플로처럼 --extra-tickers 로 함께 계산합니다."""
    files = sorted(p for p in glob.glob(os.path.join(frozen_dir, "????-??-??.json")) if os.path.basename(p)[:-5] < before)
    return [p["ticker"] for p in load_json(files[-1]).get("picks", [])] if files else []


def build(out: str) -> None:
    rules, holidays = os.path.join(DATA, "rules.json"), os.path.join(DATA, "nyse_holidays.json")
    days = trading_days(Calendar(load_json(holidays)), END, N_DAYS)
    rng = random.Random(SEED)
    uni_path, pol_path = os.path.join(out, "universe.json"), os.path.join(out, "source_policy.json")
    dump_json(uni_path, universe_doc())
    dump_json(pol_path, policy_doc(load_json(os.path.join(DATA, "source_policy.json"))))
    frozen, results, track = os.path.join(out, "picks"), os.path.join(out, "results"), os.path.join(out, "track_record.json")
    with tempfile.TemporaryDirectory(prefix="fde-fixture-eod-") as tmp:
        eod, manifest = os.path.join(tmp, "eod"), {}
        for t in TICKERS + ["MKT"]:
            rows = make_series(t, days, rng)
            dump_json(os.path.join(eod, f"{t}.json"), {"ticker": t, "provider": "synthetic", "source_url": "synthetic://scripts/make_fixture.py",
                                                         "fetched_at_utc": "2026-11-07 03:00", "adjusted": True, "rows": rows})
            manifest[t] = {"first": rows[0]["date"], "last": rows[-1]["date"], "rows": len(rows)}
        for i, (as_of, now) in enumerate(WEEKS, 1):
            dump_json(os.path.join(eod, "_manifest.json"), {"provider": "synthetic", "terms": TERMS_EN, "adjusted": True,
                      "fetched_at_utc": now.replace("T", " ")[:16], "since": days[0].isoformat(), "tickers": manifest, "errors": {}})
            last = i == len(WEEKS)
            weekly = os.path.join(out, "weekly.json") if last else os.path.join(tmp, f"weekly_{i}.json")
            picks = os.path.join(out, "picks.json") if last else os.path.join(tmp, f"picks_{i}.json")
            private = os.path.join(tmp, f"weekly_full_{i}.json")
            print(f"--- {i}주차 기준일 {as_of}", flush=True)
            run("build_weekly.py", "--eod", eod, "--private-out", private, "--out", weekly, "--universe", uni_path, "--rules", rules,
                "--policy", pol_path, "--holidays", holidays, "--as-of", as_of, "--now", now,
                "--extra-tickers", ",".join(last_frozen_tickers(frozen, as_of)))
            if i > 1:
                run("build_results.py", "--weekly", weekly, "--private", private, "--rules", rules, "--frozen-dir", frozen,
                    "--results-dir", results, "--track", track, "--now", now)
            run("build_picks.py", "--weekly", weekly, "--private", private, "--rules", rules, "--universe", uni_path, "--holidays", holidays,
                "--out", picks, "--frozen-dir", frozen, "--now", now)
    with open(os.path.join(out, "README.md"), "w", encoding="utf-8") as f:
        f.write(README_KO)


def verify(out: str) -> list[str]:
    """3주차 weekly.json · picks.json 에서 사례 (a)~(h) 를 확인하고 한 줄씩 찍습니다. 실패한 사례 이름을 돌려줍니다."""
    w, p, tr = load_json(os.path.join(out, "weekly.json")), load_json(os.path.join(out, "picks.json")), load_json(os.path.join(out, "track_record.json"))
    st = {s["ticker"]: s for s in w["stocks"]}
    why = {r["ticker"]: r["why_code"] for r in p["not_selected"]}
    cap = [r["ticker"] for r in p["not_selected"] if r["why_code"] == "SECTOR_CAP" and r["sector_ko"] == "정보기술"]
    below = sorted(t for t, s in st.items() if "BELOW_MA200" in s["flags"])

    def has(t: str, flag: str) -> bool:
        return flag in st[t]["flags"]

    cases = [
        ("(a) S07 CRASH_DAY", has("S07", "CRASH_DAY") and why.get("S07") == "CRASH_DAY", f"worst_day_4w={st['S07'].get('worst_day_4w')}"),
        ("(b) S12 SURGE", has("S12", "SURGE") and why.get("S12") == "SURGE", f"ret_1w={st['S12'].get('ret_1w')}"),
        ("(c) BELOW_MA200 ≥ 8", len(below) >= 8, f"{len(below)}개 {below}"),
        ("(d) S25 150일 DATA", st["S25"]["days_of_data"] == 150 and st["S25"]["fresh"] is True and has("S25", "DATA") and why.get("S25") == "DATA",
         f"days_of_data={st['S25']['days_of_data']} fresh={st['S25']['fresh']}"),
        ("(e) S30 stale DATA", st["S30"]["fresh"] is False and st["S30"]["last_date"] == "2026-11-05" and has("S30", "DATA") and why.get("S30") == "DATA",
         f"last_date={st['S30']['last_date']} fresh={st['S30']['fresh']}"),
        ("(f) S18 SUSPECT", has("S18", "SUSPECT") and why.get("S18") == "SUSPECT", f"suspect={(st['S18'].get('suspect') or {}).get('code')}"),
        ("(g) 정보기술 SECTOR_CAP", bool(cap) and p["sector_mix"].get("정보기술") == 2, f"not_selected={cap} sector_mix={p['sector_mix']}"),
        ("(h) S03 VOL_SPIKE · S22 VOL_UP", has("S03", "VOL_SPIKE") and has("S22", "VOL_UP"),
         f"S03 vol_ratio={st['S03'].get('vol_ratio')} · S22 vol_ratio_20_60={st['S22'].get('vol_ratio_20_60')}"),
    ]
    for name, ok, detail in cases:
        print(f"{'OK  ' if ok else 'FAIL'} {name}: {detail}")
    print(f"3주차 선정 {p['status']}: {[(r['ticker'], r['score'], r['sector_ko']) for r in p['picks']]} · M={w['M']} · fresh={w['fresh_count']} "
          f"· 기록 {tr['summary'].get('rows')}행(확정 {tr['summary'].get('n')})")
    return [name for name, ok, _ in cases if not ok]


def listing(d: str) -> dict[str, str]:
    return {os.path.relpath(os.path.join(r, f), d): os.path.join(r, f) for r, _, fs in os.walk(d) for f in fs}


def compare(a: str, b: str) -> list[str]:
    """두 폴더의 파일 목록과 바이트가 같은지. 다른 경로 목록을 돌려줍니다."""
    la, lb = listing(a), listing(b)
    diffs = sorted(set(la) ^ set(lb))
    for rel in sorted(set(la) & set(lb)):
        with open(la[rel], "rb") as fa, open(lb[rel], "rb") as fb:
            if fa.read() != fb.read():
                diffs.append(rel)
    return diffs


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(DATA, "fixtures"))
    ap.add_argument("--check", action="store_true", help="임시 폴더에 다시 만들어 --out 과 비교만 합니다(--out 은 바꾸지 않음)")
    args = ap.parse_args(argv)
    out = os.path.abspath(args.out)
    with tempfile.TemporaryDirectory(prefix="fde-fixture-out-") as tmp_out:
        build(tmp_out)
        failed = verify(tmp_out)
        if failed:
            print(f"사례 미재현: {failed} — PARAMS 또는 make_series() 를 조정하세요", file=sys.stderr)
            return 2
        if args.check:
            diffs = compare(out, tmp_out)
            if diffs:
                print("DIFF (결정성 실패 또는 fixture 가 오래됨 — python3 -I scripts/make_fixture.py 로 다시 만드세요):\n  " + "\n  ".join(diffs), file=sys.stderr)
                return 1
            print(f"OK --check: {out} 의 파일 {len(listing(out))}개가 다시 만든 것과 바이트 단위로 같습니다")
            return 0
        if os.path.exists(out):
            if not load_json(os.path.join(out, "source_policy.json"), default={}).get("synthetic"):
                print(f"안전장치: {out} 는 합성 fixture 폴더(source_policy.json synthetic=true)가 아니라서 덮어쓰지 않습니다", file=sys.stderr)
                return 1
            shutil.rmtree(out)
        shutil.copytree(tmp_out, out)
    print(f"OK fixtures → {out} ({len(listing(out))}개 파일 · 원본 일별 시세는 임시 폴더에서 지움)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
