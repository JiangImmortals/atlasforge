"""统一配置；模型、推理服务、输入助手各自声明参数，密钥只在本机保存。"""
import json
from dataclasses import asdict, dataclass, field
from atlasforge.paths import DATA
from atlasforge.agents.prompts import MAIN_PROMPT, SUB_PROMPT


@dataclass
class Settings:
    backend: str = "external"
    model_path: str = "models/minicpm/MiniCPM-V-4_7-1B-Q5_K_M.gguf"
    projector_path: str = "models/minicpm/mmproj-model-f16.gguf"
    server_path: str = "runtime/llama/llama-server.exe"
    base_url: str = "http://127.0.0.1:8123/v1"
    model: str = "local-minicpm"
    api_key: str = ""
    context_size: int = 8192
    threads: int = 4
    vision: bool = True
    close_action: str = "minimize"
    main_system_prompt: str = MAIN_PROMPT
    sub_system_prompt: str = SUB_PROMPT
    subagent_retries: int = 5
    engine_auto_update: bool = False
    pi_provider: str = "portable"
    aipy_provider: str = "openai"

    def __post_init__(self):
        if self.backend not in ("managed", "external", "cloud"):
            raise ValueError("未知推理后端")
        if self.close_action not in ("minimize", "tray", "exit"):
            raise ValueError("未知关闭窗口行为")
        if not isinstance(self.subagent_retries, int) or not 1 <= self.subagent_retries <= 5:
            raise ValueError("子 Agent 最多重做次数应为 1～5")
        if not all(isinstance(text, str) and text.strip() for text in (self.main_system_prompt, self.sub_system_prompt)):
            raise ValueError("主 Agent 和子 Agent 提示词不能为空")
        for value, low, high in ((self.context_size, 2048, 262144), (self.threads, 1, 64)):
            if not isinstance(value, int) or not low <= value <= high:
                raise ValueError("上下文、线程或输入延时超出允许范围")

    @classmethod
    def load(cls):
        path = DATA / "settings.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (ValueError, TypeError, AttributeError) as exc:
            import logging
            from time import time_ns
            backup = path.with_name(f"settings.invalid-{time_ns()}.json")
            backup.write_bytes(path.read_bytes())
            logging.getLogger(__name__).warning("配置无效，已保存备份 %s：%s", backup, exc)
            result = cls()
            result.load_warning = "设置文件无效，已保留备份并使用本地默认配置"
            return result

    def save(self):
        DATA.mkdir(parents=True, exist_ok=True)
        target = DATA / "settings.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
