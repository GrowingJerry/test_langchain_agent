"""Domain models and errors independent from UI and infrastructure."""

from domain.exceptions import (
    AgentCallLimitError,
    AgentExecutionError,
    ConfigurationError,
    ModelUnavailableError,
    PersistenceError,
    ProjectScopeError,
    RetrievalError,
    StructuredOutputError,
)

__all__ = [
    "AgentCallLimitError",
    "AgentExecutionError",
    "ConfigurationError",
    "ModelUnavailableError",
    "PersistenceError",
    "ProjectScopeError",
    "RetrievalError",
    "StructuredOutputError",
]
