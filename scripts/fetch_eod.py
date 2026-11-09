"""공개 일별 시세(EOD) 수집 — 종목 목록을 받아 docs/data/eod/{TICKER}.json 으로 저장합니다.

사용:
  python3 -I scripts/fetch_eod.py --provider eodhd --tickers AAPL,MSFT --out docs/data/eod
  python3 -I scripts/fetch_eod.py --provider alphavantage --tickers IBM --out docs/data/eod

키: 환경 변수 EOD_API_KEY (GitHub Actions 에서는 Secret 으로 넣습니다). 없으면 'demo' 로 시도하는데,
    demo 는 공급자가 정한 소수 종목(EODHD: AAPL.US 등, Alpha Vantage: IBM 등)만 됩니다.
키는 절대 출력하지 않고, 저장되는 source_url 에서도 가립니다.

출력(종목당 1개): {"ticker","provider","source_url","fetched_at_utc","adjusted","rows":[{"date","close","adj_close","volume"}]}
요약: docs/data/eod/_manifest.json
종료 코드: 0 = 모두 성공, 2 = 일부 실패(요약에 기록), 1 = 인자 오류
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
        "terms": "EODHD — 무료/데모 키는 개인·비상업 용도, 출처 표기 (https://eodhd.com/financial-apis/terms-of-use)",
        "adjusted": True,
    },
    "alphavantage": {
        "host": "www.alphavantage.co",
        "terms": "Alpha Vantage — 무료 키 개인 용도, 하루 25회 (https://www.alphavantage.co/terms_of_service/)",
        "adjusted": False,  # TIME_SERIES_DAILY 는 수정 종가가 없음 (수정 종가는 유료)
    },
}
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def redact(url: str, key: str) -> str:
    return url.replace(key, "***") if key else url


def http_get_json(url: str, timeout: int = 40):
    req = urllib.request.Request(url, headers={"User-Agent": "fde-lab-learning-project/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — https only, hosts fixed above
        body = resp.read().decode("utf-8", errors="replace")
    if body.lstrip().startswith("<"):
        raise ValueError("JSON 대신 HTML 응답 (봇 확인 또는 오류 페이지)")
    return json.loads(body)


def fetch_eodhd(ticker: str, key: str, since: str) -> tuple[str, list[dict]]:
    url = (
        f"https://eodhd.com/api/eod/{urllib.parse.quote(ticker)}.US"
        f"?api_token={urllib.parse.quote(key)}&fmt=json&from={since}&order=a"
    )
    data = http_get_json(url)
    if not isinstance(data, list):
        raise ValueError(f"예상 밖 응답: {str(data)[:120]}")
    rows = []
    for r in data:
        if r.get("close") is None:
            continue
        rows.append({
            "date": r["date"],
            "close": float(r["close"]),
            "adj_close": float(r.get("adjusted_close", r["close"])),
            "volume": int(r.get("volume") or 0),
        })
    return url, rows


def fetch_alphavantage(ticker: str, key: str, since: str) -> tuple[str, list[dict]]:
    url = (
        "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY"
        f"&symbol={urllib.parse.quote(ticker)}&outputsize=full&apikey={urllib.parse.quote(key)}"
    )
    data = http_get_json(url)
    series = data.get("Time Series (Daily)") if isinstance(data, dict) else None
    if not series:
        msg = data.get("Information") or data.get("Note") or data.get("Error Message") if isinstance(data, dict) else None
        raise ValueError(f"시계열 없음: {str(msg or data)[:160]}")
    rows = []
    for date, v in series.items():
        if date < since:
            continue
        rows.append({
            "date": date,
            "close": float(v["4. close"]),
            "adj_close": float(v["4. close"]),
            "volume": int(float(v["5. volume"])),
        })
    rows.sort(key=lambda r: r["date"])
    return url, rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=sorted(PROVIDERS), required=True)
    ap.add_argument("--tickers", required=True, help="쉼표로 구분, 예: AAPL,MSFT,SPY")
    ap.add_argument("--out", default="docs/data/eod")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD (기본: 400일 전 — 52주 계산에 충분)")
    ap.add_argument("--max-requests", type=int, default=20, help="무료 키 하루 한도 보호 (EODHD 20, Alpha Vantage 25)")
    ap.add_argument("--pause", type=float, default=1.5, help="요청 사이 대기(초)")
    args = ap.parse_args(argv)

    key = os.environ.get("EOD_API_KEY", "").strip() or "demo"
    if key == "demo":
        print("주의: EOD_API_KEY 가 없어 demo 키로 시도합니다 (공급자가 허용한 소수 종목만 됩니다).", file=sys.stderr)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    bad = [t for t in tickers if not TICKER_RE.match(t)]
    if bad:
        print(f"종목 코드 형식 오류: {bad}", file=sys.stderr)
        return 1
    if len(tickers) > args.max_requests:
        print(f"종목 {len(tickers)}개가 --max-requests {args.max_requests} 를 넘습니다. 목록을 줄이거나 한도를 올리세요.", file=sys.stderr)
        return 1

    today = dt.date.today()
    since = args.since or (today - dt.timedelta(days=400)).isoformat()
    os.makedirs(args.out, exist_ok=True)
    fetcher = fetch_eodhd if args.provider == "eodhd" else fetch_alphavantage
    info = PROVIDERS[args.provider]
    manifest = {
        "provider": args.provider,
        "terms": info["terms"],
        "adjusted": info["adjusted"],
        "fetched_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "since": since,
        "tickers": {},
        "errors": {},
    }

    for i, t in enumerate(tickers):
        if i:
            time.sleep(args.pause)
        try:
            url, rows = fetcher(t, key, since)
            if not rows:
                raise ValueError("행이 0개")
            out = {
                "ticker": t,
                "provider": args.provider,
                "source_url": redact(url, key),
                "fetched_at_utc": manifest["fetched_at_utc"],
                "adjusted": info["adjusted"],
                "rows": rows,
            }
            with open(os.path.join(args.out, f"{t}.json"), "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
            manifest["tickers"][t] = {"first": rows[0]["date"], "last": rows[-1]["date"], "rows": len(rows)}
            print(f"OK   {t:6s} {rows[0]['date']} → {rows[-1]['date']} ({len(rows)}행)")
        except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError, TimeoutError) as e:
            manifest["errors"][t] = redact(str(e), key)
            print(f"FAIL {t:6s} {redact(str(e), key)}", file=sys.stderr)

    with open(os.path.join(args.out, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"요약: 성공 {len(manifest['tickers'])} · 실패 {len(manifest['errors'])} → {args.out}/_manifest.json")
    return 2 if manifest["errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
