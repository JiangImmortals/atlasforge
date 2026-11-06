"""统一后台工作线程，界面不直接执行推理、网络和进程等待。"""
from PySide6.QtCore import QThread, Signal
import threading


class Job(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    def run(self):
        try:
            self.done.emit(self.operation())
        except Exception as exc:
            self.failed.emit(str(exc))


class AgentJob(QThread):
    event = Signal(object)
    failed = Signal(str)

    def __init__(self, session, prompt, mode="chat", model_service=None):
        super().__init__()
        self.session, self.prompt = session, prompt
        self.mode = mode
        self.model_service = model_service
        self.cancelled = threading.Event()

    def run(self):
        try:
            from contextlib import nullcontext
            def contains_image(value):
                if isinstance(value, dict):
                    return value.get('type') in ('image', 'image_url') or any(contains_image(v) for v in value.values())
                return isinstance(value, list) and any(contains_image(v) for v in value)
            vision = bool(self.session.pending_images) or contains_image(self.session.messages)
            if self.cancelled.is_set():
                return
            self.event.emit({'type':'status', 'text':'正在准备模型服务（首次使用按需加载）…'})
            lease = self.model_service.lease(vision=vision, wait=True) if self.model_service else nullcontext()
            # Pi 和 AIpy 整个任务期间持有租约，空闲回收不能中断工具循环。
            with lease:
                if not self.cancelled.is_set():
                    self.session.run(self.prompt, self.event.emit, mode=self.mode)
        except Exception as exc:
            self.failed.emit(str(exc))

    def cancel(self):
        self.cancelled.set()
        self.session.cancel()
