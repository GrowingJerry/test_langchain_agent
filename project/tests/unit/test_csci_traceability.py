from pathlib import Path

from application.services.traceability_service import (
    atomic_indicators, build_coverage_plan, coverage_matrix, match_indicator_elements,
    parse_csci_docx, parse_csci_structure, parse_offline_html, validate_step_alignment,
)


TEXT = """1 CSCI能力需求
1.1 新闻门户/标识符_XWMP
1.1.1 个人中心/标识符_GRZX
1.1.1.1 个人信息配置/标识符_GRXXPZ
功能描述
用户可以查看和修改姓名、手机号。姓名不能为空；手机号应为11位数字；保存后提示“保存成功”。
输入
姓名、手机号
处理
校验输入；数据库持久化需联机确认
输出
页面显示校验状态和提示
2 外部接口
接口内容不得混入
"""


def test_csci_boundary_tree_leaf_and_stable_ids():
    first = parse_csci_structure(TEXT, "fixture.docx")
    second = parse_csci_structure(TEXT, "fixture.docx")
    assert len(first) == 1
    assert first[0].hierarchy_path == ["新闻门户", "个人中心", "个人信息配置"]
    assert first[0].identifier == "GRXXPZ"
    assert first[0].node_id == second[0].node_id
    assert "接口内容" not in str(first[0].sections)


def test_atomization_html_matching_and_coverage():
    node = parse_csci_structure(TEXT, "fixture.docx")[0]
    indicators = atomic_indicators(node)
    assert any("姓名不能为空" in item.indicator_text for item in indicators)
    html = Path(__file__).parents[1] / "fixtures" / "csci_demo" / "profile.html"
    page, elements = parse_offline_html(html.read_bytes(), "profile.html")
    mobile = next(x for x in indicators if "手机号" in x.indicator_text)
    link = match_indicator_elements(mobile, elements)
    assert page["title"].startswith("测试夹具")
    assert any(x.label == "手机号码" for x in elements)
    plans = build_coverage_plan(indicators, [link])
    assert len(plans) == len(indicators)
    cases = [{"case_id": "TC-1", "indicator_ids": [mobile.indicator_id]}]
    matrix = coverage_matrix(indicators, cases)
    assert {x["coverage_status"] for x in matrix} >= {"已覆盖", "未覆盖"}


def test_step_expected_alignment():
    validate_step_alignment({"steps": ["输入", "保存"], "expected": ["显示输入", "显示提示"]})
    try:
        validate_step_alignment({"steps": ["输入"], "expected": []})
    except ValueError:
        pass
    else:
        raise AssertionError("misaligned case was accepted")


def test_docx_fixture_pipeline(tmp_path):
    from tests.fixtures.csci_demo.build_fixture import build
    leaves = parse_csci_docx(build(tmp_path / "synthetic.docx"))
    assert {x.identifier for x in leaves} == {"GRXXPZ", "YHCX"}
    profile = next(x for x in leaves if x.identifier == "GRXXPZ")
    assert len(atomic_indicators(profile)) >= 5
