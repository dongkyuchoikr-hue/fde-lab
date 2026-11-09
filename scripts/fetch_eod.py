"""공개 일별 시세(EOD) 수집 — 종목 목록을 받아 작업 폴더에 {TICKER}.json 으로 저장합니다. 작업 폴더는 커밋하지 않습니다.

사용:
  python3 -I scripts/fetch_eod.py --provider eodhd --tickers SPY,AAPL,MSFT --out .work/eod [--expect-last 2026-11-06] [--skip-fresh]
  python3 -I scripts/fetch_eod.py --provider alphavantage --tickers IBM --out .work/eod   (진단용 — 수정 종가 없음)

키: 환경 변수 EOD_API_KEY (GitHub Actions 에서는 Secret). 없으면 'demo' 로 시도하는데, demo 는 공급자가 정한 소수 종목만 됩니다.
    키는 절대 출력하지 않고, 저장되는 source_url 에서도 가립니다. 표준 출력에는 날짜·행 수만 찍고 가격은 찍지 않습니다.
원본 일별 시세는 공급자 이용 조건상 공개(표시·재배포)할 수 없으므로 docs/ 가 아니라 작업 폴더(--out)에만 둡니다.

동작: 종목마다 1회 요청, 요청 사이 --pause 초 대기(EODHD 분당 20회 제한).
  - --expect-last 와 --skip-fresh 를 주면 작업 폴더에 이미 그 날짜까지 있는 종목은 건너뜁니다(재시도 때 한도 절약).
  - HTTP 402(한도 초과)면 즉시 중단하고 남은 종목을 오류로 기록합니다. 404 는 '종목 코드 확인 필요'.
  - 429 는 Retry-After(최대 60초)만큼 기다린 뒤 1회 재시도, 네트워크·5xx 는 5·10·20초 간격으로 최대 3회 재시도.
출력(종목당 1개): {"ticker","provider","source_url","fetched_at_utc","adjusted","rows":[{"date","close","adj_close","volume"}]}
요약: {out}/_manifest.json — provider, terms_url, adjusted, fetched_at_utc, since, expected_last, tickers{T:{first,last,rows,fresh}}, errors{T:사유}
종료 코드: 0 = 모두 성공 · 2 = 일부 실패(요약에 기록) · 1 = 인자 오류
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROVIDERS = {
    "eodhd": {
        "host": "eodhd.com",
        "terms_url": "https://eodhd.com/financial-apis/terms-conditions",
        "terms": "EODHD — 무료 키는 개인·비상업 용도, 데이터 표시·재배포 금지(분석 결과만 게시) (https://eodhd.com/financial-apis/terms-conditions)",
        "adjusted": True,
    },
    "alphavantage": {
        "host": "www.alphavantage.co",
        "terms_url": "https://www.alphavantage.co/terms_of_service/",
        "terms": "Alpha Vantage — 무료 키 개인 용도, 하루 25회, 공개 화면 불가(진단용) (https://www.alphavantage.co/terms_of_service/)",
        "adjusted": False,  # TIME_SERIES_DAILY 는 수정 종가가 없음
    },
}
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class QuotaExceeded(Exception):
    pass


class NotFound(Exception):
    pass


def redact(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def http_get_json(url: str, timeout: int = 40):
    """GET → JSON. 402/404 는 전용 예외, 429 는 Retry-After 대기 후 1회 재시도, 나머지는 3회 재시도."""
    req = urllib.request.Request(url, headers={"User-Agent": "fde-lab-learning-project/1.0"})
    delays = [5, 10, 20]
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — https only, hosts fixed above
                body = resp.read().decode("utf-8", errors="replace")
            if body.lstrip().startswith("<"):
                raise ValueError("JSON 대신 HTML 응답 (봇 확인 또는 오류 페이지)")
            return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 402:
                raise QuotaExceeded("한도 초과(HTTP 402) — 오늘 더 받을 수 없음") from None
            if e.code == 404:
                raise NotFound("종목 코드 확인 필요(HTTP 404)") from None
            if e.code == 429 and attempt == 0:
                wait = min(int(e.headers.get("Retry-After", "20") or 20), 60)
                time.sleep(wait)
                attempt += 1
                continue
            if 500 <= e.code < 600 and attempt < len(delays):
                time.sleep(delays[attempt])
                attempt += 1
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if attempt < len(delays) and not isinstance(e, json.JSONDecodeError):
                time.sleep(delays[attempt])
                attempt += 1
                continue
            raise


def fetch_eodhd(ticker: str, key: str, since: str) -> tuple[str, list[dict]]:
    url = (f"https://eodhd.com/api/eod/{urllib.parse.quote(ticker)}.US"
           f"?api_token={urllib.parse.quote(key)}&fmt=json&from={since}&order=a")
    data = http_get_json(url)
    if not isinstance(data, list):
        raise ValueError(f"예상 밖 응답: {str(data)[:120]}")
    rows = []
    for r in data:
        if r.get("close") is None:
            continue
        rows.append({"date": r["date"], "close": float(r["close"]),
                     "adj_close": float(r.get("adjusted_close", r["close"])), "volume": int(r.get("volume") or 0)})
    return url, rows


def fetch_alphavantage(ticker: str, key: str, since: str) -> tuple[str, list[dict]]:
    url = ("https://www.alphavantage.co/query?function=TIME_SERIES_DAILY"
           f"&symbol={urllib.parse.quote(ticker)}&outputsize=full&apikey={urllib.parse.quote(key)}")
    data = http_get_json(url)
    series = data.get("Time Series (Daily)") if isinstance(data, dict) else None
    if not series:
        msg = (data.get("Information") or data.get("Note") or data.get("Error Message")) if isinstance(data, dict) else None
        raise ValueError(f"시계열 없음: {str(msg or data)[:160]}")
    rows = [{"date": d, "close": float(v["4. close"]), "adj_close": float(v["4. close"]), "volume": int(float(v["5. volume"]))}
            for d, v in series.items() if d >= since]
    rows.sort(key=lambda r: r["date"])
    return url, rows


def already_fresh(path: str, expect_last: str | None) -> bool:
    if not expect_last or not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        rows = d.get("rows") or []
        return bool(rows) and rows[-1]["date"] == expect_last
    except (OSError, ValueError, KeyError):
        return False


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=sorted(PROVIDERS), required=True)
    ap.add_argument("--tickers", required=True, help="쉼표로 구분, 예: SPY,AAPL,MSFT")
    ap.add_argument("--out", default=".work/eod", help="작업 폴더(커밋하지 않음)")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD (기본: 400일 전 — 52주·200일 계산에 충분)")
    ap.add_argument("--max-requests", type=int, default=40, help="한 번 실행에서 보낼 최대 요청 수")
    ap.add_argument("--pause", type=float, default=3.1, help="요청 사이 대기(초) — EODHD 분당 20회")
    ap.add_argument("--expect-last", default=None, help="기대 기준일 YYYY-MM-DD (요약에 fresh 표시, --skip-fresh 와 함께 쓰면 건너뜀)")
    ap.add_argument("--skip-fresh", action="store_true", help="작업 폴더에 이미 기대 기준일까지 있는 종목은 요청하지 않음")
    args = ap.parse_args(argv)

    key = os.environ.get("EOD_API_KEY", "").strip() or "demo"
    if key == "demo":
        print("주의: EOD_API_KEY 가 없어 demo 키로 시도합니다 (공급자가 허용한 소수 종목만 됩니다).", file=sys.stderr)

    tickers = []
    for t in (x.strip().upper() for x in args.tickers.split(",")):
        if t and t not in tickers:
            tickers.append(t)
    bad = [t for t in tickers if not TICKER_RE.match(t)]
    if bad:
        print(f"종목 코드 형식 오류: {bad}", file=sys.stderr)
        return 1

    today = dt.date.today()
    since = args.since or (today - dt.timedelta(days=400)).isoformat()
    os.makedirs(args.out, exist_ok=True)
    fetcher = fetch_eodhd if args.provider == "eodhd" else fetch_alphavantage
    info = PROVIDERS[args.provider]
    manifest_path = os.path.join(args.out, "_manifest.json")
    manifest = {
        "provider": args.provider, "terms_url": info["terms_url"], "terms": info["terms"], "adjusted": info["adjusted"],
        "fetched_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"), "since": since,
        "expected_last": args.expect_last, "tickers": {}, "errors": {}, "skipped_fresh": [],
    }
    if os.path.exists(manifest_path):  # 재시도: 이전 요약의 종목 기록은 유지(건너뛴 종목의 정보)
        try:
            with open(manifest_path, encoding="utf-8") as f:
                old = json.load(f)
            manifest["tickers"] = {t: v for t, v in (old.get("tickers") or {}).items() if t in tickers}
        except (OSError, ValueError):
            pass

    requests_made = 0
    stop_reason = None
    for t in tickers:
        path = os.path.join(args.out, f"{t}.json")
        if args.skip_fresh and already_fresh(path, args.expect_last):
            manifest["skipped_fresh"].append(t)
            print(f"SKIP {t:6s} 이미 {args.expect_last} 까지 있음")
            continue
        if stop_reason:
            manifest["errors"][t] = stop_reason
            continue
        if requests_made >= args.max_requests:
            stop_reason = f"--max-requests {args.max_requests} 도달 — 요청 안 함"
            manifest["errors"][t] = stop_reason
            continue
        if requests_made:
            time.sleep(args.pause)
        requests_made += 1
        try:
            url, rows = fetcher(t, key, since)
            if not rows:
                raise ValueError("행이 0개 — 종목 코드 확인 필요")
            out = {"ticker": t, "provider": args.provider, "source_url": redact(url, key), "fetched_at_utc": manifest["fetched_at_utc"],
                   "adjusted": info["adjusted"], "rows": rows}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
            fresh = (rows[-1]["date"] == args.expect_last) if args.expect_last else None
            manifest["tickers"][t] = {"first": rows[0]["date"], "last": rows[-1]["date"], "rows": len(rows), "fresh": fresh}
            manifest["errors"].pop(t, None)
            print(f"OK   {t:6s} {rows[0]['date']} → {rows[-1]['date']} ({len(rows)}행)" + ("" if fresh is None else (" 최신" if fresh else " 기준일 행 아직 없음")))
        except QuotaExceeded as e:
            stop_reason = str(e)
            manifest["errors"][t] = stop_reason
            print(f"STOP {t:6s} {stop_reason}", file=sys.stderr)
        except (NotFound, urllib.error.URLError, ValueError, KeyError, TimeoutError) as e:
            manifest["errors"][t] = redact(str(e), key)
            print(f"FAIL {t:6s} {redact(str(e), key)}", file=sys.stderr)

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    fresh_n = sum(1 for v in manifest["tickers"].values() if v.get("fresh"))
    print(f"요약: 요청 {requests_made} · 성공 {len(manifest['tickers'])} · 건너뜀 {len(manifest['skipped_fresh'])} · 실패 {len(manifest['errors'])}"
          + (f" · 기준일 {args.expect_last} 최신 {fresh_n}" if args.expect_last else "") + f" → {manifest_path}")
    return 2 if manifest["errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
