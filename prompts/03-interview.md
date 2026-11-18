# 3단계 — 공용 역할 후보와 비공개 역할 순위 제출

D-B3(2026-11-19)가 이 단계를 바꿨다. 3단계에는 개인 AI 인터뷰가 없다. 파일 이름(`03-interview.md`)은 단계 순서를 가리키는 식별자라 그대로 둔다. 배정 뒤 인터뷰는 4단계에서 한다.

## 목표

활성화된 그룹의 각 사용자가 서로 다른 시간에 같은 공용 역할 후보를 보고 서로 다른 1·2·3순위를 비공개로 제출하게 하고, 제출 뒤 `WAITING_ASSIGNMENT`와 `N/M명 제출 완료`를 저장·복구한다. 개인 AI 인터뷰, MemberState, 역할 배정과 투두 생성은 이 단계에서 하지 않는다.

## 선행 게이트

GATE B(D-B1)와 D-B3이 승인됐고, 0B-2 Timely LIVE 확인이 완료됐으며 LIVE 호출은 D-A8(고정 호출 상한 없음, 모든 호출 기록, 단일 작업 100회 이상 대량 호출만 사전 승인)을 따른다.

## 수행

1. `moim-chat` 2열 구조를 유지해 왼쪽은 역할 후보 안내와 선택, 오른쪽은 내 순위와 제출 현황을 보여주고 둘이 같은 서버 상태를 쓰게 하라. 기존 패널·칩·간격·타이포그래피를 재사용한다.
2. 최신 ProjectBrief revision에서 그룹 역할 후보를 서버가 한 번 생성하고 `source_project_brief_revision`과 함께 immutable candidate revision으로 저장하라. 입력은 ProjectBrief뿐이다. 모든 cohort 구성원에게 같은 revision을 제공하고 새로고침·재접속·구성원마다 다시 만들지 마라. 생성 실패는 오류와 명시적 재시도로 보여주고 고정 목록·이전 revision으로 대체하지 말며 모든 LIVE 호출을 기록하라. ProjectBrief revision이 바뀌면 이전 후보와 미확정 순위를 STALE 처리하라.
3. 개인 AI 인터뷰를 순위 제출의 선행 조건에서 제거하라. MemberState·인터뷰 턴·인터뷰 종료 버튼 없이 순위를 제출할 수 있어야 하며 3단계 경로는 `E.coach`를 호출하지 않는다. `E.coach` 어댑터와 인터뷰 저장 구조는 4단계 재사용을 위해 삭제하지 않고 사용 중단 상태로 보존하라.
4. 현재 candidate revision의 후보 ID 중 서로 다른 1·2·3순위를 구조화해 받으라. 후보 밖 ID, 중복 순위, 누락, 오래된 candidate revision은 서버에서 거부하고 저장하지 마라. 제출 전에는 자유롭게 바꿀 수 있고 제출은 `source_candidate_revision`을 가진 immutable preference submission revision이다. 같은 action ID의 중복·동시 제출은 한 번만 저장한다.
5. 제출 뒤 `WAITING_ASSIGNMENT`를 저장하고 모든 구성원에게 `제출 완료 수/활성화 cohort 수`만 보여주라. 순위 원문과 이유는 본인과 서버 배정 서비스 외에는 공개하지 마라. cohort 전원 제출 전에는 역할 배정, MemberState, todo draft, 숨겨진 평가 기준을 만들지 마라.
6. 후보 생성과 제출 각각에 operation/action ID, 상태 조회와 새로고침·서버 재시작 복구를 적용하라. 오류·시간 초과를 정확히 표시하고 재시도 버튼을 제공하되 자동 재시도는 하지 마라.

범위 밖: 개인 AI 인터뷰(배정 뒤 인터뷰는 4단계), 서버 역할 배정, 역할·투두 draft 생성·확정·그룹 공개, 다른 팀원의 private 정보 열람, 원본 HTML 변경.

## 검증

- 개인 인터뷰 없이 순위를 제출할 수 있고 `E.coach`가 호출되지 않으며 제출 전 MemberState가 없어도 됨을 결정적 테스트로 확인하라.
- 교차 사용자·교차 그룹·추측 ID로 순위·화면 접근이 불가능함을 확인하라.
- 후보 밖 ID·중복·누락·stale 후보 revision을 거부하고, 유효한 세 순위만 한 번 제출되며 중복·동시 제출이 멱등이고 `WAITING_ASSIGNMENT`가 새로고침·서버 재시작 뒤 복구되는지 검증하라.
- 동일 ProjectBrief revision의 중복 요청·새로고침·재접속이 같은 candidate revision을 반환하고 재호출하지 않는지, 모든 cohort 구성원이 같은 후보를 보는지 검증하라.
- 후보 생성 실패·시간 초과·명시적 재시도·LIVE 호출 기록과 ProjectBrief 변경 뒤 candidate/preference STALE 전파를 검증하라.
- cohort 전원 제출 전후 모두 이 단계의 DB·응답·템플릿에 assignment, MemberState, todo draft 또는 숨겨진 평가 기준이 생기지 않는지 확인하라.
- 순위 원문이 다른 사용자의 응답·로그·화면·템플릿 소스에 없는지 검사하라.
- 후보 생성 Timely LIVE 스키마/latency/usage를 기록하고 원본 HTML 해시를 검사하고 1440×900 스크린샷 캡처를 실행해 사용자가 원본과 나란히 확인할 수 있게 하라.

## 중단 조건

GATE B·D-B3 미승인, LIVE 권한/예산 부족, 개인정보 범위 확대 또는 제품 차이가 필요하면 중단하고 BLOCKED로 보고한다. 범위 밖 기능은 시작하지 않는다.

## 완료 보고

공용 후보→유효한 순위 제출→WAITING_ASSIGNMENT 흐름, 격리/복구/결정적/LIVE/디자인 검증, 호출 수, 다음 한 단계(4단계)를 보고하라.
