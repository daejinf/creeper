from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_execution_plan_ui_separates_scope_summary_and_sw_handoff():
    html = (ROOT / 'tools' / 'bench' / 'kickoff.html').read_text(encoding='utf-8')
    source = (ROOT / 'tools' / 'bench' / 'kickoff-plans.js').read_text(encoding='utf-8')
    css = (ROOT / 'tools' / 'bench' / 'kickoff.css').read_text(encoding='utf-8')
    for fragment in ('showGroupPlan', 'showPersonalPlan', 'groupPlanPane', 'personalPlanPane', 'SW 전달 자료'):
        assert fragment in html
    for fragment in ('selectPlanScope', "'초안 · 실행 계획'", "'할 일 검토하기 · '", '새 버전 만들기', 'planHandoff'):
        assert fragment in source or fragment in html
    for fragment in ('.planScopeTabs', '.planSummary', '.planTaskList>summary', '.swHandoff'):
        assert fragment in css
