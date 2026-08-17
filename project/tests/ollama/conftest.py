import os, pytest
from config.settings import settings
from infrastructure.llm.ollama_health import OllamaHealthClient

@pytest.fixture(scope="session")
def live_ollama():
    if os.getenv("RUN_OLLAMA_TESTS") != "1": pytest.skip("RUN_OLLAMA_TESTS不是1；真实模型测试仅显式运行")
    health=OllamaHealthClient(settings)
    if not health.is_available(): pytest.skip(f"Ollama服务不可用：{settings.ollama_base_url}")
    if not health.model_exists(settings.ollama_model): pytest.skip(f"模型未安装：{settings.ollama_model}")
    return settings
