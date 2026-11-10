from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
OUTPUT = ROOT / "output" / "computer_network_assignment01_report_draft.docx"


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=90, start=100, bottom=90, end=100):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{margin}"))
        if node is None:
            node = OxmlElement(f"w:{margin}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_repeat_table_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def apply_font(run, size=10.2, bold=False, color="000000"):
    run.font.name = "Malgun Gothic"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Malgun Gothic")
    run.font.size = Pt(size)
    run.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)


def add_text(paragraph, text, size=10.2, bold=False, color="000000"):
    run = paragraph.add_run(text)
    apply_font(run, size=size, bold=bold, color=color)
    return run


def add_heading(doc, text, level=1):
    p = doc.add_paragraph(style=f"Heading {level}")
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.space_before = Pt(13 if level == 1 else 9)
    p.paragraph_format.space_after = Pt(5)
    add_text(p, text, size=15 if level == 1 else 12, bold=True)
    return p


def add_body(doc, text, bold_lead=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(5)
    p.paragraph_format.line_spacing = 1.28
    if bold_lead and text.startswith(bold_lead):
        add_text(p, bold_lead, bold=True)
        add_text(p, text[len(bold_lead):])
    else:
        add_text(p, text)
    return p


def add_bullet(doc, text):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(2)
    add_text(p, text)
    return p


def add_table(doc, headers, rows, widths=None, font_size=8.8):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    table.autofit = False
    hdr = table.rows[0]
    set_repeat_table_header(hdr)
    for idx, header in enumerate(headers):
        cell = hdr.cells[idx]
        set_cell_shading(cell, "244062")
        set_cell_margins(cell)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        add_text(p, header, size=font_size, bold=True, color="FFFFFF")
        if widths:
            cell.width = widths[idx]
    for r_idx, values in enumerate(rows):
        row = table.add_row()
        for c_idx, value in enumerate(values):
            cell = row.cells[c_idx]
            set_cell_margins(cell)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            if r_idx % 2 == 1:
                set_cell_shading(cell, "F3F6FA")
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT if c_idx == len(values) - 1 and len(values) > 3 else WD_ALIGN_PARAGRAPH.CENTER
            add_text(p, str(value), size=font_size)
            if widths:
                cell.width = widths[c_idx]
    doc.add_paragraph().paragraph_format.space_after = Pt(1)
    return table


def add_figure(doc, path, caption, width=6.2):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next = True
    p.add_run().add_picture(str(path), width=Inches(width))
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(8)
    add_text(cap, caption, size=9, color="404040")


def add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_text(paragraph, "- ", size=9, color="666666")
    run = paragraph.add_run()
    fld_char1 = OxmlElement("w:fldChar")
    fld_char1.set(qn("w:fldCharType"), "begin")
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = "PAGE"
    fld_char2 = OxmlElement("w:fldChar")
    fld_char2.set(qn("w:fldCharType"), "end")
    run._r.extend([fld_char1, instr_text, fld_char2])
    add_text(paragraph, " -", size=9, color="666666")


doc = Document()
section = doc.sections[0]
section.page_width = Cm(21.0)
section.page_height = Cm(29.7)
section.top_margin = Cm(1.8)
section.bottom_margin = Cm(1.8)
section.left_margin = Cm(1.9)
section.right_margin = Cm(1.9)

styles = doc.styles
normal = styles["Normal"]
normal.font.name = "Malgun Gothic"
normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Malgun Gothic")
normal.font.size = Pt(10.2)
for style_name in ("Title", "Heading 1", "Heading 2"):
    style = styles[style_name]
    style.font.name = "Malgun Gothic"
    style._element.rPr.rFonts.set(qn("w:eastAsia"), "Malgun Gothic")
    style.font.color.rgb = RGBColor(0, 0, 0)

add_page_number(section.footer.paragraphs[0])

# Cover
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(78)
p.paragraph_format.space_after = Pt(18)
add_text(p, "컴퓨터네트워크 Assignment 1", size=24, bold=True)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_after = Pt(55)
add_text(p, "Mininet 기반 정적 라우팅 구성 및 검증", size=16, bold=True, color="244062")

info = add_table(
    doc,
    ["항목", "내용"],
    [
        ["학과", "직접 입력"],
        ["학번", "직접 입력"],
        ["이름", "직접 입력"],
        ["제출일", "2026년 11월 13일"],
    ],
    widths=[Cm(4.0), Cm(8.0)],
    font_size=10.5,
)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(35)
add_text(p, "전남대학교 컴퓨터네트워크", size=12, bold=True)

doc.add_page_break()

add_heading(doc, "1 과제 목적과 결과 요약")
add_body(
    doc,
    "본 과제에서는 Mininet과 MiniEdit을 이용하여 6개의 호스트, 6개의 라우터, 1개의 스위치로 구성된 네트워크를 구현하였다. 각 노드의 인터페이스 이름을 eth0, eth1, eth2 형식으로 통일하고 IPv4 주소와 정적 경로를 설정하였다.",
)
add_body(
    doc,
    "최종 시험에서 h1부터 h6까지 모든 종단 호스트가 서로 통신하였다. 라우터 간 링크에는 /30 네트워크를 사용했으며, 기본 경로와 요약 경로를 활용하여 라우팅 항목 수를 줄였다. traceroute 결과에서도 h1에서 h6, h3에서 h6으로 가는 경로가 최소 홉 경로를 사용함을 확인하였다.",
)

add_heading(doc, "2 실험 환경")
add_table(
    doc,
    ["항목", "설정"],
    [
        ["호스트 운영체제", "Windows 11"],
        ["가상화 프로그램", "Oracle VirtualBox 7"],
        ["게스트 운영체제", "Ubuntu 26.04.1 LTS"],
        ["네트워크 에뮬레이터", "Mininet 2.3.0"],
        ["가상 스위치", "Open vSwitch"],
        ["토폴로지 도구", "MiniEdit"],
    ],
    widths=[Cm(5.0), Cm(10.0)],
    font_size=9.5,
)
add_figure(doc, ASSETS / "01_mininet_basic_test.png", "그림 1 Mininet 기본 pingall 시험 결과", width=6.1)

add_heading(doc, "3 네트워크 토폴로지")
add_body(
    doc,
    "제공된 comnet_topology.mn 파일을 MiniEdit으로 불러왔다. h1과 h2는 s7을 통해 r1에 연결되고, h3은 r2, h4와 h5는 r3, h6은 r6에 연결된다. 라우터 구간에는 r1-r2-r3, r1-r4-r3, r3-r5, r4-r5, r5-r6 링크가 존재한다.",
)
add_figure(doc, ASSETS / "02_topology.png", "그림 2 과제에서 제공된 MiniEdit 토폴로지", width=6.35)

add_heading(doc, "4 IP 주소 설계")
add_body(
    doc,
    "호스트 구간에는 과제에서 제시한 10, 30, 40, 60번 대역을 사용하였다. 라우터 간 점대점 링크는 주소 낭비를 줄이기 위해 /30으로 구성했다. h4와 h5 구간은 각각 40.0.0.0/24와 40.0.1.0/24로 배치하여 원격 라우터에서 40.0.0.0/23으로 요약할 수 있도록 설계했다.",
)

host_rows = [
    ["h1", "eth0", "10.0.0.11/24", "10.0.0.1"],
    ["h2", "eth0", "10.0.0.12/24", "10.0.0.1"],
    ["h3", "eth0", "30.0.0.11/24", "30.0.0.1"],
    ["h4", "eth0", "40.0.0.11/24", "40.0.0.1"],
    ["h5", "eth0", "40.0.1.11/24", "40.0.1.1"],
    ["h6", "eth0", "60.0.0.11/24", "60.0.0.1"],
]
add_table(doc, ["호스트", "인터페이스", "IP 주소", "기본 게이트웨이"], host_rows, widths=[Cm(2), Cm(3), Cm(4.6), Cm(4.6)])

doc.add_page_break()
add_heading(doc, "4.1 라우터 인터페이스 주소", level=2)
router_rows = [
    ["r1", "eth0", "10.0.0.1/24", "s7 LAN"], ["r1", "eth1", "192.168.12.1/30", "r2"], ["r1", "eth2", "192.168.14.1/30", "r4"],
    ["r2", "eth0", "192.168.12.2/30", "r1"], ["r2", "eth1", "30.0.0.1/24", "h3"], ["r2", "eth2", "192.168.23.1/30", "r3"],
    ["r3", "eth0", "192.168.23.2/30", "r2"], ["r3", "eth1", "192.168.34.1/30", "r4"], ["r3", "eth2", "40.0.0.1/24", "h4"], ["r3", "eth3", "40.0.1.1/24", "h5"], ["r3", "eth4", "192.168.35.1/30", "r5"],
    ["r4", "eth0", "192.168.34.2/30", "r3"], ["r4", "eth1", "192.168.14.2/30", "r1"], ["r4", "eth2", "192.168.45.1/30", "r5"],
    ["r5", "eth0", "192.168.35.2/30", "r3"], ["r5", "eth1", "192.168.45.2/30", "r4"], ["r5", "eth2", "192.168.56.1/30", "r6"],
    ["r6", "eth0", "192.168.56.2/30", "r5"], ["r6", "eth1", "60.0.0.1/24", "h6"],
]
add_table(doc, ["라우터", "인터페이스", "IP 주소", "연결 대상"], router_rows, widths=[Cm(2), Cm(3), Cm(5), Cm(4)])

add_heading(doc, "5 인터페이스 이름 통일")
add_body(
    doc,
    "Mininet은 기본적으로 h1-eth0, r1-eth0과 같은 이름을 생성한다. 제출 환경에서는 eth0, eth1, eth2 형식이 필요하므로 각 노드의 셸 스크립트에서 ip link 명령으로 인터페이스 이름을 변경한 뒤 IP 주소를 설정하였다.",
)
add_figure(doc, ASSETS / "03_interfaces.png", "그림 3 eth 형식으로 통일한 인터페이스 이름과 IP 주소", width=6.15)

add_heading(doc, "6 정적 라우팅 설계")
add_body(
    doc,
    "직접 연결된 네트워크는 운영체제가 자동으로 라우팅 테이블에 등록한다. 원격 네트워크에는 기본 경로와 필요한 예외 경로만 추가했다. 이 구성은 목적지별 최단 경로를 유지하면서 수동 라우팅 항목 수를 줄인다.",
)
route_rows = [
    ["r1", "60.0.0.0/24", "192.168.14.2", "r4 방향"], ["r1", "default", "192.168.12.2", "r2 방향"],
    ["r2", "10.0.0.0/24", "192.168.12.1", "r1 방향"], ["r2", "default", "192.168.23.2", "r3 방향"],
    ["r3", "60.0.0.0/24", "192.168.35.2", "r5 방향"], ["r3", "default", "192.168.23.1", "r2 방향"],
    ["r4", "40.0.0.0/23", "192.168.34.1", "r3 방향 요약 경로"], ["r4", "60.0.0.0/24", "192.168.45.2", "r5 방향"], ["r4", "default", "192.168.14.1", "r1 방향"],
    ["r5", "10.0.0.0/24", "192.168.45.1", "r4 방향"], ["r5", "60.0.0.0/24", "192.168.56.2", "r6 방향"], ["r5", "default", "192.168.35.1", "r3 방향"],
    ["r6", "default", "192.168.56.1", "r5 방향"],
]
add_table(doc, ["라우터", "목적지", "다음 홉", "설명"], route_rows, widths=[Cm(2), Cm(4), Cm(4), Cm(5)])

add_heading(doc, "7 통신 시험")
add_body(
    doc,
    "각 호스트에서 다른 모든 종단 호스트의 주소로 ICMP echo request를 전송했다. 대표적인 장거리·역방향·서로 다른 40번 대역 시험에서 패킷 손실이 0%였고, 전체 검사에서도 모든 호스트가 6개 주소에 도달했다.",
)
ping_rows = []
hosts = ["h1", "h2", "h3", "h4", "h5", "h6"]
for src in hosts:
    ping_rows.append([src] + ["-" if src == dst else "성공" for dst in hosts])
add_table(doc, ["출발지"] + hosts, ping_rows, widths=[Cm(2.1)] + [Cm(2.0)] * 6, font_size=8.5)
add_figure(doc, ASSETS / "04_ping.png", "그림 4 대표 호스트 간 ping 시험 결과", width=6.15)

doc.add_page_break()
add_heading(doc, "8 최단 경로 검증")
add_body(
    doc,
    "traceroute를 사용하여 목적지까지 실제로 통과하는 라우터를 확인했다. h1에서 h6으로 가는 경로는 r1-r4-r5-r6이고, h3에서 h6으로 가는 경로는 r2-r3-r5-r6이다. 두 경로 모두 토폴로지에서 가능한 최소 라우터 홉 수를 사용한다.",
)
add_table(
    doc,
    ["출발지", "목적지", "검증 경로", "라우터 홉"],
    [
        ["h1", "h6", "h1 - r1 - r4 - r5 - r6 - h6", "4"],
        ["h3", "h6", "h3 - r2 - r3 - r5 - r6 - h6", "4"],
    ],
    widths=[Cm(2), Cm(2), Cm(8), Cm(3)],
)
add_figure(doc, ASSETS / "08_traceroute.png", "그림 5 traceroute를 이용한 최단 경로 확인", width=6.15)

add_heading(doc, "9 셸 스크립트 구성")
add_body(
    doc,
    "제출 파일은 h1.sh부터 h6.sh, r1.sh부터 r6.sh까지 총 12개이다. 호스트 스크립트는 인터페이스 이름 변경, IP 주소 설정, 기본 게이트웨이 등록을 수행한다. 라우터 스크립트는 여기에 IPv4 forwarding 활성화와 정적 경로 등록을 추가로 수행한다.",
)
add_bullet(doc, "호스트 스크립트: eth0 이름 변경, 호스트 IP 설정, 기본 게이트웨이 설정")
add_bullet(doc, "라우터 스크립트: 다중 eth 인터페이스 설정, net.ipv4.ip_forward 활성화, 정적 경로 설정")
add_bullet(doc, "검증 명령: ip -br -4 addr, route -n, ping, traceroute")

add_heading(doc, "10 생성형 AI 사용 내역")
add_body(
    doc,
    "Ubuntu와 VirtualBox 설치, Mininet 환경 구성, 인터페이스 이름 변경 방법, IP 주소 및 정적 라우팅 설계 초안, 셸 스크립트 작성과 오류 점검 과정에서 ChatGPT/Codex의 도움을 받았다. 제안된 명령과 설정은 Mininet에서 직접 실행했으며, 인터페이스 주소, 라우팅 테이블, ping 및 traceroute 결과를 통해 동작을 확인하였다.",
)
add_body(
    doc,
    "제출 전에는 본인이 실제로 사용한 범위에 맞게 위 문장을 수정하고, 과제 지시에 따라 관련 대화 화면을 첨부해야 한다.",
    bold_lead="제출 전에는",
)

add_heading(doc, "11 결론")
add_body(
    doc,
    "Mininet에서 주어진 토폴로지를 구성하고 모든 호스트·라우터 인터페이스를 eth 형식으로 통일하였다. 점대점 링크에 /30 네트워크를 적용하고 기본 경로, 예외 경로, 40번 대역 요약 경로를 조합하여 라우팅 테이블을 간결하게 유지했다. 최종 ping 및 traceroute 시험으로 모든 호스트의 상호 통신과 주요 목적지의 최단 경로를 확인하였다.",
)

add_heading(doc, "제출 전 확인 사항")
for item in [
    "표지의 학과, 학번, 이름을 실제 정보로 수정한다.",
    "모든 캡처에서 오류 문구가 없는지 확인한다.",
    "h1.sh부터 h6.sh, r1.sh부터 r6.sh까지 총 12개를 포함한다.",
    "최종 레포트를 PDF 형식으로 저장한다.",
    "PDF와 셸 스크립트를 하나의 ZIP 파일로 압축한다.",
]:
    add_bullet(doc, item)

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
doc.save(OUTPUT)
print(OUTPUT)
