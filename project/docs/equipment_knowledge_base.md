# 装备知识库

## 导入 military.jsonl

导入器先根据实际字段建立可配置映射，逐行流式处理，不能把 JSONL 当作普通文档分块：

```powershell
python scripts/import_military_jsonl.py --file data/military.jsonl --project-id GLOBAL
```

也可以把 `--project-id` 换成已存在的当前项目 ID。每条记录保留 `source_file`、`source_line_no`、`raw_payload_json` 和稳定 `record_hash`；未知字段仍在原始 JSON 中。单条非法 JSON 只记入导入错误，不中断整文件。

## 标准字段与检索

标准化字段包括装备ID、名称、别名、类别、平台类型、角色、能力、接口、约束、仿真参数、最小/最大单元和来源描述。检索顺序为精确名称、精确别名、结构化角色、能力覆盖、接口与约束；只有必要时使用 Ollama embedding 补充候选。embedding 不能覆盖结构化排除结果。

查询必须显式传入 `project_id`。`allow_global=False` 是默认值；只有用户明确允许时才合并 GLOBAL，且当前项目同名数据优先。当前项目库存过滤不能读取其他项目库存。

## 配置与数量

装备候选不等于批准配置。场景角色匹配通过后，数量只能来自明确配置规则或用户输入。求解结果保存规则ID、公式描述、输入值、约束检查、警告和来源。没有规则时数量为 `null` 并要求人工确认。

导入报告包含总行数、成功、跳过、重复、失败、缺少名称、字段覆盖和前20条错误。UI 的“装备数据”页可以查看原始 JSONL 行来源。
