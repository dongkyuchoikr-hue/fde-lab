"""금지어·비밀값 모양 검사 — 공개 화면·데이터·README 에 투자 권유·마케팅성 표현과 키 모양 문자열이 없는지 (check-deploy.yml 이 배포 전에 실행).

사용: python3 -I scripts/check_words.py [경로 ...]    (파일 또는 폴더 · 기본: docs/index.html docs/data README.md)
종료 코드: 0 통과 · 1 금지어 또는 비밀값 모양 발견(" - 파일:줄번호: 「용어」 … 문장 앞부분") · 2 사용 오류(경로 없음)

검사 항목:
- 금지어 검사 대상: .html · .md 는 줄 단위 본문(HTML 엔티티 풀어서), .json 은 문자열 값만(키 제외 · 재귀). .py 는 보지 않습니다.
- 금지어: 추천 권유 목표가 최고 수익 최고의 종목 지금 사 사야 할 사세요 매수 신호 매도 신호 매수 타이밍 수익 보장 확실한 수익 오를 종목
  급등주 대박 필승 무조건 보장합니다
- 허용 규칙: 본문을 。.!?줄바꿈 으로 문장으로 나누고, 문장을 다시 '·' 와 ' — ' 로 조각으로 나눕니다.
  · 메타 표지(금지 금지어 검사 제외 ≠)가 같은 문장 어디에든 있으면 그 문장 전체 허용 — 용어를 '언급'하는 문장(금지어 목록 등)
  · 부정 표지(않 아닙니다 아니 아닌 아님 없습니다 없 하지 않 말 것)는 같은 조각 또는 같은 문장의 '뒤' 조각에 있으면 허용 —
    한국어는 부정이 문장 끝에 오므로 "종목 추천·수익 예측·매매 신호는 하지 않는다" 는 허용, "추천하지 않습니다 · 이번 주 추천 종목" 은 둘째 조각이 걸림
- 비밀값 모양(경로 아래 모든 텍스트 파일): (api_token|apikey|api_key|token)=12자 이상 · sk-20자 이상 · AKIA+16자 · ghp_+36자 — 값은 출력하지 않습니다.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fdelib import DATE_RE  # noqa: E402,F401  (다른 check_*.py 와 같은 import 경로를 확인하는 용도)

DEFAULT_PATHS = ["docs/index.html", "docs/data", "README.md"]
WORD_EXTS = {".html", ".md", ".json"}
TERMS = ["추천", "권유", "목표가", "최고 수익", "최고의 종목", "지금 사", "사야 할", "사세요", "매수 신호", "매도 신호", "매수 타이밍",
         "수익 보장", "확실한 수익", "오를 종목", "급등주", "대박", "필승", "무조건", "보장합니다"]
NEGATIONS = ["않", "아닙니다", "아니", "아닌", "아님", "없습니다", "없", "하지 않", "말 것"]
META = ["금지", "금지어", "검사", "제외", "≠"]
SENT_RE = re.compile(r"[。.!?\n]")
FRAG_RE = re.compile(r"·| — ")
JSON_STR_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
SECRET_RES = [("api_token=…", re.compile(r"(api_token|apikey|api_key|token)=[A-Za-z0-9._\-]{12,}", re.I)),
              ("sk-…", re.compile(r"sk-[A-Za-z0-9]{20,}")),
              ("AKIA…", re.compile(r"AKIA[0-9A-Z]{16}")),
              ("ghp_…", re.compile(r"ghp_[A-Za-z0-9]{36}"))]


def bad_terms(text: str) -> list[tuple[str, str]]:
    """허용 표지 없는 금지어의 (용어, 조각) 목록 — 규칙은 모듈 설명 참고."""
    hits = []
    for sent in SENT_RE.split(text):
        if any(m in sent for m in META):
            continue
        frags = FRAG_RE.split(sent)
        for i, frag in enumerate(frags):
            if any(n in f for f in frags[i:] for n in NEGATIONS):
                continue
            hits += [(term, frag.strip()) for term in TERMS if term in frag]
    return hits


def json_values(lines: list[str]):
    """(줄번호, 문자열 값) — 키("…":)는 뺍니다. 값으로 못 읽는 조각은 건너뜁니다."""
    for no, line in enumerate(lines, 1):
        for m in JSON_STR_RE.finditer(line):
            if line[m.end():].lstrip().startswith(":"):
                continue
            try:
                v = json.loads(m.group(0))
            except ValueError:
                continue
            if isinstance(v, str):
                yield no, v


def collect(paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                out += [os.path.join(root, f) for f in sorted(files)]
        elif os.path.isfile(p):
            out.append(p)
        else:
            print(f"사용 오류: 경로가 없습니다 — {p}", file=sys.stderr)
            sys.exit(2)
    return out


def scan_file(path: str, errors: list[str]) -> bool:
    """비밀값 모양은 모든 텍스트 파일에서, 금지어는 .html/.md/.json 에서 찾습니다. 금지어 검사를 했으면 True."""
    with open(path, "rb") as f:
        raw = f.read()
    if b"\0" in raw[:8192]:
        return False
    lines = raw.decode("utf-8", errors="replace").split("\n")
    for no, line in enumerate(lines, 1):
        for name, rx in SECRET_RES:
            if rx.search(line):
                errors.append(f"{path}:{no}: 비밀값 모양 문자열({name}) — 값은 출력하지 않음")
    ext = os.path.splitext(path)[1].lower()
    if ext not in WORD_EXTS:
        return False
    if ext == ".json":
        try:
            json.loads("\n".join(lines))
        except ValueError as e:
            errors.append(f"{path}: JSON 을 읽을 수 없음({getattr(e, 'msg', e)} 줄 {getattr(e, 'lineno', '?')})")
            return True
        items = json_values(lines)
    else:
        items = ((no, html.unescape(line)) for no, line in enumerate(lines, 1))
    for no, s in items:
        for term, frag in bad_terms(s):
            errors.append(f"{path}:{no}: 「{term}」 … {frag[:40]}")
    return True


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    files = collect(argv or DEFAULT_PATHS)
    errors: list[str] = []
    scanned = sum(1 for p in files if scan_file(p, errors))
    if errors:
        print("FAIL 금지어·비밀값 검사")
        for e in errors:
            print(" -", e)
        return 1
    print(f"OK 금지어·비밀값 검사: 파일 {len(files)}개(금지어 대상 {scanned}개) · 금지어 0 · 비밀값 모양 0")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
