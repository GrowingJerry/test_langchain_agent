"""Schemas for CSCI requirement and offline-HTML traceability."""

from typing import Any, Literal

from pydantic import BaseModel, Field


IndicatorType = Literal[
    "展示", "输入", "点击", "选择", "默认状态", "校验", "处理", "输出",
    "提示", "跳转", "保存", "删除", "权限", "异常", "性能或定量指标", "其他",
]


class RequirementNode(BaseModel):
    node_id: str
    name: str
    identifier: str
    identifier_generated: bool = False
    level: int
    parent_id: str = ""
    section_number: str = ""
    hierarchy_path: list[str] = Field(default_factory=list)
    source_document: str = ""
    source_block_id: str = ""
    sections: dict[str, str] = Field(default_factory=dict)
    need_human_confirm: bool = False


class RequirementIndicator(BaseModel):
    indicator_id: str
    capability_id: str
    function_id: str
    parent_indicator_id: str = ""
    indicator_text: str
    indicator_type: IndicatorType = "其他"
    source_section: str = "功能描述"
    source_block_id: str = ""
    source_table_index: int | None = None
    source_row_index: int | None = None
    source_text: str = ""
    input_constraints: list[str] = Field(default_factory=list)
    processing_rules: list[str] = Field(default_factory=list)
    expected_behavior: list[str] = Field(default_factory=list)
    exception_rules: list[str] = Field(default_factory=list)
    verification_scope: str = "offline"
    need_human_confirm: bool = False


class HtmlElement(BaseModel):
    element_id: str
    page_id: str
    tag: str
    element_type: str = ""
    text: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    label: str = ""
    visible: bool = True
    enabled: bool = True
    default_value: str = ""
    options: list[str] = Field(default_factory=list)
    form_id: str = ""
    local_events: list[str] = Field(default_factory=list)
    locator_candidates: list[str] = Field(default_factory=list)
    semantic_position: str = ""
    dom_path: str = ""


class CoveragePlanItem(BaseModel):
    function_id: str
    indicator_id: str
    source_location: str = ""
    html_page_id: str = ""
    html_element_ids: list[str] = Field(default_factory=list)
    test_types: list[str] = Field(default_factory=list)
    category: str = "正常"
    offline_verifiable: bool = True
    need_human_confirm: bool = False
    planned_case_count: int = 1

