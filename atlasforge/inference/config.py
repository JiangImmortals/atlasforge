"""推理内部参数，不读取旧应用 .env，不依赖 GUI 或 pydantic。"""
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RuntimeConfig:
    llm_backend: str = "api"
    api_base_url: str = "http://127.0.0.1:8123/v1"
    api_model: str = "local-minicpm"
    api_api_key: str = ""
    api_timeout_s: float = 15
    minicpm_model_path: Path = Path()
    minicpm_mmproj_path: Path = Path()
    llama_dir: Path = Path()
    llama_host: str = "127.0.0.1"
    llama_port: int = 8123
    llama_ctx_size: int = 8192
    llama_threads: int = 4
    llama_reasoning: str = "off"
    llama_request_timeout_s: float = 120
    log_dir: Path = Path("data/logs")
    server_executable: Path | None = None
    use_projector: bool = True

    @property
    def llama_server_exe(self):
        return self.server_executable or self.llama_dir / "llama-server.exe"

    @property
    def llama_base_url(self):
        return f"http://{self.llama_host}:{self.llama_port}"


def get_config():
    return RuntimeConfig()
