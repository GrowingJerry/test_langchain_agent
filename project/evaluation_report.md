# Scenario Pipeline Evaluation

Overall: **PASS**

## Metrics

| Metric | Value |
|---|---:|
| scenario_field_completeness | 0.992857 |
| project_fact_source_coverage | 1.000000 |
| unsupported_fact_rate | 0.000000 |
| equipment_role_coverage | 0.700000 |
| quantity_rule_traceability | 1.000000 |
| ungrounded_quantity_fill_rate | 0.000000 |
| parameter_unit_condition_completeness | 1.000000 |
| cross_project_leak_rate | 0.000000 |
| blocking_issue_recall | 1.000000 |
| book_override_rate | 0.000000 |
| step_expected_result_alignment | 1.000000 |
| repeat_run_stability | 1.000000 |
| human_modified_field_ratio | 0.023077 |
| source_reference_validity | 1.000000 |

## Hard thresholds

| Metric | Required | Result |
|---|---:|---|
| cross_project_leak_rate | 0.000000 | PASS |
| ungrounded_quantity_fill_rate | 0.000000 | PASS |
| book_override_rate | 0.000000 | PASS |
| source_reference_validity | 1.000000 | PASS |

## Golden cases

| Case | Category | Expected blockers | Detected blockers |
|---|---|---|---|
| normal-complete | 信息完整的正常场景 | - | - |
| missing-quantity-rule | 缺少装备数量规则 | - | - |
| capability-mismatch | 装备能力不满足 | equipment_role_consistency | equipment_role_consistency |
| inventory-shortage | 库存不足 | inventory_limit | inventory_limit |
| book-project-conflict | 项目资料与书籍参数冲突 | - | - |
| same-symbol-scopes | 多个同名符号 | - | - |
| scanned-pdf-gap | 扫描PDF缺少内容 | missing_source_content | missing_source_content |
| cross-project-attempt | 跨项目检索尝试 | project_scope_attempt | project_scope_attempt |
| environment-disturbance | 环境扰动场景 | - | - |
| fault-degraded-recovery | 故障降级与恢复场景 | - | - |
