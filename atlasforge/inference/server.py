"""llama-server 生命周期管理。

要点：
- ``--reasoning off`` 必须作为**启动参数**传入；写进请求体会被忽略（旧项目踩过）。
- 端口上已有可用实例时直接复用，不重复拉起。
- 退出顺序：terminate → 等待 → kill，避免残留进程。
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager

from .config import RuntimeConfig as Settings, get_config
from atlasforge.resource_policy import PROMPT_CACHE_MIB, INFERENCE_BATCH, INFERENCE_MICROBATCH

logger = logging.getLogger(__name__)

HEALTH_POLL_INTERVAL_S = 0.5
HEALTH_MAX_POLLS = 240


class LlamaServerError(RuntimeError):
    """llama-server 启动或运行异常。"""


class LlamaServerManager:
    """按需拉起并复用本地 llama-server。"""

    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or get_config()
        self._proc: subprocess.Popen[bytes] | None = None
        self._owned = False
        self._cancel_start = False

    # ---------- 启动参数 ----------
    def build_args(self) -> list[str]:
        """构造 llama-server 命令行。"""
        cfg = self.cfg
        args = [
            str(cfg.llama_server_exe),
            "-m",
            str(cfg.minicpm_model_path),
            "--mmproj",
            str(cfg.minicpm_mmproj_path),
            "--host",
            cfg.llama_host,
            "--port",
            str(cfg.llama_port),
            "-c",
            str(cfg.llama_ctx_size),
            "-t",
            str(cfg.llama_threads),
            "--parallel",
            "1",
            # 关闭思考模式：只能在这里生效
            "--reasoning",
            cfg.llama_reasoning,
            "--jinja",
            "--no-webui",
            "--no-warmup",
            "--cache-ram", str(PROMPT_CACHE_MIB),
            "-b", str(INFERENCE_BATCH), "-ub", str(INFERENCE_MICROBATCH),
        ]
        if not cfg.use_projector:
            pos = args.index("--mmproj")
            del args[pos:pos + 2]
        return args

    # ---------- 健康检查 ----------
    @property
    def health_url(self) -> str:
        """健康检查地址。"""
        return f"{self.cfg.llama_base_url}/health"

    def probe(self, timeout: float = 1.5) -> bool:
        """探测端口上是否已有就绪实例。"""
        try:
            with urllib.request.urlopen(self.health_url, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, OSError, ValueError):
            return False
        return body.get("status") == "ok"

    # ---------- 启动 / 停止 ----------
    def start(self, wait: bool = True) -> float:
        """确保服务可用。返回就绪耗时秒数（复用已有实例时为 0）。"""
        if self._cancel_start:
            raise LlamaServerError("模型服务启动已取消")
        if self.probe():
            # 重复 start 不丢失自身进程的所有权；外部实例才标为借用。
            self._owned = self._proc is not None and self._proc.poll() is None
            logger.info("复用已运行的 llama-server: %s", self.cfg.llama_base_url)
            return 0.0

        cfg = self.cfg
        if not cfg.llama_server_exe.is_file():
            raise LlamaServerError(f"缺少 llama-server: {cfg.llama_server_exe}")
        if not cfg.minicpm_model_path.is_file():
            raise LlamaServerError(f"缺少模型: {cfg.minicpm_model_path}")

        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = cfg.log_dir / "llama-server.log"
        started = time.perf_counter()
        if self._cancel_start:
            raise LlamaServerError("模型服务启动已取消")
        with log_path.open("ab") as log_file:
            self._proc = subprocess.Popen(
                self.build_args(),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(cfg.llama_dir),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        self._owned = True
        logger.info("已拉起 llama-server pid=%s", self._proc.pid)

        if not wait:
            return 0.0

        for _ in range(HEALTH_MAX_POLLS):
            if self._cancel_start:
                self.stop()
                raise LlamaServerError("模型服务启动已取消")
            if self._proc.poll() is not None:
                code = self._proc.returncode
                self.stop()
                raise LlamaServerError(f"llama-server 提前退出（退出码 {code}），日志: {log_path}")
            if self.probe():
                return time.perf_counter() - started
            time.sleep(HEALTH_POLL_INTERVAL_S)

        self.stop()
        raise LlamaServerError(
            f"llama-server 在 {HEALTH_MAX_POLLS * HEALTH_POLL_INTERVAL_S:.0f}s 内未就绪，日志: {log_path}"
        )

    def cancel_startup(self):
        """用于用户在加载期间退出；不影响借用的外部服务。"""
        self._cancel_start = True

    def stop(self, grace_s: float = 10.0) -> bool:
        """停止由本实例拉起的服务。返回是否真正停止了一个进程。"""
        proc, owned = self._proc, self._owned
        self._proc = None
        self._owned = False
        if proc is None or not owned or proc.poll() is not None:
            return False
        proc.terminate()
        try:
            proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            logger.warning("llama-server 未在 %.0fs 内退出，强制结束", grace_s)
            proc.kill()
            proc.wait(timeout=grace_s)
        logger.info("已停止 llama-server pid=%s", proc.pid)
        return True

    @property
    def is_owned(self) -> bool:
        """当前实例是否由本对象拉起。"""
        return self._owned

    @contextmanager
    def running(self) -> Iterator[LlamaServerManager]:
        """上下文管理：退出时只停止自己拉起的进程。"""
        self.start()
        try:
            yield self
        finally:
            self.stop()
