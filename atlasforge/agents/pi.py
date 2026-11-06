"""Pi 进程适配：UTF-8 JSONL 协议、取消进程树、保留会话与子任务产物。"""
import json
import subprocess
import threading
import os
from pathlib import Path
from dataclasses import asdict
from atlasforge.paths import ROOT, DATA, NODE, PYTHON


def stop_process_tree(process):
    if process and process.poll() is None:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


class PiSession:
    def __init__(self, settings):
        self.settings = settings
        self.messages = []
        self.session_file = None
        self.process = None
        self.pending_images = []
        self.active_tasks = set()

    def cancel(self):
        stop_process_tree(self.process)
        self._finish_active_tasks("cancelled")

    def _finish_active_tasks(self, state):
        from datetime import datetime, timezone
        for task_id in list(self.active_tasks):
            path = DATA / "tasks" / task_id / "task.json"
            if path.is_file():
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("state") == "running":
                    record.update(state=state, finished=datetime.now(timezone.utc).isoformat())
                    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        self.active_tasks.clear()

    def run(self, prompt, on_event, mode="chat"):
        from atlasforge.agents.engines import selection, adapter_path, environment as engine_environment
        active = selection()
        environment = engine_environment(active)
        bash = ROOT / "runtime/git/bin/bash.exe"
        if bash.exists():
            environment["PI_BASH_PATH"] = str(bash)
        environment["PATH"] = str(PYTHON.parent) + os.pathsep + environment.get("PATH", "")
        self.process = subprocess.Popen(
            [str(NODE), str(adapter_path(active))], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW, env=environment,
        )
        errors = []
        reader = threading.Thread(target=lambda: errors.append(self.process.stderr.read()), daemon=True)
        reader.start()
        session_file = None
        if self.session_file:
            candidate = ROOT / self.session_file
            if not candidate.exists():
                candidate = DATA / 'pi/sessions' / Path(self.session_file).name
            if candidate.exists():
                session_file = str(candidate)
        self.process.stdin.write(json.dumps({"config": asdict(self.settings), "root": str(ROOT),
            "images": self.pending_images, "mode": mode, "session_file": session_file,
            "python": str(PYTHON), "prompt": prompt, "messages": self.messages}, ensure_ascii=False) + "\n")
        self.pending_images = []
        self.process.stdin.close()
        completed = False
        for line in self.process.stdout:
            event = json.loads(line)
            if event["type"] == "task":
                self.active_tasks.add(event["id"])
            elif event["type"] == "task_end":
                self.active_tasks.discard(event["id"])
            if event["type"] == "done":
                self.messages = event["messages"]
                native_file = event.get('session_file')
                self.session_file = str(Path(native_file).relative_to(ROOT)) if native_file else None
                completed = True
            on_event(event)
        code = self.process.wait()
        reader.join(3)
        if code and not completed:
            self._finish_active_tasks("failed")
            raise RuntimeError(("".join(errors))[-2000:] or "Agent 已取消或运行失败")
