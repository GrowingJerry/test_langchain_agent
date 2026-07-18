"""Extract grounded project usage scenarios from project chunks."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from chains.scenario_extraction import ScenarioExtractionChain
from config.settings import settings
from core.project_manager import ProjectManager, new_id
from models.schemas import ScenarioCard


def _as_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if value:
        return [x.strip() for x in re.split(r"[、,，;；\n]+", str(value)) if x.strip()]
    return []


def _related_requirements(
    content: str, requirements: List[Dict[str, Any]], chunk_id: str
) -> List[str]:
    direct = [
        r["requirement_id"]
        for r in requirements
        if r.get("source_chunk_id") == chunk_id
    ]
    if direct:
        return direct
    chars = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_-]+", content))
    scored = []
    for req in requirements:
        text = str(req.get("description") or req.get("title") or "")
        overlap = len(
            chars & set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_-]+", text))
        )
        if overlap:
            scored.append((overlap, req["requirement_id"]))
    return [rid for _, rid in sorted(scored, reverse=True)[:3]]


def _fallback_cards(
    project_id: str, chunks: List[Dict[str, Any]], requirements: List[Dict[str, Any]]
) -> List[ScenarioCard]:
    cards: List[ScenarioCard] = []
    markers = (
        "当",
        "用户",
        "操作员",
        "系统",
        "接口",
        "收到",
        "发送",
        "启动",
        "运行",
        "异常",
        "场景",
    )
    for chunk in chunks:
        content = re.sub(r"\s+", " ", str(chunk.get("content") or "")).strip()
        if len(content) < 12 or not any(k in content for k in markers):
            continue
        sentences = [x.strip() for x in re.split(r"(?<=[。；;])", content) if x.strip()]
        related = _related_requirements(
            content, requirements, str(chunk.get("chunk_id") or "")
        )
        interfaces = [
            x
            for x in sentences
            if any(
                k in x for k in ("接口", "API", "HTTP", "TCP", "UDP", "报文", "协议")
            )
        ][:3]
        abnormal = [
            x
            for x in sentences
            if any(k in x for k in ("异常", "失败", "超时", "无效", "中断", "错误"))
        ][:3]
        boundary = [
            x
            for x in sentences
            if any(
                k in x
                for k in ("最大", "最小", "边界", "不低于", "不高于", "至少", "至多")
            )
        ][:3]
        performance = [
            x
            for x in sentences
            if any(k in x for k in ("性能", "响应", "并发", "吞吐", "时延"))
        ][:3]
        safety = [
            x
            for x in sentences
            if any(k in x for k in ("安全", "权限", "认证", "加密", "审计"))
        ][:3]
        actors = [
            x for x in ("用户", "操作员", "管理员", "外部系统", "设备") if x in content
        ]
        trigger = next(
            (
                x
                for x in sentences
                if x.startswith("当") or any(k in x for k in ("触发", "收到", "启动"))
            ),
            "",
        )
        inputs = [
            x
            for x in sentences
            if any(k in x for k in ("输入", "数据", "文件", "报文", "参数"))
        ][:3]
        states = [
            x
            for x in sentences
            if any(k in x for k in ("状态", "待命", "运行中", "停止", "故障"))
        ][:3]
        normal = [x for x in sentences if x not in abnormal][:5]
        name_seed = normal[0] if normal else content
        cards.append(
            ScenarioCard(
                scenario_id=new_id("SCN"),
                project_id=project_id,
                scenario_name=name_seed[:42] + ("..." if len(name_seed) > 42 else ""),
                scenario_type="异常场景"
                if abnormal
                else ("接口场景" if interfaces else "业务场景"),
                related_requirements=related,
                actors=actors,
                preconditions=sentences[:1],
                trigger_event=trigger,
                input_data=inputs,
                system_state="；".join(states) if states else "资料未明确时需人工确认",
                external_interfaces=interfaces,
                environment=[
                    x
                    for x in sentences
                    if any(k in x for k in ("环境", "网络", "平台", "终端"))
                ][:3],
                normal_flow=normal,
                abnormal_flow=abnormal,
                boundary_conditions=boundary,
                performance_constraints=performance,
                safety_constraints=safety,
                source_document=[str(chunk.get("filename") or "")],
                source_chunk_ids=[str(chunk.get("chunk_id") or "")],
                confidence=0.72 if related else 0.55,
                need_human_confirm=not bool(related and trigger and inputs),
            )
        )
        if len(cards) >= 30:
            break
    return cards


def extract_and_save_scenario_cards(
    manager: ProjectManager,
    project_id: str,
    ollama: Optional[Any] = None,
    use_ollama: bool = True,
) -> List[Dict[str, Any]]:
    """Extract, validate, and persist project-isolated scenario cards."""
    chunks = manager.list_chunks(project_id, limit=2000)
    requirements = manager.list_requirements(project_id)
    runtime_settings = (
        settings if use_ollama else settings.model_copy(update={"enable_ollama": False})
    )
    result = ScenarioExtractionChain(runtime_settings).run(
        project_id,
        chunks,
        requirements,
        _fallback_cards,
    )
    payload = []
    for card in result.scenario_cards:
        row = card.model_dump()
        row.update(
            {
                "generation_mode": result.generation_mode,
                "failure_type": result.failure_type,
                "failure_message": result.failure_message,
            }
        )
        payload.append(row)
    manager.replace_full_scenario_cards(project_id, payload)
    return payload
