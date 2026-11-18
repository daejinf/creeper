# 4단계 — 역할·개인 투두 초안과 확정

## 목표

활성화 cohort 전원의 유효한 역할 순위를 먼저 모아 서버에서 역할 충돌을 해결하고, 고정된 assignment revision 뒤에만 ProjectBrief와 본인의 private MemberState를 이용해 겹치지 않는 개인 투두를 만든다. 사용자가 수정·확정한 최소 정보만 그룹 구성원이 조회할 수 있게 공유한다. 별도 개인 목표 단계·엔터티·화면은 만들지 않는다. 평가용 내부 데이터는 보호하며 그룹 화면 표시는 5단계에서 연결한다.

## 선행 게이트

3단계가 통과했고 GATE B의 그룹별 역할 후보 방식, 미제출·신규 합류·재배정 규칙(배정 시점은 D-A5의 cohort 전원 제출 뒤로 확정), 배정 안내 노출 범위, 초안 사용 필드, 사용자 확정, 그룹 공개 범위와 ProjectBrief 변경 시 재검토 규칙이 승인됐으며, Timely LIVE 호출은 D-A8(고정 호출 상한 없음, 모든 호출 기록, 단일 작업 100회 이상 대량 호출만 사전 승인)을 따른다.

## 수행

1. 원본 `moim-chat`의 대화, 역할 안내, 투두 수정·직접 추가·확정 흐름을 templates/static으로 구현해 실제 서버 상태와 연결하라.
2. private conversation/MemberState/preference submission, assignment revision, todo draft와 사용자가 확정한 assignment를 별도 저장·권한 경계로 모델링하라. MemberState, 배정 역할과 확정 투두가 구성원의 실행 목표이며 별도 개인 목표 record를 만들지 마라.
3. 역할 후보는 GATE B에서 승인된 방식으로 그룹의 ProjectBrief를 근거로 정한 그룹별 목록을 사용하라. 원본 데모의 PPT·디자인·기획 등 고정 목록을 하드코딩하지 마라. 활성화 cohort 전원의 유효한 preference submission revision이 준비되지 않았으면 `WAITING_ASSIGNMENT`를 유지하고 역할·투두 draft를 만들지 마라.
4. cohort 전원이 준비되면 서버 배정 서비스만 순위 원문을 읽어 1·2·3순위별로 배정하라. 같은 순위에서 같은 역할이 충돌하면 서버 무작위로 한 명을 정하고 나머지는 다음 순위로 이동시킨다. 3순위까지 배정되지 않은 구성원은 남은 역할 중 서버 무작위로 배정하고, 남은 역할이 없을 때는 GATE B에서 승인된 중복 규칙을 적용하라. 순위 원문은 다른 구성원·그룹장에게 공개하지 마라.
5. 무작위 결과는 AI가 고르지 않게 하고 source cohort와 preference submission revision 집합을 가진 하나의 assignment revision 안에서 한 번만 생성·감사 가능하게 저장하라. 중복 요청·새로고침·재시도로 재추첨하지 마라. 결정적 테스트에서는 주입 가능한 고정 RNG를 사용하되 운영 결과를 조작 가능한 고정 seed로 만들지 마라.
6. 채팅의 배정 안내 문구는 GATE B에서 승인된 노출 범위를 따르고 배정 성공 뒤 모든 cohort 구성원의 서버 상태를 `ROLE_TODOS`로 전환하라. 접속 중 사용자는 상태 조회로 전환하고 미접속 사용자는 재접속 때 같은 assignment revision을 보게 하라. 승인 없이 원본 데모처럼 다른 구성원의 이름과 순위를 보여주지 마라.
7. 최종 assignment revision과 ProjectBrief를 기준으로 팀 전체 deliverable coverage를 먼저 검사한 뒤, 각 구성원의 private MemberState와 배정 역할을 이용해 개인 투두·마감일 초안을 만들라. 중복 역할이 있으면 구성원별 결과물을 명시적으로 분할하고, 서로 다른 역할 사이에도 같은 결과물·책임이 중복되거나 필수 결과물이 빠지지 않게 검증하라. 별도 사용자용 그룹 계획 엔터티나 화면은 만들지 마라.
8. 모든 todo draft와 숨겨진 평가 기준은 `source_assignment_revision`을 저장해야 한다. 현재 assignment revision과 다르면 표시·수정·확정하지 못하게 STALE 처리하라. 사용자의 명시적 확정 전에는 어떤 역할·투두 draft도 그룹 화면에 공개하지 마라.
9. AI가 각 투두와 함께 내부 `deliverable`, 하나 이상의 `completion_criteria`와 허용할 첨부·증거 유형을 생성해 서버에 저장하라. 이 기준을 검토·편집하는 별도 UI를 만들거나 투두 패널에 노출하지 마라. 사용자가 `투두 확정하기`를 누르면 보이는 투두와 숨겨진 기준을 같은 불변 revision으로 고정하고, 이후 M2가 평가 중에 기준을 바꾸지 못하게 하라.
10. 자연어 수정은 투두 제목·마감일·허용된 범위의 투두 추가·삭제만 구조화된 변경안으로 변환하고 현재 사용자 담당·프로젝트 기간·업무량·assignment revision을 검증한 뒤 같은 서버 revision에 저장하라. 사용자가 배정 역할이나 담당자를 직접 바꾸지 못하게 하라. 역할 변경은 GATE B에서 승인된 서버 재배정 operation으로만 수행하고 새 assignment revision을 생성하며 관련 todo draft/confirmed assignment와 숨겨진 평가 기준을 함께 STALE 처리하라.
11. ProjectBrief, cohort, preference submission 또는 assignment revision이 바뀌면 GATE B에서 승인된 규칙에 따라 관련 todo draft/confirmed assignment와 숨겨진 평가 기준을 함께 STALE 또는 새 revision으로 처리하라.
12. 투두 패널의 "우리 팀의 최종 목표" 박스에는 2단계에서 최신 ProjectBrief revision으로 생성된 팀 목표 표시문과 프로젝트 기간을 별도 라벨 없이 읽기 전용으로 표시하라. 이 단계에서 별도 팀 목표를 다시 생성하지 마라.
13. 확정된 역할·투두는 그룹 구성원이 조회할 수 있는 서버 API와 직렬화까지 구현하되 private MemberState·조건·역할 순위와 숨겨진 평가 기준은 공개하지 마라. 그룹 화면의 투두·진행률 영역 렌더링은 5단계에서 구현한다.
14. 기존 모듈 후보 `E.draft`, `E.plan`, `update_member_condition`, `F`, `F.revise`를 검토하되 MOIM의 권한·revision·확정 계약을 우회하지 마라.

범위 밖: 별도 개인 목표 단계·엔터티·화면, 평가 기준 검토 UI, 별도 TeamGoal 합성·합의 화면, 2단계와 별개인 팀 목표 생성, 그룹 화면의 투두·진행률 렌더링, 그룹 계획 생성, 다른 팀원의 private 정보 열람, 순위 원칙을 건너뛴 AI·클라이언트 임의 배정, 사용자·브라우저의 배정 역할 또는 담당자 직접 수정, 외부 캘린더 연동, 원본 HTML 변경.

## 검증

- 확정 전 private 상태·역할 순위 원문·draft가 다른 팀원·그룹장·그룹 화면에 나타나지 않음을 결정적 테스트로 검증하라.
- 1·2·3순위 반영, 같은 순위 충돌, 다음 순위 이동, 3순위 소진 뒤 남은 역할 무작위 배정, 남은 역할 없음, 미제출·신규 합류와 승인된 재배정 규칙을 검증하라.
- cohort 전원 제출 전에는 역할 배정·todo draft·숨겨진 평가 기준이 전혀 생기지 않고, 전원 제출 직후 하나의 assignment revision만 생성되는지 검증하라.
- 같은 입력과 배정 revision의 중복 요청·새로고침·재시도가 같은 결과를 반환하고 재추첨하지 않는지 검증하라.
- 각 투두가 MemberState·최종 배정 역할·source assignment revision에서 추적되고 비어 있지 않은 결과물·완료 기준·허용 증거를 같은 투두 revision의 숨겨진 snapshot으로 갖는지 검증하라.
- 중복 역할 fixture에서 결과물과 투두가 구성원별로 분할되고, 팀 전체 필수 결과물에 중복·누락이 없는지 검증하라.
- 사용자 응답·템플릿·투두 패널에 숨겨진 평가 기준이나 별도 개인 목표 UI가 나타나지 않는지 확인하라.
- 투두 수정·직접 추가·삭제, 기간 이탈, 과도한 업무량, 교차 사용자 수정, 배정 역할·담당자 직접 변경 차단, 승인된 재배정 operation의 새 assignment revision과 STALE 전파, revision 충돌, 중복 확정과 저장 실패를 검증하라.
- 확정 성공 뒤 다른 팀원·그룹장의 그룹 조회 API 응답에 필요한 역할·투두만 나타나고, private 원문·조건은 직렬화·로그·화면에 없는지 확인하라.
- 목표 박스가 최신 ProjectBrief revision의 팀 목표 표시문과 기간을 표시하고, 이 단계의 AI 응답으로 바뀌지 않는지 확인하라.
- Timely LIVE는 승인 예산 안에서 스키마/완전성/usage/latency를 기록하라.
- 원본 HTML 해시를 검사하고 1440×900 스크린샷 캡처를 실행해 사용자가 원본과 나란히 확인할 수 있게 하라.

## 중단 조건

그룹 공개 범위가 불명확하거나 개인정보 공개를 넓혀야 하거나 원본에 없는 별도 합의 화면이 필요하면 사용자 승인 전 중단하고 BLOCKED로 보고한다. 범위 밖 기능은 시작하지 않는다.

## 완료 보고

cohort 전원 역할 순위 제출→서버 assignment revision→팀 전체 coverage 검사→개인 투두와 숨겨진 평가 기준 흐름, private/confirmed 경계, 결정적/LIVE/디자인 검증, 호출 수, 다음 한 단계(`GATE-D.md`의 vision 사전 확인과 추천안 작성)를 보고하라.
