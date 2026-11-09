"""동결 기록 검사 — 지난 선정(picks/)·결과(results/)·기록(track_record.json)이 기준 커밋 이후 바뀌지 않았는지(추가만 허용).

사용: python3 -I scripts/check_frozen.py --base origin/main [--frozen-dir docs/data/picks] [--results-dir docs/data/results]
        [--track docs/data/track_record.json]
종료 코드: 0 통과 · 1 실패(이유를 " - " 줄로 출력) · 2 사용 오류(git 저장소 아님 · 기준 ref 없음 · 경로가 저장소 밖)
CI 에서는 actions/checkout 에 fetch-depth: 0 을 줘야 기준 ref(origin/main 등)를 읽을 수 있습니다.

규칙:
- 기준 ref 에 있는 picks/ 파일은 작업 폴더에서 바이트 단위로 같아야 함(삭제도 실패)
- 기준 ref 에 있는 results/ 파일도 같아야 함 — 단, 기준 쪽 status 가 보류·공백 이면 다시 계산돼 바뀔 수 있음(삭제는 실패)
- track_record.json 의 기준 쪽 행(pick_as_of 기준)은 그대로 있어야 함 — 기준 쪽 status 가 보류·공백 인 행만 바뀔 수 있음(행 삭제는 실패)
- 새 파일·새 행은 허용 · 기준 ref 에 폴더·파일이 없으면 모두 새것으로 봄
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fdelib import load_json  # noqa: E402

REDO_OK = {"보류", "공백"}


def git(top: str | None, *args: str) -> tuple[int, bytes]:
    cmd = ["git"] + (["-C", top] if top else []) + list(args)
    p = subprocess.run(cmd, capture_output=True)
    return p.returncode, p.stdout


def rel(path: str, top: str) -> str:
    r = os.path.relpath(os.path.abspath(path), top).replace(os.sep, "/")
    if r.startswith(".."):
        print(f"사용 오류: {path} 가 git 저장소({top}) 밖에 있습니다", file=sys.stderr)
        sys.exit(2)
    return r


def base_files(top: str, base: str, rel_dir: str) -> list[str]:
    rc, out = git(top, "ls-tree", "-r", "--name-only", base, "--", rel_dir)
    return out.decode("utf-8").split("\n") if rc == 0 and out.strip() else []


def base_blob(top: str, base: str, rel_path: str) -> bytes | None:
    rc, out = git(top, "show", f"{base}:{rel_path}")
    return out if rc == 0 else None


def base_status(blob: bytes):
    try:
        d = json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return d.get("status") if isinstance(d, dict) else None


def wt_files(top: str, rel_dir: str) -> set[str]:
    out = set()
    for root, _, files in os.walk(os.path.join(top, rel_dir)):
        for f in files:
            out.add(os.path.relpath(os.path.join(root, f), top).replace(os.sep, "/"))
    return out


def check_dir(top: str, base: str, rel_dir: str, label: str, redo_ok: bool, errors: list[str]) -> dict:
    """기준 ref 의 파일마다 작업 폴더와 바이트 비교. 결과 폴더는 기준 status 가 보류·공백 이면 변경 허용."""
    base_list = [p for p in base_files(top, base, rel_dir) if p]
    n = {"same": 0, "redo": 0, "new": len(wt_files(top, rel_dir) - set(base_list))}
    for rp in base_list:
        blob = base_blob(top, base, rp)
        wt_path = os.path.join(top, rp)
        if blob is None:
            errors.append(f"{rp}: 기준({base})에서 읽을 수 없음")
            continue
        if not os.path.isfile(wt_path):
            errors.append(f"{rp}: 기준({base})에 있던 {label} 파일이 삭제됨 — 기록은 추가만 합니다")
            continue
        with open(wt_path, "rb") as f:
            if f.read() == blob:
                n["same"] += 1
                continue
        st = base_status(blob)
        if redo_ok and st in REDO_OK:
            n["redo"] += 1
            continue
        errors.append(f"{rp}: 기준({base})과 내용이 다름(기준 status {st!r}) — 동결된 {label}은 바꾸지 않습니다")
    return n


def check_track(top: str, base: str, rel_track: str, errors: list[str]) -> dict:
    blob = base_blob(top, base, rel_track)
    wt_path = os.path.join(top, rel_track)
    wt_rows: dict = {}
    if os.path.isfile(wt_path):
        try:
            wt_rows = {r.get("pick_as_of"): r for r in (load_json(wt_path) or {}).get("weeks", []) if isinstance(r, dict)}
        except (json.JSONDecodeError, AttributeError):
            errors.append(f"{rel_track}: 작업 폴더의 JSON 을 읽을 수 없음")
    n = {"same": 0, "redo": 0, "new": len(wt_rows)}
    if blob is None:
        return n
    try:
        base_rows = {r.get("pick_as_of"): r for r in json.loads(blob.decode("utf-8")).get("weeks", []) if isinstance(r, dict)}
    except (ValueError, AttributeError, UnicodeDecodeError):
        errors.append(f"{rel_track}: 기준({base})의 JSON 을 읽을 수 없음")
        return n
    if not os.path.isfile(wt_path):
        errors.append(f"{rel_track}: 기준({base})에 있던 기록 파일이 삭제됨")
        return n
    n["new"] = len(set(wt_rows) - set(base_rows))
    for k, br in base_rows.items():
        wr = wt_rows.get(k)
        if wr is None:
            errors.append(f"{rel_track}: 기록 행 {k} 이 삭제됨 — 기록은 추가만 합니다")
        elif wr == br:
            n["same"] += 1
        elif br.get("status") in REDO_OK:
            n["redo"] += 1
        else:
            errors.append(f"{rel_track}: 기록 행 {k}(기준 status {br.get('status')!r}) 이 바뀜 — 확정 행은 바꾸지 않습니다")
    return n


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="기준 git ref (예: origin/main, HEAD~1, 커밋 해시)")
    ap.add_argument("--frozen-dir", default="docs/data/picks")
    ap.add_argument("--results-dir", default="docs/data/results")
    ap.add_argument("--track", default="docs/data/track_record.json")
    args = ap.parse_args(argv)
    rc, out = git(None, "rev-parse", "--show-toplevel")
    if rc != 0:
        print("사용 오류: 현재 폴더가 git 저장소가 아닙니다", file=sys.stderr)
        return 2
    top = out.decode("utf-8").strip()
    rc, _ = git(top, "rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}")
    if rc != 0:
        print(f"사용 오류: 기준 ref 를 찾을 수 없습니다 — {args.base} (fetch-depth: 0 인지 확인)", file=sys.stderr)
        return 2

    errors: list[str] = []
    picks = check_dir(top, args.base, rel(args.frozen_dir, top), "선정", False, errors)
    results = check_dir(top, args.base, rel(args.results_dir, top), "결과", True, errors)
    track = check_track(top, args.base, rel(args.track, top), errors)
    if errors:
        print(f"FAIL 동결 검사(기준 {args.base})")
        for e in errors:
            print(" -", e)
        return 1
    print(f"OK 동결 검사(기준 {args.base}): 선정 동결 {picks['same']}개 동일 · 새 {picks['new']}개 | "
          f"결과 {results['same']}개 동일 · 재계산 {results['redo']}개 · 새 {results['new']}개 | "
          f"기록 행 {track['same']}개 동일 · 재계산 {track['redo']}개 · 새 {track['new']}개")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
