# 3단계 — 개인 AI 인터뷰

## 목표

활성화된 그룹의 각 사용자가 서로 다른 시간에 본인에게만 보이는 AI와 대화하고, 구조화된 MemberState와 유효한 역할 1·2·3순위 제출을 비동기로 축적·복구하게 한다. 역할 배정과 투두 생성은 이 단계에서 실행하지 않는다.

## 선행 게이트

GATE B의 필수 항목·최대 턴·종료 조건·chat 상태 전환 방식과 역할 후보 생성 방식·별도 LIVE 예산·candidate revision 계약이 승인됐고, 0B-2 Timely LIVE 확인이 완료됐으며 개인 인터뷰용 3단계 LIVE 예산(최대 6회)이 허용돼 있어야 한다.

## 수행

1. `moim-chat` 2열 구조를 유지해 왼쪽 대화와 오른쪽 MemberState가 같은 서버 revision을 사용하게 하라.
2. private conversation과 MemberState를 사용자 단위의 세션과 객체 인가 경계 안에 저장하라.
3. GATE B에서 승인된 방식으로 최신 ProjectBrief revision에서 그룹 역할 후보를 서버가 한 번 생성하고 `source_project_brief_revision`과 함께 immutable candidate revision으로 저장하라. 모든 cohort 구성원에게 같은 candidate revision을 제공하고, 개인 인터뷰·새로고침·재접속마다 후보를 다시 만들지 마라. AI 생성 실패를 고정 목록이나 이전 revision으로 성공처럼 대체하지 말고 오류와 명시적 재시도를 제공하며 승인된 별도 LIVE 예산을 지켜라. ProjectBrief revision이 바뀌면 이전 candidate revision과 이를 참조한 미확정 preference를 STALE 처리하라.
4. 현재 candidate revision의 후보 ID 중 서로 다른 1·2·3순위를 구조화해 받으라. 후보에 없는 임의 문자열, 중복 선택, 누락 또는 오래된 candidate revision은 제출 완료로 저장하지 말고 기존 입력·오류 스타일로 다시 선택하게 하라. 승인된 candidate revision이 없으면 인터뷰의 순위 제출 단계로 진행하지 마라.
5. 인터뷰 계약을 충족하고 사용자가 제출을 확인하면 `source_candidate_revision`을 가진 immutable preference submission revision과 `WAITING_ASSIGNMENT` 상태를 저장하라. 다른 구성원·그룹장에게 순위·이유·MemberState를 공개하지 말고, 승인된 경우에만 `제출 완료 수/활성화 cohort 수` 집계를 보여주라. 이 단계에서는 역할 배정 operation, todo draft, 숨겨진 평가 기준을 만들지 마라.
6. 후보 생성과 메시지 요청 각각에 operation/action ID, 202 상태 조회와 새로고침 복구를 적용하라. 메시지 1회당 Timely 호출 1회 원칙을 지키고 필요한 최근 메시지·요약·구조 상태만 공급자에 보내며 전체 원문 재전송을 피하라.
7. 오류·시간 초과를 사용자에게 정확히 표시하고 재시도 버튼을 제공하라. 자동 재시도는 하지 마라. `E.coach`를 포함한 기존 모듈에는 `docs/ARCHITECTURE.md`에 기록되고 0B-1에서 재확인된 판정을 적용하며 같은 역할의 구조를 두 벌 두지 마라.

범위 밖: 서버 역할 배정, 역할·투두 draft 생성·확정·그룹 공개, 다른 팀원의 private 정보 열람, 원본 HTML 변경.

## 검증

- 교차 사용자·교차 그룹·추측 ID로 대화/MemberState 접근이 불가능함을 결정적 테스트로 확인하라.
- 중복 전송, revision 충돌, 작업 중 서버 재시작 뒤 실패 표시와 재시도, 새로고침을 검증하라.
- 후보 밖 문자열·중복·누락·stale 후보 revision을 거부하고, 유효한 세 순위만 한 번 제출되며 제출 후에는 `WAITING_ASSIGNMENT`가 새로고침 뒤 복구되는지 검증하라.
- 동일 ProjectBrief revision의 중복 요청·새로고침·재접속이 같은 candidate revision을 반환하고 재호출하지 않는지, 모든 cohort 구성원이 같은 후보를 보는지 검증하라.
- 역할 후보 생성 실패·시간 초과·명시적 재시도·별도 LIVE 예산과 ProjectBrief 변경 뒤 candidate/preference STALE 전파를 검증하라.
- cohort 전원 제출 전후 모두 이 단계의 DB·응답·템플릿에 assignment, todo draft 또는 숨겨진 평가 기준이 생기지 않는지 확인하라.
- private 원문이 로그·팀 응답·템플릿 소스에 없는지 검사하라.
- 승인 예산 안에서 Timely LIVE 스키마/latency/usage를 기록하고 원본 HTML 해시를 검사하고 1440×900 스크린샷 캡처를 실행해 사용자가 원본과 나란히 확인할 수 있게 하라.

## 중단 조건

GATE B 미승인, LIVE 권한/예산 부족, 개인정보 범위 확대 또는 제품 차이가 필요하면 중단하고 BLOCKED로 보고한다. 범위 밖 기능은 시작하지 않는다.

## 완료 보고

비동기 인터뷰→유효한 역할 순위 제출→WAITING_ASSIGNMENT 흐름, 격리/복구/결정적/LIVE/디자인 검증, 호출 수, 다음 한 단계(4단계)를 보고하라.
