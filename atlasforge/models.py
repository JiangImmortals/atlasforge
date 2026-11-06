"""模型接入层：复用成熟 SSE 客户端，模型与本地进程生命周期解耦。"""
from atlasforge.inference.config import RuntimeConfig
from atlasforge.inference.client import LlamaClient
from atlasforge.inference.server import LlamaServerManager
from atlasforge.paths import DATA, resolve
from atlasforge.resource_policy import MODEL_IDLE_SECONDS
from contextlib import contextmanager
import threading
import time
import os
import uuid


def _pid_alive(pid):
    """Windows 上仅查询进程句柄，绝不通过 os.kill 探测。"""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5  # 权限不足时保守地认为仍在运行。
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


class ModelService:
    def __init__(self, settings):
        self.settings = settings
        executable = resolve(settings.server_path)
        from urllib.parse import urlparse
        endpoint = urlparse(settings.base_url)
        if settings.backend == "managed" and (endpoint.hostname not in ("localhost", "127.0.0.1") or not endpoint.port):
            raise ValueError("内置服务地址必须为带端口的本机回环地址")
        cfg = RuntimeConfig(
            llm_backend="api", api_base_url=settings.base_url,
            api_model=settings.model, api_api_key=settings.api_key,
            api_timeout_s=120, minicpm_model_path=resolve(settings.model_path),
            minicpm_mmproj_path=resolve(settings.projector_path),
            llama_dir=executable.parent, llama_ctx_size=settings.context_size,
            llama_threads=settings.threads, log_dir=DATA / "logs",
            llama_host=endpoint.hostname or "127.0.0.1", llama_port=endpoint.port or 8123,
            server_executable=executable, use_projector=False,
        )
        self.client = LlamaClient(cfg)
        self._lock = threading.RLock()
        self._released = threading.Condition(self._lock)
        self._active = 0
        self._last_used = time.monotonic()
        self._closed = False
        self._idle_timer = None
        self._vision = False
        self._lease_dir = DATA / 'model-leases' / str(endpoint.port or 8123)
        # managed 后端固定回环地址；外部后端从不启动、停止用户的服务。
        self.manager = LlamaServerManager(cfg) if settings.backend == "managed" else None

    def start(self):
        """兼容入口；仅显式调用才加载，应用启动不调用此方法。"""
        with self.lease():
            pass

    @contextmanager
    def lease(self, vision=False, wait=False):
        """后台请求租约：只管理自身进程，整轮任务结束后计时回收。

        视觉升级只允许在没有活动任务时切换；拒绝切断正在运行的请求。
        外部服务生命周期始终由用户管理。
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("模型服务已关闭")
            if self.manager:
                if vision and not self._vision:
                    deadline = time.monotonic() + 120
                    while wait and self._active and not self._closed:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise RuntimeError("等待本地文本任务结束超时，请稍后重试图片任务")
                        self._released.wait(min(remaining, .2))
                    if self._closed:
                        raise RuntimeError("模型服务已关闭")
                    if self._active or any(_pid_alive(int(p.name.split('-')[0])) for p in self._lease_dir.glob('*.lease')):
                        raise RuntimeError("本地文本任务运行中，请完成后再发送图片")
                    if not self.settings.projector_path:
                        raise RuntimeError("未配置本地视觉投影模型")
                    if self.manager.probe() and not self.manager.is_owned:
                        raise RuntimeError("端口被外部服务占用，无法自动切换视觉模型")
                    self.manager.stop()
                    self.manager.cfg.use_projector = True
                    self._vision = True
                self.manager.start()
            self._active += 1
            marker = None
            if self.manager:
                self._lease_dir.mkdir(parents=True, exist_ok=True)
                marker = self._lease_dir / f'{os.getpid()}-{uuid.uuid4().hex}.lease'
                marker.touch()
        try:
            yield self
        finally:
            with self._lock:
                self._active -= 1
                self._released.notify_all()
                if marker:
                    marker.unlink(missing_ok=True)
                self._last_used = time.monotonic()
                if self.manager and not self._closed:
                    if self._idle_timer:
                        self._idle_timer.cancel()
                    self._idle_timer = threading.Timer(MODEL_IDLE_SECONDS, self.release_idle)
                    self._idle_timer.daemon = True
                    self._idle_timer.start()

    def release_idle(self, idle_seconds=MODEL_IDLE_SECONDS):
        """两分钟无请求后卸载权重、KV 和提示缓存；下次请求重新启动。"""
        with self._lock:
            if self._active or time.monotonic() - self._last_used < idle_seconds:
                return False
            # 原版交互终端和 GUI 可能共用端口；跨进程租约保护它们的任务。
            for marker in self._lease_dir.glob('*.lease'):
                if _pid_alive(int(marker.name.split('-')[0])):
                    self._idle_timer = threading.Timer(30, self.release_idle)
                    self._idle_timer.daemon = True
                    self._idle_timer.start()
                    return False
                marker.unlink(missing_ok=True)
            stopped = bool(self.manager and self.manager.stop())
            if self.manager:
                self.manager.cfg.use_projector = False
            self._vision = False
            return stopped

    def stop(self):
        # 启动健康检查可能持有锁；先发取消，避免退出界面等待整个启动超时。
        if self.manager:
            self.manager.cancel_startup()
        with self._lock:
            self._closed = True
            self._released.notify_all()
            if self._idle_timer:
                self._idle_timer.cancel()
            if self.manager:
                self.manager.stop()

    def complete(self, prompt, cancel=None, max_tokens=512, response_format=None):
        return "".join(self.stream_complete(prompt, cancel, max_tokens, response_format))

    def stream_complete(self, prompt, cancel=None, max_tokens=192, response_format=None):
        if cancel is not None and cancel.is_set():
            return
        with self.lease():
            if cancel is not None and cancel.is_set():
                return
            yield from self.client.stream_chat([{"role": "user", "content": prompt}],
                max_tokens=max_tokens, cancel=cancel, response_format=response_format)
