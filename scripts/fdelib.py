"""공통 도우미 — build_*.py · check_*.py 가 함께 씁니다.

날짜·휴장일·시간대(ET→KST), JSON 읽고 쓰기, 순위·백분위, 공개 범위(publish_mode) 필터.
표준 라이브러리만 씁니다. 실행은 각 스크립트가 `python3 -I scripts/xxx.py` 로 합니다.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
UTC = dt.timezone.utc

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

# publish_mode 가 ranks_only 일 때 공개 파일에서 빼는 칸(가격·수익률·변동성·거래량 배수 등 숫자)
PRICE_FIELDS = {
    "last_close", "prev_close", "ret_1w", "rel_1w", "ret_4w", "mom_6_1", "bench_mom_6_1",
    "volatility_20d", "volatility_60d", "max_drawdown_12w", "high_52w", "low_52w",
    "pct_from_52w_high", "vol_ratio", "vol_ratio_20_60", "universe_avg_ret_1w", "max_abs_daily_ret_61d",
    "ret", "bench_ret", "vol_ratio_day",
    "ret_pct", "vs_universe_pp", "vs_bench_pp", "entry_adj_close", "exit_adj_close",
    "net_ret_pct", "cost_pct", "universe_avg_ret_pct", "bench_ret_pct",
    "basket_ret", "basket_net_ret", "universe_avg_ret", "bench_ret_1w", "idx", "cum", "values",
}


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


_MISSING = object()


def load_json(path: str, default=_MISSING):
    """파일이 없으면 default 를 돌려주고(default 를 안 주면 오류), 있으면 JSON 을 읽습니다."""
    if not os.path.exists(path):
        if default is not _MISSING:
            return default
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")


def parse_now(s: str | None) -> dt.datetime:
    """--now 'YYYY-MM-DDTHH:MM:SSZ' 또는 없음(현재). 항상 UTC aware."""
    if not s:
        return dt.datetime.now(UTC)
    s = s.strip().replace("Z", "+00:00")
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


def run_info() -> dict | None:
    """GitHub Actions 안이면 실행 id·주소·커밋을 돌려줍니다(증거용). 로컬이면 None."""
    rid = os.environ.get("GITHUB_RUN_ID")
    if not rid:
        return None
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return {"id": rid, "url": f"{server}/{repo}/actions/runs/{rid}", "sha": os.environ.get("GITHUB_SHA")}


class Calendar:
    """NYSE 휴장일·단축일 달력(docs/data/nyse_holidays.json)."""

    def __init__(self, data: dict):
        self.closed = {dt.date.fromisoformat(x) for x in data.get("closed", [])}
        self.half_days = {dt.date.fromisoformat(x) for x in data.get("half_days", [])}
        self.coverage_until = dt.date.fromisoformat(data["coverage_until"]) if data.get("coverage_until") else None
        self.close_time = data.get("close_time_et", "16:00")
        self.half_close_time = data.get("half_day_close_time_et", "13:00")

    def is_trading_day(self, d: dt.date) -> bool:
        return d.weekday() < 5 and d not in self.closed

    def close_time_of(self, d: dt.date) -> str:
        return self.half_close_time if d in self.half_days else self.close_time

    def last_trading_day_on_or_before(self, d: dt.date) -> dt.date:
        while not self.is_trading_day(d):
            d -= dt.timedelta(days=1)
        return d

    def next_trading_day_after(self, d: dt.date) -> dt.date:
        d += dt.timedelta(days=1)
        while not self.is_trading_day(d):
            d += dt.timedelta(days=1)
        return d

    @staticmethod
    def monday_of(d: dt.date) -> dt.date:
        return d - dt.timedelta(days=d.weekday())

    def week_last_trading_day(self, monday: dt.date) -> dt.date | None:
        for i in range(4, -1, -1):
            d = monday + dt.timedelta(days=i)
            if self.is_trading_day(d):
                return d
        return None

    def trading_days_in(self, start: dt.date, end: dt.date) -> int:
        n, d = 0, start
        while d <= end:
            n += self.is_trading_day(d)
            d += dt.timedelta(days=1)
        return n

    def expected_as_of(self, now_utc: dt.datetime) -> dt.date:
        """가장 최근에 '끝난' 거래 주의 마지막 거래일.

        지금(ET)이 속한 월~금 주의 마지막 거래일 L 의 마감 시각이 지났으면 L, 아니면 직전 주의 마지막 거래일.
        주중에 수동 실행해도 수요일 기준일이 생기지 않습니다.
        """
        now_et = now_utc.astimezone(ET)
        monday = self.monday_of(now_et.date())
        for back in range(0, 6):
            m = monday - dt.timedelta(days=7 * back)
            last = self.week_last_trading_day(m)
            if last is None:
                continue
            hh, mm = map(int, self.close_time_of(last).split(":"))
            close_dt = dt.datetime(last.year, last.month, last.day, hh, mm, tzinfo=ET)
            if now_et >= close_dt:
                return last
        raise RuntimeError("기준일을 정할 수 없습니다 — 휴장일 파일을 확인하세요")

    def week_bounds(self, as_of: dt.date) -> dict:
        monday = self.monday_of(as_of)
        prev_end = self.last_trading_day_on_or_before(monday - dt.timedelta(days=1))
        days = self.trading_days_in(monday, as_of)
        half = any((monday + dt.timedelta(days=i)) in self.half_days for i in range(5))
        return {"start": monday.isoformat(), "end": as_of.isoformat(), "prev_end": prev_end.isoformat(),
                "trading_days": days, "half_day": half or as_of in self.half_days, "short": days < 5 or half}

    def stamps(self, as_of: dt.date) -> dict:
        hh, mm = map(int, self.close_time_of(as_of).split(":"))
        et = dt.datetime(as_of.year, as_of.month, as_of.day, hh, mm, tzinfo=ET)
        kst = et.astimezone(KST)
        return {"date": as_of.isoformat(), "et": et.strftime("%Y-%m-%d %H:%M"), "kst": kst.strftime("%Y-%m-%d %H:%M"),
                "half_day": as_of in self.half_days}

    def is_late(self, as_of: dt.date, now_utc: dt.datetime) -> bool:
        """다음 거래일 09:30 ET 이후면 '지각' — 보유 주가 시작된 뒤의 선정은 '미리 골랐다'고 할 수 없음."""
        nxt = self.next_trading_day_after(as_of)
        open_dt = dt.datetime(nxt.year, nxt.month, nxt.day, 9, 30, tzinfo=ET)
        return now_utc.astimezone(ET) >= open_dt


def avg_ranks(values: dict, higher_better: bool) -> dict:
    """값 → 순위(1 = 가장 좋은 쪽). 동률은 평균 순위."""
    items = [(t, v) for t, v in values.items() if is_num(v)]
    items.sort(key=lambda x: (-x[1] if higher_better else x[1], x[0]))
    ranks: dict = {}
    i = 0
    while i < len(items):
        j = i
        while j + 1 < len(items) and items[j + 1][1] == items[i][1]:
            j += 1
        r = (i + 1 + j + 1) / 2
        for k in range(i, j + 1):
            ranks[items[k][0]] = r if r != int(r) else int(r)
        i = j + 1
    return ranks


def pct_from_rank(rank, m: int):
    if not is_num(rank) or m < 1:
        return None
    if m == 1:
        return 100.0
    return round((m - rank) / (m - 1) * 100, 1)


def strip_private(obj, mode: str):
    """publish_mode 가 ranks_only 면 가격 숫자 칸을 재귀적으로 뺍니다. full 이면 그대로."""
    if mode == "full":
        return obj
    if isinstance(obj, dict):
        return {k: strip_private(v, mode) for k, v in obj.items() if k not in PRICE_FIELDS}
    if isinstance(obj, list):
        return [strip_private(x, mode) for x in obj]
    return obj


def fmt_signed(x, digits: int = 1) -> str:
    return f"{x:+.{digits}f}"
