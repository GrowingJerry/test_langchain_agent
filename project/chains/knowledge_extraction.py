"""Grounded structured-output chain for project domain knowledge extraction."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import ValidationError

from config.settings import Settings, settings as default_settings
from domain.exceptions import StructuredOutputError
from domain.schemas.knowledge import KnowledgeExtractionOutput
from infrastructure.llm.model_factory import OllamaModelFactory


def knowledge_extraction_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是项目领域知识抽取器。只从提供的候选片段抽取事实，不得补充常识。"
                "每条知识必须引用有效 source_chunk_ids，并分别给出适用和不适用条件。"
                "同名符号必须保留章节作用域。参数须保留单位、值类型和来源页；"
                "通用资料中的值不得宣称为当前项目参数。只有明确包含模型或物理量取值的"
                "事实才能标为parameter；名称格式、编号格式、编写规范和判定规则应标为"
                "constraint或verification_rule。最多输出8条最相关且不重复的知识。",
            ),
            (
                "human",
                "学习目标：{learning_goal}\n领域：{domain}\n仿真对象：{simulation_object}\n"
                "候选片段JSON：{candidates_json}",
            ),
        ]
    )


class KnowledgeExtractionChain:
    def __init__(
        self, settings: Settings = default_settings, model: Optional[object] = None
    ) -> None:
        self.settings = settings
        self._model = model

    def run(
        self,
        *,
        learning_goal: str,
        domain: str,
        simulation_object: str,
        candidates: List[Dict[str, Any]],
    ) -> KnowledgeExtractionOutput:
        if not candidates:
            raise StructuredOutputError("知识抽取失败：没有可追踪的覆盖候选")
        if self._model is None and not self.settings.enable_ollama:
            raise StructuredOutputError("知识抽取失败：Ollama 未启用")
        source = [
            {
                "chunk_id": row.get("chunk_id"),
                "document_id": row.get("document_id"),
                "section_scope": row.get("section_title") or row.get("parent_section_id"),
                "page_start": row.get("page_start"),
                "page_end": row.get("page_end"),
                "content": str(row.get("content") or "")[:2400],
            }
            for row in candidates
        ]
        try:
            model = self._model or OllamaModelFactory(self.settings).extraction_model()
            runnable = knowledge_extraction_prompt() | model.with_structured_output(
                KnowledgeExtractionOutput
            )
            raw = runnable.invoke(
                {
                    "learning_goal": learning_goal,
                    "domain": domain,
                    "simulation_object": simulation_object,
                    "candidates_json": json.dumps(source, ensure_ascii=False),
                }
            )
            output = (
                raw
                if isinstance(raw, KnowledgeExtractionOutput)
                else KnowledgeExtractionOutput.model_validate(raw)
            )
            if not output.knowledge_units:
                raise StructuredOutputError("知识抽取失败：结构化输出不包含知识单元")
            return output
        except StructuredOutputError:
            raise
        except (ValidationError, ValueError, TypeError) as exc:
            raise StructuredOutputError(f"知识结构化输出校验失败：{exc}") from exc
        except Exception as exc:
            raise StructuredOutputError(f"知识抽取调用失败：{type(exc).__name__}: {exc}") from exc
