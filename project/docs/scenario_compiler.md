# 场景编译器

## 创建场景

在“智能生成”选择“场景驱动生成”，只需输入验证目标、仿真对象或子系统、任务阶段、规模、重点风险和是否使用项目默认配置。

工作流依次解析意图、读取项目画像和需求、检索 approved 知识与原始片段、匹配 approved 模板、规划角色、匹配装备、求解数量、生成必要变体、校验并保存 provenance。变体按证据选择，不机械生成全部类型。

## 审核与生成

页面显示每个自动字段的来源类型、装备来源和数量公式。数量没有规则时保持 `null` 并显示“待确认”。阻断问题必须逐项处理；非阻断建议可批量接受。批准后的场景可直接生成多条测试用例。

## 数量规则

规则写入 `equipment_configuration_rules.parameters_json`，禁止 `eval()`。示例：

```json
{
  "rule_type": "capacity",
  "equipment_id": "EQ-001",
  "parameters": {
    "demand_variable": "target_count",
    "capacity_per_unit": 4,
    "rounding": "ceil",
    "redundancy": 1,
    "unit": "entity"
  }
}
```

支持 `fixed`、`minimum`、`capacity`、`ratio`、`redundancy`、`min_max`、`inventory_limit`、`dependency`、`mutual_exclusion` 和 `manual_only`。库存不足返回冲突，不静默缩减；依赖执行拓扑和循环检测；没有规则时禁止 LLM 猜数量。

## 校验

校验覆盖需求与来源 grounding、状态一致性、角色完整性、装备能力、数量与参数追踪、可观测性、矛盾、恢复、项目作用域和书籍覆盖。跨项目数据、不存在装备、无来源数量、书籍覆盖项目参数、不存在 chunk、步骤无法对应结果均阻断。

旧 `rag/scenario_generator.expand_scenario()` 仍可调用，内部使用该编译工作流并转换为旧格式。
