"""The stage 8D evaluation corpus (RAG_DESIGN 7.1): three non-sensitive users, mixed formats
(PDF · DOCX · PPTX · XLSX · images · HWP · MD · TXT), written with `portfolio_corpus.build`.

- 평가A (`evalseta`): the 8C user A material (two projects, the injection file, a FAILED item) plus screen-mockup
  images and a third project, a data contest with a spreadsheet, slides with a chart image, a heat-map capture, a
  result notice capture and an HWP report. A's corpus is large enough that questions take the search path.
- 평가B (`evalsetb`): a look-alike `캠퍼스 길찾기 앱` with different facts (marker "베타팀") and a marketing report.
- 평가C (`evalsetc`): a tiny club newsletter (small corpus path, marker "감마") with a cover photo.

Image blocks hold the text a vision call would have written (`kind` "image"); an HWP file is name-only exactly like
the product (`kind` "meta", RAG_DESIGN 2.2). Every sentence here is hand-written FIXTURE material, not a Timely result.
"""
from __future__ import annotations

import copy
from datetime import date

import portfolio_corpus as PC

SALES = "\n".join(["월\tA지점\tB지점\tC지점\t합계"] + [
    f"{m}월\t{a:,}만원\t{b:,}만원\t{c:,}만원\t{a + b + c:,}만원"
    for m, a, b, c in [(1, 610, 480, 720), (2, 640, 470, 760), (3, 700, 520, 850),
                       (4, 720, 530, 900), (5, 760, 540, 1010), (6, 770, 560, 1360)]])

_A = copy.deepcopy(PC.USER_A)
_A["username"] = "evalseta"
_A["projects"][0]["items"].insert(3, {"title": "화면 시안 캡처", "done": date(2026, 10, 12), "files": [
    ("시안1.png", "PNG", [("a_shot1", "image", {},
                          "첫 화면 시안 캡처. 상단에 검색창이 있고 가운데에 캠퍼스 지도가 크게 보인다. 하단에는 즐겨찾기 버튼 3개가 "
                          "가로로 놓여 있다. 강조색은 주황색이다.")]),
    ("시안2.png", "PNG", [("a_shot2", "image", {},
                          "건물 상세 화면 시안 캡처. 위쪽에 건물 사진이 있고 그 아래 층별 시설 목록이 있다: 1층 카페, 2층 열람실, "
                          "3층 강의실. 오른쪽 위에 즐겨찾기 별 아이콘이 있다.")]),
], "summary": [("결과물", "첫 화면과 건물 상세 화면의 시안을 만들었다.", ["a_shot1", "a_shot2"])]})
_A["projects"].append({"name": "데이터분석 공모전", "role": "시각화", "items": [
    {"title": "매출 데이터 정리", "done": date(2026, 8, 10), "files": [
        ("매출데이터.xlsx", "XLSX", [("d_sales", "table", {"sheet": "월별", "rows": [1, 7]}, SALES)]),
        ("데이터정리.docx", "DOCX", [
            ("d_clean", "text", {"section": "정제 기준"},
             "원본 거래 기록 1만 2천 건 가운데 결측치가 있는 37건을 지웠고 중복 거래 12건을 합쳤다. 환불 거래는 매출에서 "
             "뺐다. 데이터 출처는 공공데이터포털의 상권 매출 데이터다."),
            ("d_vars", "text", {"section": "변수 정의"},
             "분석 변수는 지점, 월, 요일, 시간대, 결제 금액 다섯 가지로 정했다. 시간대는 2시간 단위로 나누었다."),
        ]),
    ], "summary": [("결과물", "1만 2천 건의 매출 데이터를 정제해 분석 변수 다섯 가지를 정했다.", ["d_clean", "d_vars"])]},
    {"title": "대시보드 제작", "done": date(2026, 8, 24), "files": [
        ("대시보드.pptx", "PPTX", [
            ("d_slide1", "slide", {"slide": 1},
             "대시보드 구성\n매출 추이 선그래프, 지점별 막대그래프, 요일별 히트맵 세 가지 차트로 구성했다\n도구: 태블로"),
            ("d_slide2", "slide", {"slide": 2},
             "핵심 발견\n주말 매출이 평일보다 34% 높다\nC지점은 6월 매출이 전달보다 크게 늘었다"),
            ("d_chart", "image", {"slide": 2, "image": 1},
             "지점별 상반기 매출 막대그래프. A지점 4,200만원, B지점 3,100만원, C지점 5,600만원으로 C지점 막대가 가장 길다. "
             "막대 색은 파란색 계열이다."),
        ]),
        ("히트맵.png", "PNG", [("d_heat", "image", {},
                              "요일별 시간대 매출 히트맵 캡처. 토요일 오후 2시부터 6시 칸이 가장 진한 빨간색이고, 화요일 오전 칸이 "
                              "가장 옅은 색이다. 세로축은 요일, 가로축은 2시간 단위 시간대다.")]),
    ], "summary": [("결과물", "세 가지 차트로 된 매출 대시보드를 태블로로 만들었다.", ["d_slide1"]),
                   ("수치·성과", "주말 매출이 평일보다 34% 높다는 점을 찾았다.", ["d_slide2"])]},
    {"title": "본선 발표", "done": date(2026, 9, 5), "files": [
        ("발표대본.docx", "DOCX", [
            ("d_script", "text", {"section": "발표 대본"},
             "발표 시간은 7분이었다. 첫 장면에서 주말 매출 차이를 보여 주고, 마지막에 C지점의 주말 오후 인력 배치를 늘리자고 "
             "제안했다. 심사위원이 데이터 출처를 물어 공공데이터포털 상권 데이터라고 답했다."),
        ]),
        ("결과공지.png", "PNG", [("d_award", "image", {},
                                "공모전 결과 공지 화면 캡처. 본선에 오른 12팀 가운데 우리 팀 이름 옆에 '장려상'이 적혀 있고 "
                                "상금은 50만원이다.")]),
        ("최종보고서.hwp", "HWP", [("d_hwp", "meta", {}, "파일: 최종보고서.hwp")]),
    ], "summary": None},
]})

_B = copy.deepcopy(PC.USER_B)
_B["username"] = "evalsetb"
_B["projects"].append({"name": "마케팅원론 팀 리포트", "role": "자료 조사", "items": [
    {"title": "시장 조사", "done": date(2026, 5, 12), "files": [
        ("조사자료.docx", "DOCX", [
            ("m_comp", "text", {"section": "경쟁사"},
             "베타팀 조사: 학교 앞 경쟁 카페 3곳의 아메리카노 평균 가격은 4,100원이었다. 가장 싼 곳은 3,500원이었다."),
            ("m_survey", "text", {"section": "설문"},
             "학생 64명에게 설문했다. 테이크아웃 비율은 72%였고, 가장 원하는 혜택은 스탬프 적립이었다."),
        ]),
        ("리포트.hwp", "HWP", [("m_hwp", "meta", {}, "파일: 리포트.hwp")]),
    ], "summary": [("결과물", "경쟁 카페 가격과 학생 64명 설문을 조사했다.", ["m_comp", "m_survey"])]},
    {"title": "포스터 제작", "done": date(2026, 5, 30), "files": [
        ("포스터.png", "PNG", [("m_poster", "image", {},
                              "홍보 포스터. 노란 배경에 '첫 잔 무료'라는 큰 문구가 있고 하단 가운데에 QR 코드가 있다.")]),
    ], "summary": None},
]})

_C = copy.deepcopy(PC.USER_C)
_C["username"] = "evalsetc"
_C["projects"][0]["items"].append({"title": "표지 촬영", "done": date(2026, 10, 3), "files": [
    ("표지.jpg", "JPEG", [("c_cover", "image", {},
                          "소식지 표지 사진. 동아리방 책장 앞에서 회원 5명이 책을 들고 웃고 있다. 오른쪽 아래에 감마 로고가 있다.")]),
], "summary": None})

USERS = {"a": _A, "b": _B, "c": _C}
MARKERS = {"a": [], "b": ["베타팀"], "c": ["감마"]}   # another user's marker in an answer is a cross-user mix
FAILED_KEYS = {"a_failed"}                          # material of a FAILED job: never evidence


def keys(user: str) -> set[str]:
    out = set()
    for p in USERS[user]["projects"]:
        for it in p["items"]:
            for _, _, blocks in it["files"]:
                out.update(b[0] for b in blocks)
    return out


def projects(user: str) -> list[str]:
    return [p["name"] for p in USERS[user]["projects"]]
