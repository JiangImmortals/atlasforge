"""本地 llama-server 的 OpenAI 兼容流式客户端（仅标准库）。

- 手写 SSE 解析，不依赖 requests / httpx。
- 逐块检查取消标志，支持打断。
- 支持 ``image_url`` data URI，为后续多模态阶段预留。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import RuntimeConfig as Settings, get_config


class LlamaClientError(RuntimeError):
    """请求 llama-server 失败。"""


@dataclass
class ChatStats:
    """单次流式对话的实测指标。"""

    first_token_s: float | None = None
    total_s: float = 0.0
    chars: int = 0
    #: 上下文本轮实际占用的 token 数（来自服务端 usage，需 stream_options）
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def chars_per_s(self) -> float:
        """生成速率（字符/秒）。"""
        return self.chars / self.total_s if self.total_s > 0 else 0.0


def encode_image(path: Path) -> str:
    """把本地图片编码为 OpenAI 兼容的 data URI。"""
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def user_message(text: str, image: Path | None = None) -> dict[str, Any]:
    """构造用户消息；带图时使用多模态 content 数组。"""
    if image is None:
        return {"role": "user", "content": text}
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": encode_image(image)}},
        ],
    }


class LlamaClient:
    """OpenAI 兼容流式对话客户端：本地 llama-server 或远程大模型 API。"""

    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or get_config()

    @property
    def using_api(self) -> bool:
        """是否走远程 API（否则本地 llama-server）。"""
        return str(self.cfg.llm_backend).strip().lower() == "api"

    @property
    def chat_url(self) -> str:
        """OpenAI 兼容对话端点。"""
        if self.using_api:
            base = (self.cfg.api_base_url or "").strip().rstrip("/")
            if base.endswith("/chat/completions"):
                return base
            return f"{base}/chat/completions"
        return f"{self.cfg.llama_base_url}/v1/chat/completions"

    @property
    def model(self) -> str:
        """请求里带的模型名；本地服务器忽略该字段，远程必须填。"""
        return self.cfg.api_model if self.using_api else ""

    @property
    def timeout_s(self) -> float:
        return (
            float(self.cfg.api_timeout_s)
            if self.using_api
            else float(self.cfg.llama_request_timeout_s)
        )

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 256,
        temperature: float = 0.0,
        cancel: threading.Event | None = None,
        response_format: dict | None = None,
    ) -> ChatStream:
        """发起流式对话，返回可迭代对象（同时携带实测指标）。"""
        return ChatStream(self, messages, max_tokens, temperature, cancel, response_format=response_format)

    def _request(self, payload: dict[str, Any]) -> urllib.request.Request:
        headers = {"Content-Type": "application/json"}
        key = (self.cfg.api_api_key or "").strip()
        if self.using_api and key:
            headers["Authorization"] = f"Bearer {key}"
        return urllib.request.Request(
            self.chat_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 256,
        temperature: float = 0.0,
        cancel: threading.Event | None = None,
    ) -> tuple[str, ChatStats]:
        """非流式便捷封装：一次性拿完整答案与指标。"""
        stream = self.stream_chat(messages, max_tokens, temperature, cancel)
        return stream.collect(), stream.stats


@dataclass
class ChatStream:
    """流式对话迭代器，附带实测指标。"""

    client: LlamaClient
    messages: list[dict[str, Any]]
    max_tokens: int = 256
    temperature: float = 0.0
    cancel: threading.Event | None = None
    stats: ChatStats = field(default_factory=ChatStats)
    response_format: dict | None = None
    _chunks: Iterator[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._chunks = self._iter_chunks()

    def __iter__(self) -> Iterator[str]:
        return self._chunks

    def __next__(self) -> str:
        return next(self._chunks)

    def collect(self) -> str:
        """读完整个流并拼接。"""
        return "".join(self)

    def _iter_chunks(self) -> Iterator[str]:
        payload: dict[str, Any] = {
            "messages": self.messages,
            "stream": True,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if self.response_format:
            payload["response_format"] = self.response_format
        if self.client.using_api:
            # 远程 API：模型名必填；llama 专属参数不传（部分服务会拒收未知字段）
            if self.client.model:
                payload["model"] = self.client.model
        else:
            payload["cache_prompt"] = True
            # 让 llama-server 在流末尾回传 usage，用于前端显示上下文占用
            payload["stream_options"] = {"include_usage": True}
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(
                self.client._request(payload),
                timeout=self.client.timeout_s,
            ) as resp:
                for raw in resp:
                    if self.cancel is not None and self.cancel.is_set():
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        # include_usage 的末尾块：只有 usage，没有 choices
                        usage = obj.get("usage")
                        if usage:
                            self.stats.prompt_tokens = usage.get("prompt_tokens")
                            self.stats.completion_tokens = usage.get("completion_tokens")
                        continue
                    delta = (choices[0].get("delta") or {}).get("content")
                    if not delta:
                        continue
                    if self.stats.first_token_s is None:
                        self.stats.first_token_s = time.perf_counter() - started
                    self.stats.chars += len(delta)
                    yield delta
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise LlamaClientError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            target = "API" if self.client.using_api else "llama-server"
            raise LlamaClientError(f"无法连接 {target}: {exc.reason}") from exc
        finally:
            self.stats.total_s = time.perf_counter() - started
