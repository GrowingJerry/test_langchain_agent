"""Domain-specific exceptions used across application boundaries."""


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is invalid or missing."""


class ModelUnavailableError(RuntimeError):
    """Raised when the configured local model cannot serve a request."""


class StructuredOutputError(ValueError):
    """Raised when model output cannot be validated as the requested schema."""


class ProjectScopeError(PermissionError):
    """Raised when an operation attempts to escape its bound project scope."""


class RetrievalError(RuntimeError):
    """Raised when project-scoped retrieval cannot complete safely."""


class PersistenceError(RuntimeError):
    """Raised when a repository cannot persist or restore domain data."""


class AgentExecutionError(RuntimeError):
    """Raised when test-case Agent execution fails and the service should fallback."""


class AgentCallLimitError(AgentExecutionError):
    """Raised when a finite model or tool call limit is exceeded."""
