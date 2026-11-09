# docs/data

`week.json` 한 개 — 첫 화면 카드가 읽는 이번 주 시세 요약입니다. Sprint 2에서는 공개 일별 시세를 손으로 옮깁니다. 자동 수집은 Sprint 3.

| 칸 | 뜻 | 예 |
|---|---|---|
| ticker · name | 종목 코드 · 이름 | "AAPL" · "Apple" |
| week_start · week_end | 이번 주 미국 장 첫·마지막 거래일 | "2026-11-02" · "2026-11-06" |
| prev_close | 지난주 마지막 거래일 종가 (USD) | 숫자 |
| last_close | 이번주 마지막 거래일 종가 (USD) | 숫자 |
| change_pct | (last_close / prev_close - 1) x 100, 소수 둘째 자리 | 숫자 |
| volume_ratio | 이번주 일평균 거래량 / 직전 4주 일평균 거래량 (없으면 null — 카드에서 거래량 줄이 빠짐) | 숫자 또는 null |
| source_name · source_url | 공개 출처 이름 · 내려받은 주소 | "Stooq" · https://... |
| as_of_et · as_of_kst | 기준일 — 마지막 종가 시각을 미국 동부·한국 시간으로 | "2026-11-06 16:00" · "2026-11-07 06:00" |

검사: `python3 -I scripts/check_week_json.py docs/data/week.json` (check-deploy.yml 이 배포 전에 같은 검사를 돌립니다).

키·계정·내 보유 종목은 넣지 않습니다.
