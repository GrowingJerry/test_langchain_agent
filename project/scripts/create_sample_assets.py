# -*- coding: utf-8 -*-
"""生成示例 Excel：用例库、场景表、规则表。"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

DATA = ROOT / "data"
RULES = ROOT / "rules"
DATA.mkdir(parents=True, exist_ok=True)
RULES.mkdir(parents=True, exist_ok=True)


def main() -> None:
    """写入 sample_case_library.xlsx、sample_scenarios.xlsx、规则表。"""
    cases = [
        {
            "case_id": "LIB-TC-001",
            "case_name": "通信中断恢复测试",
            "requirement_text": "通信中断后自动重连，恢复后时限内成功率满足要求",
            "six_quality_attribute": "可靠性",
            "test_object": "通信子系统",
            "test_type": "恢复性测试",
            "test_method": "故障注入测试、恢复性测试",
            "test_condition": "链路可人为断开与恢复",
            "test_environment": "实验室闭环环境",
            "test_steps": "1.建立正常链路\n2.注入通信中断\n3.恢复链路并记录重连时间\n4.统计成功率",
            "expected_result": "通信恢复后在规定时间内完成重连，成功率满足需求",
            "pass_criteria": "满足需求规定值",
            "record_items": "中断时刻;恢复时刻;重连耗时;成功标志",
            "test_result_template": "重连次数___ 成功次数___ 成功率___",
            "tags": "通信,重连",
        },
        {
            "case_id": "LIB-TC-002",
            "case_name": "响应时间测试",
            "requirement_text": "输入后规定时间内完成识别结果显示",
            "six_quality_attribute": "性能",
            "test_object": "识别处理模块",
            "test_type": "计时测试",
            "test_method": "计时测试、重复测试",
            "test_condition": "标准输入样本",
            "test_environment": "实验室",
            "test_steps": "1.准备计时工具\n2.输入样本并记录开始时间\n3.记录结果显示时间\n4.计算耗时",
            "expected_result": "响应时间满足需求阈值",
            "pass_criteria": "满足需求规定值",
            "record_items": "样本编号;开始时间;结束时间;耗时",
            "test_result_template": "平均耗时___ 最大耗时___",
            "tags": "响应时间",
        },
        {
            "case_id": "LIB-TC-003",
            "case_name": "日志记录测试",
            "requirement_text": "记录用户操作、异常告警和系统状态变化",
            "six_quality_attribute": "测试性",
            "test_object": "日志服务",
            "test_type": "功能验证",
            "test_method": "日志审查测试",
            "test_condition": "开启标准日志级别",
            "test_environment": "测试环境",
            "test_steps": "1.执行典型操作\n2.触发可控异常\n3.导出日志比对字段",
            "expected_result": "关键事件均有对应日志记录",
            "pass_criteria": "按测试大纲规定判定",
            "record_items": "日志文件路径;事件时间线",
            "test_result_template": "缺失项___ 备注___",
            "tags": "日志",
        },
        {
            "case_id": "LIB-TC-004",
            "case_name": "开机自检测试",
            "requirement_text": "开机自检并在关键模块异常时告警",
            "six_quality_attribute": "测试性",
            "test_object": "系统引导与自检模块",
            "test_type": "功能验证",
            "test_method": "功能验证测试、故障注入测试",
            "test_condition": "冷启动",
            "test_environment": "实验室",
            "test_steps": "1.冷启动系统\n2.观察自检结果\n3.模拟关键模块异常\n4.检查告警",
            "expected_result": "异常被检出并提示",
            "pass_criteria": "满足需求规定值",
            "record_items": "自检项列表;告警内容",
            "test_result_template": "自检结论___ 告警是否出现___",
            "tags": "自检",
        },
        {
            "case_id": "LIB-TC-005",
            "case_name": "权限访问控制测试",
            "requirement_text": "非授权用户无法访问配置功能",
            "six_quality_attribute": "安全性",
            "test_object": "配置管理界面",
            "test_type": "安全性测试",
            "test_method": "访问控制测试",
            "test_condition": "准备授权与非授权账号",
            "test_environment": "测试网络",
            "test_steps": "1.非授权登录尝试访问配置\n2.授权账号访问配置\n3.核对审计记录",
            "expected_result": "非授权访问被拒绝",
            "pass_criteria": "满足需求规定值",
            "record_items": "账号角色;访问结果;审计记录",
            "test_result_template": "拒绝访问次数___",
            "tags": "权限",
        },
        {
            "case_id": "LIB-TC-006",
            "case_name": "低温环境适应性测试",
            "requirement_text": "低温环境下完成基本功能验证",
            "six_quality_attribute": "环境适应性",
            "test_object": "整机",
            "test_type": "环境试验",
            "test_method": "环境适应性试验",
            "test_condition": "低温箱达到规定温度并稳定",
            "test_environment": "低温试验箱",
            "test_steps": "1.预处理\n2.降温至规定值\n3.执行基本功能用例\n4.记录异常",
            "expected_result": "基本功能正常",
            "pass_criteria": "按测试大纲规定判定",
            "record_items": "温度曲线;功能检查结果",
            "test_result_template": "功能结论___ 异常说明___",
            "tags": "低温",
        },
    ]
    pd.DataFrame(cases).to_excel(DATA / "sample_case_library.xlsx", index=False)

    scenarios = [
        {
            "scenario_id": "SCE-001",
            "requirement_id": "REQ-001",
            "scenario_name": "无人车夜间避障",
            "scenario_environment": "夜间低照度、道路存在障碍物",
            "initial_condition": "车辆以10km/h直行",
            "trigger_event": "前方15m出现静止障碍物",
            "expected_behavior": "安全制动或避让",
            "evaluation_metrics": "识别时间、制动距离、日志完整性",
        }
    ]
    pd.DataFrame(scenarios).to_excel(DATA / "sample_scenarios.xlsx", index=False)

    six_rules = pd.DataFrame(
        [
            {"category": "可靠性", "keywords": "连续运行,故障,恢复,重连"},
            {"category": "安全性", "keywords": "权限,越权,防护,制动"},
        ]
    )
    six_rules.to_excel(RULES / "six_quality_rules.xlsx", index=False)

    tm_rules = pd.DataFrame(
        [
            {"pattern": "响应时间|延迟", "methods": "计时测试,重复测试"},
            {"pattern": "准确率|成功率", "methods": "样本集验证测试,统计分析测试"},
        ]
    )
    tm_rules.to_excel(RULES / "test_method_rules.xlsx", index=False)

    print("已生成:", DATA / "sample_case_library.xlsx")
    print("已生成:", DATA / "sample_scenarios.xlsx")
    print("已生成:", RULES / "six_quality_rules.xlsx", RULES / "test_method_rules.xlsx")


if __name__ == "__main__":
    main()
