"""Non-sensitive text fixtures for stage 8A (portfolio ingest): the Korean chars/token measurement corpus and the
private markers that must never reach a portfolio copy. All text is written for these tests."""
from __future__ import annotations

# Korean prose of the kind a submission holds (reports, retrospectives, specs). Used to measure cl100k chars/token.
KO_PROSE = [
    "우리 팀은 대학생을 위한 일정 관리 앱을 기획했습니다. 저는 디자인을 맡아 피그마로 화면 시안을 세 차례 수정했고, "
    "사용자 인터뷰 다섯 건을 정리해 개선점을 도출했습니다. 그 결과 로그인 화면에서 이탈하는 비율이 눈에 띄게 줄었습니다.",
    "이번 과제의 목표는 캠퍼스 내 빈 강의실을 실시간으로 찾을 수 있게 하는 것이었다. 데이터는 학교 공개 시간표를 "
    "주 1회 수집했고, 강의실별 사용 여부를 30분 단위로 계산했다. 계산 결과를 지도 위에 색으로 표시하는 화면을 만들었다.",
    "설문 응답 120건을 분석한 결과, 응답자의 68%가 과제 마감 직전에 협업 도구를 가장 많이 사용한다고 답했다. 특히 "
    "자료 공유와 일정 조율에서 불편을 크게 느꼈으며, 알림이 너무 많다는 의견도 적지 않았다.",
    "한계도 분명했다. 시간이 부족해 접근성 검사를 충분히 하지 못했고, 실제 사용자 테스트는 두 번밖에 진행하지 못했다. "
    "다음 학기에는 화면 낭독기 사용자를 포함해 테스트 범위를 넓힐 계획이다.",
    "회의록 요약: 3주차 회의에서는 역할 분담을 다시 점검했다. 백엔드 일정이 이틀 늦어져서 프런트엔드는 목업 데이터로 "
    "먼저 화면을 완성하기로 했다. 다음 회의까지 각자 맡은 부분의 진행 상황을 문서로 정리해 오기로 했다.",
    "프로젝트를 통해 배운 점은 문제를 작게 나누어 빠르게 확인하는 습관의 중요성이다. 처음에는 모든 기능을 한 번에 "
    "만들려다 일정이 밀렸지만, 기능을 주 단위로 쪼갠 뒤에는 매주 눈에 보이는 결과를 팀원들과 공유할 수 있었다.",
]
EN_PROSE = [
    "Our team built a schedule planner for students. I designed the screens in Figma and revised them three times "
    "after five user interviews, which reduced the drop-off on the sign-in screen.",
    "The API layer uses FastAPI with PostgreSQL 16. Each request is authenticated with a server-side session, and "
    "background jobs record their state in the database so a restart can recover them.",
]
MIXED = [
    "React와 TypeScript로 프런트엔드를 구현했고, 상태 관리는 Zustand를 사용했다. API 응답 시간은 평균 120ms였다.",
    "Python 3.12 환경에서 pandas로 데이터를 정리한 뒤 matplotlib으로 그래프 4개를 그렸다. 결과는 PDF 보고서로 제출했다.",
    "| 항목 | 값 |\n| 응답 수 | 120 |\n| 만족도 | 4.2 / 5 |\n| 재사용 의향 | 71% |",
]

# Private data planted around a submission. None of it may appear in any portfolio block or node.
PRIVATE_CHAT = "개인대화비밀문장-QX81 저는 사실 이 팀이 불편해요"
PRIVATE_STATE = "멤버상태비밀-ZR42 주 3시간만 가능"
OTHER_MEMBER_TEXT = "다른팀원첨부-KP07 남의 보고서 본문"
