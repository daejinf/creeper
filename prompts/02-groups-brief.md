# 2단계 — 대시보드·그룹·ProjectBrief

## 목표

생성자가 초대방을 먼저 열어 팀원을 모으고, ProjectBrief 제출로 그룹을 활성화하며, 초대받은 사용자가 활성화 전후의 정확한 상태를 보게 한다. 활성화된 그룹과 ProjectBrief는 권한 안에서 버전 관리하고 대시보드에서 실제 상태를 조회한다.

## 선행 게이트

1단계 인증과 0B-2 결정적 검증이 통과했고 초대코드·그룹장·ProjectBrief 수정 정책이 활성 문서에 기록돼 있어야 한다. 팀 목표 표시문 LIVE 검증에는 Timely 연결과 2단계 최대 3회 예산이 필요하다. LIVE가 아직 막혀 있어도 그룹·ProjectBrief 구현은 진행하되 표시문 상태를 `LIVE BLOCKED`로 정확히 남기고 성공 결과로 간주하지 않는다.

## 수행

1. 원본 dashboard/group-create/invite 화면과 group 화면 중 그룹 생성 직후의 기본 화면·ProjectBrief 표시 영역까지만 templates/static으로 구현해 실제 DB 데이터와 연결하라. dashboard와 group 구현본의 `새 그룹 만들기`는 `moim-group-create`로 직접 이동시키지 말고 생성 의도가 확인되는 POST를 거쳐 생성자용 `moim-invite` 흐름으로 연결하라. 루트 원본 HTML의 링크와 데모 동작은 변경하지도 서버 구현본에 그대로 이식하지도 마라. 별도 목표 입력 칸은 만들지 마라. ProjectBrief `프로젝트 내용`을 근거로 Timely가 짧은 한 문장 팀 목표 표시문을 생성하게 하고 source ProjectBrief revision과 함께 저장해 group 머리의 `팀 목표`에 별도 라벨 없이 그대로 표시하라. ProjectBrief 원문을 목표 엔터티로 복제하지 마라. 실행 진행률·투두·활동 영역은 5단계까지 만들지 마라.
2. 새 그룹 생성 POST에서만 `FORMING` 그룹, 그룹장 멤버십과 6자리 초대코드를 만들거나 해당 사용자가 소유한 활성 `FORMING` 초대방 하나를 재사용하라. 일반 `moim-invite` 조회나 초대코드 참여 화면 진입은 그룹을 생성하거나 코드를 재발급하지 않아야 한다. 초대받은 사용자는 POST로 코드를 제출해 같은 `FORMING` 그룹에 참여하며, 활성화 전에는 대기 상태만 보고 ProjectBrief 기반 chat이나 그룹 실행 화면에 접근하지 못하게 하라. 다른 그룹 참여 시 본인만 있는 기존 생성자용 `FORMING` 초대방은 `CANCELLED`로 전환할 수 있지만 참여자가 있는 초대방은 자동 폐기하지 말고 충돌을 안내해 명시적 정리를 요구하라.
3. 생성자를 포함해 현재 구성원이 최소 2명일 때만 `moim-invite`의 `그룹 생성하기`를 그룹장이 사용할 수 있게 하고, 한 명일 때는 `팀원 한 명 이상이 참여하면 그룹을 만들 수 있어요`를 표시하라. 이 버튼은 그룹을 활성화하거나 원본의 `그룹 생성 완료` 팝업을 띄우지 않고 기존 `FORMING` 상태를 유지한 채 `moim-group-create`로만 이동시킨다. `moim-group-create`의 다음 버튼에서 ProjectBrief revision 1 저장, 활성화 cohort snapshot 생성과 `FORMING → ACTIVE` 전환을 하나의 트랜잭션으로 처리하고, 성공 뒤 생성자와 참여 구성원이 각자의 `moim-chat`에 진입할 수 있게 하라. `moim-group-create-position.html` 이동 동작은 구현본에 이식하지 말고 별도 포지션 등록 또는 AI 인터뷰 시작 화면을 만들지 마라.
4. 그룹, `FORMING`·`CANCELLED`·`ACTIVE` 상태, 멤버십, 활성화 cohort revision, ProjectBrief revision, 서버 생성 초대코드와 만료/재발급을 구현하라. `새로 만들기`는 같은 `FORMING` 그룹의 기존 코드를 폐기하고 새 코드만 발급하며 새 그룹을 만들지 않는다. `ACTIVE` 전환과 함께 기존 초대코드를 폐기하고, GATE B 승인 전 활성화된 그룹의 코드 참여는 `GROUP_NOT_JOINABLE`로 거부해 `이미 시작된 그룹이에요. 그룹장에게 새 초대 가능 여부를 확인해 주세요`라고 안내하라. 활성화 뒤 신규 합류와 cohort 편입 자체는 GATE B 승인 전 구현하지 마라.
5. 모든 조회·수정·참여·활성화에 객체 단위 인가와 그룹 경계를 적용하라. 그룹장만 ProjectBrief revision 1로 활성화할 수 있고, 동시 활성화·동시 수정은 revision 충돌과 멱등성으로 처리해 조용히 덮어쓰거나 cohort를 두 번 만들지 마라.
6. 정상·빈 상태·로딩·오류·만료 초대·활성화 대기·이미 활성화된 상태를 기존 디자인 언어로 표현하라. ProjectBrief가 비었거나 목표 표시문 생성이 실패하면 성공 폴백을 만들지 말고 빈 상태 또는 오류·재시도를 표시하라. ProjectBrief revision 변경 시 이전 표시문을 현재 목표처럼 보여주지 마라.

범위 밖: AI 인터뷰, 역할 선호 수집·배정, 개인 투두 생성, 팀원 내보내기, 원본 HTML 변경.

## 검증

- 소유/비소유/비회원/다른 그룹 사용자의 직접 객체 접근을 결정적 테스트로 차단하라.
- 새 그룹 생성 의도가 없는 GET·초대 참여 화면 조회가 그룹이나 코드를 만들지 않고, 반복 생성 POST가 본인 소유의 활성 `FORMING` 초대방을 재사용하는지 검증하라.
- 빈 `FORMING` 취소, 참여자가 있는 `FORMING` 자동 폐기 차단, 코드 재발급 시 그룹 불변·기존 코드 폐기, 초대 추측·재사용·만료·동시 참여와 ProjectBrief revision 충돌을 검증하라.
- dashboard/group의 `새 그룹 만들기`가 생성자용 invite 흐름으로 연결되고 ProjectBrief 화면으로 직접 진입할 수 없는지 검증하라.
- 최소 2명 조건, `그룹 생성하기` 뒤 `FORMING` 유지와 group-create 이동, 원본 완료 팝업 미이식, 다음 버튼에서만 원자적 활성화가 일어나는지 검증하라.
- `ACTIVE` 전환 시 코드가 폐기되고 GATE B 승인 전 활성 그룹 참여가 `GROUP_NOT_JOINABLE`과 승인된 안내 문구로 차단되는지 검증하라.
- 비그룹장의 활성화, ProjectBrief 없는 활성화, 중복 활성화, 활성화 전 chat 직접 접근을 차단하고 ProjectBrief revision 1·cohort snapshot·ACTIVE 상태가 원자적으로 저장되는지 검증하라.
- 저장 후 새 세션에서 그룹과 brief 버전을 재조회하라.
- 팀 목표 표시문이 최신 ProjectBrief revision만 근거로 생성되고 원본에 없는 라벨 없이 팀 목표 영역에 표시되는지, 내용이 비었거나 LIVE 실패·시간 초과일 때 이전/고정 문구로 성공을 가장하지 않는지 확인하라.
- 승인된 2단계 LIVE 예산(최대 3회) 안에서 표시문의 근거 일치, 길이, latency, usage와 오류를 기록하라.
- 원본 HTML 해시를 검사하고 1440×900 스크린샷 캡처를 실행해 사용자가 원본과 나란히 확인할 수 있게 하라.

## 중단 조건

그룹장 권한 확대, 개인정보 노출 또는 범위 밖 관리 정책이 필요하면 승인 없이 구현하지 않는다. LIVE 권한·연결이 없으면 팀 목표 표시문 검증만 BLOCKED로 두고 그룹·ProjectBrief의 결정적 구현은 계속한다.

## 완료 보고

FORMING 초대방→ProjectBrief→ACTIVE 흐름과 데이터 모델, 인가/상태/디자인 검증, 팀 목표 표시문의 LIVE 호출 수 또는 정확한 BLOCKED 사유, 다음 한 단계(`GATE-B.md`의 추천안 작성)를 보고하라.
