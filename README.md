# <주제 제목> — 주경야독 2026 개인 학습 프로젝트

사이트: https://<계정>.github.io/<저장소>/
데이터: 합성(지어낸 자료) 또는 공개 출처 <URL · 이용 조건> · 실존 인물·회사 없음 · 기준일은 화면에 표시

## 무엇을 하는가
사용자 역할 · 할 수 있는 일 하나 · 하지 않는 일

## 진행 — Sprint와 Release
- Sprint 계획·회고: Issues → 라벨 `sprint`
- 일정: Issues → Milestones (Sprint 1~8 · 달력 한 주씩)
- 판(버전): Releases (E2E 선언 Release = v1.0.0)

## 처음 보는 사람이 여는 법
(회차 4·8에서 실제로 해 본 뒤 고칩니다 — 작동하지 않는 순서를 완성된 안내처럼 남기지 않습니다)
1. 이 저장소를 fork 합니다
2. Settings → Pages → Source: GitHub Actions
3. Actions → check-deploy.yml → Run workflow (데이터 첫 갱신·배포)
4. 1~2분 뒤 https://<내 계정>.github.io/<저장소>/ 를 엽니다

## 자료 한 건 바꾸고 배포하기
변경 → PR → 검사 → 병합 → Actions → Pages 확인 (실행해 본 명령·경로만 적음)

## 문제가 생기면
빈 입력·잘못된 자료 / 자동 검사·배포 실패 / 이전 판으로 돌아가기 → RUNBOOK.md (회차 7)

## 자료와 한계
모의 AI 출력/실제 호출 구분 · 미완 기능 · 이후 관리: 소유자 본인 / 코치 지원 종료 2026-12-31

## 증거 길잡이
요구 이슈(회차 1) / 대표 PR / 검사(Actions) / Sprint 이슈와 LEARNING-LOG / Release / 인계 기록(HANDOVER)

---
### 이 시작 틀을 받은 뒤 처음 5분 (다 하면 이 절은 지워도 됩니다)
먼저: 공개 화면(프로필 이름 칸 · 저장소 이름·설명 · README · 사이트 · 이슈 · 커밋)에는 실명과 회사명을 쓰지 않습니다. 프로필 Name 칸은 비우거나 별칭으로 두고, Settings → Emails에서 'Keep my email addresses private'를 켜 두면 커밋에 메일이 남지 않습니다.
1. Issues → Labels → New label: `sprint`, `customer` 두 개를 만듭니다(나머지 `enhancement`·`bug`는 기본 라벨입니다).
2. Issues → Milestones → New milestone: `Sprint 1`~`Sprint 8`(마감일 11/15 · 11/22 · 11/29 · 12/6 · 12/13 · 12/20 · 12/27 · 12/31).
3. Settings → Collaborators → Add people: 코치 2인을 초대합니다(공지에 적힌 메일 또는 GitHub 사용자 이름).
4. Issues → New issue → 「Sprint 계획·회고」로 Sprint 1 이슈를 엽니다.
이슈 양식(Issue forms)이 보이지 않으면 GitHub 화면이 바뀐 것일 수 있습니다. 빈 이슈에 양식의 칸 이름을 그대로 적어도 됩니다.
