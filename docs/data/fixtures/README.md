# fixtures — 합성 예시 데이터 (지어낸 값)

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
