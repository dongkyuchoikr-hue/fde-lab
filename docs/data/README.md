# docs/data — 화면이 읽는 자료와 그 규칙

모든 파일은 GitHub Actions(`fetch-prices.yml`)가 매주 토요일에 만들어 커밋합니다. 사람이 손으로 고치는 파일은 `universe.json`·`nyse_holidays.json`·`rules.json`·`source_policy.json` 네 개뿐입니다.
키·계정·내 보유 종목·금액은 어디에도 넣지 않습니다.

## 한눈에 — 자료가 흐르는 길

```
공개 일별 시세(EODHD, 비공개 작업 폴더 .work/ — 커밋 안 함)
  → scripts/build_weekly.py  → weekly.json      종목별 이번 주 통계(순위·조건·방향; full 모드면 숫자까지)
  → scripts/build_results.py → results/{지난 선정 기준일}.json + track_record.json   지난주 고른 5개가 실제로 어땠나
  → scripts/build_picks.py   → picks.json + picks/{기준일}.json(동결 사본)             이번 주 조건에 가장 잘 맞는 5개
  → scripts/check_*.py       → 통과해야 커밋·배포
```

## 공급자 결정과 공개 범위 (publish_mode)

`source_policy.json` 에 적혀 있습니다. 2026-11-09 현재: 공급자 **EODHD**, 공개 범위 **ranks_only**.

EODHD 이용 조건(https://eodhd.com/financial-apis/terms-conditions, 2026-11-09 확인) 원문:

> Non-Professional Users are permitted to store, manipulate, and analyze the data for private, non-commercial purposes. However, they are prohibited from: Sharing access to their account with others, including within groups. Selling, reselling, retransmitting, redistributing, **displaying**, or granting access to the Information or Services, whether in its original or repackaged form.

즉 무료 키로 받은 가격 데이터는 개인적으로 분석할 수 있지만 공개 화면에 **표시**할 수 없습니다. 그래서:

- **ranks_only(기본)** — 공개 파일에는 가격·등락률·변동성·낙폭·거래량 배수 같은 숫자를 넣지 않고, 데이터를 분석한 **결과**만 넣습니다: 순위(M개 중 몇 위), 백분위 점수, 조건 통과/탈락, 오름/내림 방향, 참/거짓 표시, 날짜. `fdelib.PRICE_FIELDS` 에 있는 칸이 공개 파일에서 빠지며 `check_weekly.py`·`check_picks.py`·`check_results.py` 가 하나라도 남아 있으면 실패합니다. 원본 일별 행은 `.work/`(커밋 안 함)와 Actions 캐시에만 있고, 로그에도 날짜·행 수만 찍습니다.
- **full** — 주간 집계 숫자까지 공개. 공급자의 서면 허락을 받거나, 출처 표기 조건으로 표시를 허용하는 공급자로 바꿨을 때만 `source_policy.json` 의 `publish_mode` 를 `full` 로 바꿉니다(PR 로, 날짜·근거를 `decision_ko` 에). 바꾼 뒤의 주부터만 숫자가 생기고 그 전 기록은 다시 계산하지 않습니다.

함께 확인한 다른 공급자(2026-11-09 Actions probe 로그): Twelve Data 약관도 "Redistribute or provide external display of Data only if and as expressly authorized by a Redistribution Rights Add-On or separate written agreement" — 무료 플랜의 외부 표시 불가. Alpha Vantage 약관은 개인 용도를 "private and individual in nature" 로 한정, 수정 종가 없음(진단용 probe 에만). Stooq 는 JavaScript 봇 확인 페이지가 돌아와 제외(우회하지 않음).

무료 키 한도(EODHD 문서): 하루 20회 + 가입 보너스 500회, 1년치 이력, 개인 용도. 주 1회 31종목(다우 30 + SPY)을 받으면 하루 20회를 넘는 11회는 보너스에서 빠집니다(약 45주분). 실행 요약(Actions summary)에 남은 보너스가 찍히고, 다 쓰면 선정이 '보류'로 나오므로 그때 공급자나 유니버스 크기를 다시 정합니다.

## 손으로 고치는 파일 4개

| 파일 | 뜻 | 검사 |
|---|---|---|
| `universe.json` (universe/1) | 다우존스 산업평균 30종목 · name_ko · sector_ko · 벤치마크 SPY · 출처(위키백과 구성표, CC BY-SA 4.0) · `verified_on` | 정확히 30개 · 중복 없음 · 벤치마크는 목록 밖 (`check_static.py`) |
| `nyse_holidays.json` (holidays/1) | NYSE 휴장일·단축 거래일(13:00 마감) 2026~2027 · 출처 nyse.com · `coverage_until` | 오늘+60일까지 달력이 있어야 함 — 없으면 기준일 계산이 조용히 틀릴 수 있어 FAIL |
| `rules.json` (rules/1) | 규칙 v1: 조건 4개 · 점수 3요소와 가중치 · 상위 5 · 업종 상한 2 · 위험 표시 임계값 · 교훈 문장 규칙 · `frozen_on` | 가중치 합 1.0 등 |
| `source_policy.json` (source_policy/1) | 공급자 · 공개 범위 · 이용 조건 URL·원문 인용·확인일 · 결정 이유·날짜 · 문의 기록 | publish_mode 값 등 |

## 기준일(as_of)과 '이번 주'

- 기준일 = **가장 최근에 끝난 거래 주의 마지막 거래일**. 지금(미국 동부 시간)이 속한 월~금 주의 마지막 거래일 L 의 마감 시각(16:00, 단축일 13:00)이 지났으면 L, 아니면 직전 주. 주중에 수동 실행해도 수요일 기준일이 생기지 않습니다(`fdelib.Calendar.expected_as_of`).
- 화면의 기준일은 ET 와 KST 둘 다 표시합니다. KST 는 고정 +9 가 아니라 `zoneinfo` 로 계산합니다(겨울 16:00 ET = 다음 날 06:00 KST, 여름 = 05:00 KST, 단축일 13:00 ET = 03:00 KST).
- 이번 주 = 기준일이 속한 월~금. `week.prev_end` = 직전 주 마지막 거래일. `week.trading_days` < 5 또는 단축일 포함이면 `SHORT_WEEK` 표시.

## weekly.json (weekly/2) — 종목별 이번 주 통계

최상위: `as_of{date,et,kst,half_day}` · `week{start,end,prev_end,trading_days,half_day,short}` · `provider` · `publish_mode` · `adjusted` · `terms_url` · `attribution_ko` · `universe{name,version,verified_on,size}` · `benchmark{...}` · `M`(순위 대상 종목 수) · `fresh_count` · `universe_count` · `stocks[]` · `skipped[]` · `run{id,url,sha}`(Actions 실행 증거, 로컬이면 null).

종목(`stocks[]`) 칸 — 항상 공개:

| 칸 | 뜻 |
|---|---|
| `fresh` | 기준일 행이 있음. 없으면 순위·선정에서 빠지고 `DATA` 표시 ('데이터 지연' — 숫자를 지어내거나 전날 값을 끌어오지 않음) |
| `days_of_data` | 기준일까지 거래일 수. 200 미만이면 C1 미충족 |
| `suspect` | 최근 61거래일 중 하루 등락률 절댓값 40% 초과 → 분할 미반영·오류 의심, 순위 제외 |
| `above_ma200` | 기준일 수정 종가 > 최근 200거래일 평균 |
| `dir_1w` | 이번 주 등락 방향: +0.5% 넘으면 상승, −0.5% 밑이면 하락, 사이는 보합 |
| `beat_bench_1w` · `beat_universe_1w` | 이번 주 등락률이 SPY / 유니버스 동일비중 평균보다 높았나 |
| `gates{C1..C4}` | 조건 통과 여부(true/false/null) |
| `ranks{trend,calm,dd,ret_1w}` · `pct{trend,calm,dd}` | M개 중 순위(1 = 가장 좋은 쪽, 동률은 평균 순위)와 백분위 P = (M − rank) ÷ (M − 1) × 100 |
| `flags[]` | 위험 표시 코드(`rules.json` 의 `flags`) |
| `worst_day_4w{date,market_down_too,volume_spike}` | 최근 20거래일 중 최악의 하루 — 그날 시장(SPY)도 1% 넘게 내렸나 · 그날 거래량이 직전 20일 평균의 2배 이상이었나 |

full 모드에만 있는 숫자 칸: `last_close` `prev_close` `ret_1w` `rel_1w` `ret_4w` `mom_6_1` `volatility_20d` `volatility_60d` `vol_ratio_20_60` `max_drawdown_12w` `high_52w` `low_52w` `pct_from_52w_high` `vol_ratio` `worst_day_4w.ret/bench_ret/vol_ratio_day`, 최상위 `universe_avg_ret_1w`, 벤치마크의 같은 칸.

### 공식 (수정 종가 adj_close, 거래일 기준, t = 기준일)

- 이번 주 등락률 `ret_1w` = adj[t] ÷ adj[prev_end] − 1
- 추세 `mom_6_1` = adj[t−21] ÷ adj[t−126] − 1 — "한 달 전 가격이 반년 전보다 얼마나 올랐나". 최근 한 달을 빼는 것은 과거 연구(Jegadeesh·Titman 1993 등)에서 관찰된 단기 되돌림 경향 때문입니다. **이 화면은 그 연구를 검증하지 않으며, 앞으로도 그렇다는 뜻이 아닙니다.** 표준 12-1 대신 6-1을 쓰는 이유: 무료 키의 1년 이력으로는 t−252 가 없을 수 있음.
- 잔잔함 `volatility_60d` = 최근 60거래일 일별 등락률의 표본표준편차(n−1) × 100. 20일이 아니라 60일인 이유: 20일은 급락일 하나가 숫자를 지배합니다. 저변동성 관련 연구(Baker·Bradley·Wurgler 2011)는 배경일 뿐 같은 단서가 붙습니다.
- 낙폭 `max_drawdown_12w` = 최근 61거래일 종가에서 running peak 대비 가장 깊은 하락(%) ≤ 0
- 52주 고점 대비 `pct_from_52w_high` = adj[t] ÷ max(최근 250거래일 adj) − 1 (George·Hwang 2004 는 배경일 뿐). 초보가 "고점에서 많이 빠짐 = 싸다" 로 읽기 쉬워 점수에 넣지 않고 표시만 합니다.
- 거래량 배수 `vol_ratio` = 이번 주 일평균 거래량 ÷ 직전 20거래일 일평균 (원 거래량)
- 200일선 `above_ma200` = adj[t] > mean(adj[t−199..t])

## picks.json (picks/1) — 이번 주 조건에 가장 잘 맞는 종목

1. **모집단 M** = 유니버스 종목 중 C1 통과(기준일 자료 있음 · 200일 이상 · 자료 이상 없음). 조건 통과 여부와 무관하게 M 전부에 점수를 매겨 표에 보입니다.
2. **점수** = 0.4 × P_추세 + 0.4 × P_잔잔함 + 0.2 × P_낙폭 (0~100, 소수 첫째 자리). 추세는 높을수록, 잔잔함(변동성)은 낮을수록, 낙폭은 0에 가까울수록 rank 1.
3. **조건** C1 데이터 충분 · C2 200일 추세선 위 · C3 이번 주 등락률 ≤ +10% · C4 최근 20거래일 최악의 하루 > −7%. 하나라도 어긋나면 이번 주 후보에서 빠지고 사유 코드가 `not_selected[]` 에 남습니다.
4. 통과 종목을 점수 내림차순(동률: volatility_60d 오름차순 → 종목 코드)으로 담되 같은 `sector_ko` 가 이미 2개면 건너뜁니다(`SECTOR_CAP`). 5개가 차면 끝. 5개 미만이면 있는 만큼만.
5. **상태** `확정` / `보류`(최신 자료가 30개 중 27개 미만 — 반쪽 순위를 내지 않음; 화면은 지난주 선정을 '유지 중'으로 표시) / `선정 없음`(통과 0개 — 정상 결과) / `지각`(다음 거래일 09:30 ET 이후 실행 — 동결하지 않고 기록에 공백).
6. `확정`·`선정 없음` 이면 `picks/{기준일}.json` 에 **동결 사본**을 씁니다. 이미 있는 동결 파일과 내용이 다르면 스크립트가 실패합니다. `check_frozen.py` 는 PR·푸시에서 지난 동결 파일·결과·기록이 바뀌지 않았는지 검사합니다('수정 불가'를 보장한다는 뜻이 아니라, 바꾸면 검사가 실패하고 git 이력에 남는다는 뜻입니다). Actions 가 커밋할 때 태그 `data/{기준일}` 도 붙입니다.
7. 카드 문장(`reasons_ko`·`reference_ko`·`flags[].message_ko`·`closing_ko`)은 전부 규칙 템플릿입니다. 11월에는 AI 를 호출하지 않습니다. ranks_only 면 숫자 없이 순위·방향으로 적습니다.
8. `changes{added,dropped,kept,changed_count,prev_as_of}` — 직전 동결 선정과 비교. `basket_flags[]` — 바구니 단위 표시(선정 많이 바뀜 · 시장 추세선 아래 · 단축 거래 주).

## results/{선정 기준일}.json (results/1) · track_record.json (track/1) — 지난주 복기와 기록

- 다음 주 실행이 직전 동결 선정 P(기준일 = 이번 주 `prev_end`)를 읽어, **이번 주 수집본의 `ret_1w`**(같은 시계열의 두 날짜)로 종목별 결과를 계산합니다.
- 바구니 = 선정 종목 단순 평균(동일 비중, 주중 매매 없음). 기준선 둘: 유니버스 동일비중 평균(`beat_universe`), 시장 SPY(`beat_bench`). `pct_rank_among_singles` = 단일 종목 M개 중 바구니보다 낮은 비율(%).
- 비용 차감 선 = 편도 0.3% × (매수+매도 건수) ÷ 5 — 첫 주는 매수 5건(0.3%p), 이후 주는 교체 종목당 매도 1·매수 1(changed_count × 0.12%p). 0.3% 는 바꿀 수 있는 예시 값입니다.
- 가정(화면에도 표시): 기준일(금요일) 종가 → 다음 기준일 종가. **금요일 종가는 토·일에 고른 뒤라 실제로 살 수 없는 가격입니다.** 세금·환전·호가 차이 제외, 배당은 수정 종가에 포함, USD 기준, 실제 매매가 아닌 가상 계산.
- 선정 종목 중 하나라도 기준일 자료가 없으면 결과 `보류`(다음 실행이 재계산). 다음 주 수집이 아예 없었던 선정은 `공백`으로 남기고 2주 수익률로 메우지 않습니다.
- 교훈 문장은 `rules.json` 의 우선순위(L1 → L1b → L2 → L4 → L5 → L6)대로 조건에 맞는 것을 최대 2개 고릅니다(규칙 문장, AI 아님).
- `track_record.json` 은 **추가만** 합니다. 확정된 행을 바꾸려 하면 스크립트가 실패합니다. `summary.n` = 선정이 있던 확정 주 수. 적중 비율·평균 초과 같은 요약 통계는 n ≥ 26 전에는 화면 타일에 쓰지 않습니다. **26주 미만이면 성적을 읽지 마세요. 26주가 지나도 1년 남짓한 기록으로는 규칙이 좋은지 나쁜지 판단하기 어렵습니다.**
- ranks_only 면 종목·바구니의 수익률 숫자 대신 `dir_1w`·`beat_*`·`pct_rank_among_singles` 만 공개됩니다. 나중에 full 로 바뀌어도 과거 행은 재계산하지 않습니다.

## fixtures/ — 합성 예시 데이터 (지어낸 값)

`scripts/make_fixture.py` 가 만드는 30개 가짜 종목(`S01`~`S30`, '예시 1'~'예시 30')의 완전한 자료 세트입니다. publish_mode 가 `full` 이라 숫자가 모두 보이므로 (1) 화면의 숫자 버전 예시(`index.html?data=fixtures`), (2) 손으로 다시 계산해 보는 연습, (3) CI 의 결정성 검사(`make_fixture.py --check` — 다시 만들어 바이트 단위로 같아야 함)에 씁니다. 실제 종목·가격과 무관합니다.

### 손으로 다시 계산해 보기

`fixtures/weekly.json` 에서 종목 하나를 고르고 `pct.trend`·`pct.calm`·`pct.dd` 를 읽어 0.4·0.4·0.2 를 곱해 더하면 `fixtures/picks.json` 의 `score`(또는 미선정 목록의 `score`)와 ±0.1 안에서 같아야 합니다. 백분위 자체는 `ranks` 와 `M` 으로 P = (M − rank) ÷ (M − 1) × 100 입니다. 실제 화면(ranks_only)에서도 같은 방법으로 점수를 검산할 수 있습니다 — 가격 숫자 없이도 점수는 재계산됩니다.

## 검사 스크립트와 로컬 실행

```
python3 -I scripts/check_static.py                       # 유니버스·휴장일·공급자 결정·규칙
python3 -I scripts/check_weekly.py docs/data/weekly.json # 구조·순위 범위·공개 범위(숫자 비공개)
python3 -I scripts/check_picks.py docs/data/picks.json   # 점수 재검산 ±0.1 · 조건 · 업종 상한 · 동결 사본 일치
python3 -I scripts/check_results.py                      # 결과·기록 일관성
python3 -I scripts/check_words.py docs README.md         # 금지어 검사: 추천, 목표가, 수익 보장 같은 말이 없는지 (부정문은 허용), 키 모양 문자열
python3 -I scripts/check_frozen.py --base origin/main    # 지난 선정·결과·기록 불변
python3 -I scripts/make_fixture.py --check               # 합성 데이터 결정성
```

로컬에서 전체 체인을 돌려 보려면(키는 환경 변수로만, 결과 숫자는 `.work/` 에만):

```
EOD_API_KEY=... python3 -I scripts/fetch_eod.py --provider eodhd --tickers "$(python3 -I scripts/plan_run.py | sed -n 's/^tickers=//p')" --out .work/eod
python3 -I scripts/build_weekly.py --eod .work/eod --private-out .work/weekly_full.json
python3 -I scripts/build_results.py --private .work/weekly_full.json
python3 -I scripts/build_picks.py --private .work/weekly_full.json
```

`.work/` 는 `.gitignore` 에 있어 커밋되지 않습니다.
