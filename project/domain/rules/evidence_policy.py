"""One policy for selecting HTML evidence by test type."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class EvidencePolicy:
    test_type: str
    html_level: str
    include_elements: bool
    include_observations: bool
    limitations: tuple[str, ...] = ()

    @classmethod
    def for_test_type(cls, test_type: str, settings: Any) -> "EvidencePolicy":
        name = (test_type or "功能测试").strip()
        mapping = {
            "功能测试": (settings.html_evidence_functional, True, True, ()),
            "界面测试": (settings.html_evidence_ui, True, True, ()),
            "易用性测试": (settings.html_evidence_usability, True, True, ()),
            "兼容性测试": (settings.html_evidence_compatibility, True, True, ()),
            "安全测试": (settings.html_evidence_security, True, True, ("HTML只证明攻击入口；服务端鉴权、注入和持久化安全待联机验证",)),
            "性能测试": (settings.html_evidence_performance, True, False, ("HTML只用于事务入口和操作路径；性能指标不得由HTML推断",)),
            "接口测试": (settings.html_evidence_interface, False, True, ("普通HTML元素默认不作为接口证据；缺少接口或网络证据时信息不足",)),
            "可靠性测试": (settings.html_evidence_reliability, True, False, ("HTML只用于触发入口",)),
        }
        level, elements, observations, limitations = mapping.get(name, mapping["功能测试"])
        if level == "off":
            elements = False
        return cls(name, level, elements, observations, limitations)

    def as_dict(self) -> dict[str, Any]:
        return {"test_type": self.test_type, "html_level": self.html_level,
                "include_elements": self.include_elements,
                "include_observations": self.include_observations,
                "limitations": list(self.limitations)}
