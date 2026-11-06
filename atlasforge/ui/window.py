"""独立 Agent 工作台：会话、AIpy 委派与任务产物，不包含翻译或输入助手。"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from dataclasses import replace
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QTextCursor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QTabWidget, QTextBrowser, QPlainTextEdit,
    QListWidget, QListWidgetItem, QSplitter, QCheckBox, QSystemTrayIcon,
    QMenu, QStyle, QFileDialog, QMessageBox, QDialog, QDialogButtonBox)
from atlasforge.paths import DATA, ROOT
from atlasforge import __version__
from atlasforge.agents.pi import PiSession
from atlasforge.ui.workers import AgentJob, Job
from atlasforge.ui.settings_dialog import SettingsDialog


class Window(QMainWindow):
    exiting = Signal()

    def __init__(self, settings, models):
        super().__init__()
        self.settings, self.models = settings, models
        self.runtime_settings = settings
        self.agent = PiSession(settings)
        self.chat_job = None
        self.history_id = uuid.uuid4().hex
        self.transcript = []
        self.quitting = False
        self.deleted_session = None
        self.drafting = False
        self.setWindowTitle(f"AtlasForge · 擎界 · {__version__}")
        area = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1080, area.width() - 48), min(750, area.height() - 60))
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 14)
        header = QHBoxLayout()
        title = QLabel("AtlasForge · 擎界")
        title.setObjectName("heading")
        header.addWidget(title)
        header.addStretch()
        self.backend_label = QLabel(f"{'本地' if settings.backend != 'cloud' else '云端'} · {settings.model}")
        self.backend_label.setObjectName("muted")
        header.addWidget(self.backend_label)
        self.button(header, "设置", self.open_settings)
        layout.addLayout(header)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._agent_tab()
        self.setCentralWidget(content)
        self.statusBar().showMessage("正在准备本地服务…")
        self._tray()

    def button(self, layout, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _agent_tab(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        splitter = QSplitter()
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        self.button(side, "＋ 新对话", self.new_session)
        self.sessions = QListWidget()
        self.sessions.itemClicked.connect(self.load_session)
        self.sessions.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sessions.customContextMenuRequested.connect(self.session_menu)
        side.addWidget(self.sessions)
        history_actions = QHBoxLayout()
        self.button(history_actions, "删除会话", self.delete_session)
        self.undo_delete = self.button(history_actions, "撤销删除", self.restore_deleted_session)
        self.undo_delete.setEnabled(False)
        side.addLayout(history_actions)
        side.addWidget(QLabel("子任务执行记录"))
        self.task_list = QListWidget()
        self.task_list.setMaximumHeight(180)
        self.task_list.itemDoubleClicked.connect(lambda item: os.startfile(str(DATA / "tasks" / item.data(Qt.UserRole))))
        self.task_items = {}
        side.addWidget(self.task_list)
        self.button(side, "查看子任务产物", self.open_tasks)
        splitter.addWidget(sidebar)
        chat = QWidget()
        main = QVBoxLayout(chat)
        self.conversation = QTextBrowser()
        self.conversation.setOpenExternalLinks(False)
        main.addWidget(self.conversation, 1)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("描述任务；输入 @AIpy 请子 Agent 代答。Ctrl+Enter 发送，草稿不会自动发出。")
        self.editor.textChanged.connect(self.offer_mention)
        self.editor.setMaximumHeight(110)
        main.addWidget(self.editor)
        row = QHBoxLayout()
        self.button(row, "添加文件", self.attach)
        self.button(row, "导出对话", self.export_chat)
        self.draft_button = self.button(row, "@AIpy 代答", self.request_draft)
        row.addStretch()
        self.stop_button = self.button(row, "停止", self.cancel_chat)
        self.send_button = self.button(row, "发送", self.send)
        self.stop_button.setEnabled(False)
        main.addLayout(row)
        splitter.addWidget(chat)
        splitter.setSizes([220, 800])
        outer.addWidget(splitter)
        self.tabs.addTab(page, "Agent 工作台")
        action = QAction(self)
        action.setShortcut("Ctrl+Return")
        action.triggered.connect(self.send)
        self.addAction(action)
        self.refresh_sessions()



    def _tray(self):
        icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("AtlasForge · 擎界")
        menu = QMenu()
        menu.addAction("打开工作台", self.show_normal)
        menu.addAction("设置", self.open_settings)
        menu.addSeparator()
        menu.addAction("退出", self.exit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_normal() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def show_normal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def agent_settings(self):
        """人格立即生效，模型进程相关配置保持本次启动时的值。"""
        return replace(self.runtime_settings, main_system_prompt=self.settings.main_system_prompt,
                       sub_system_prompt=self.settings.sub_system_prompt,
                       subagent_retries=self.settings.subagent_retries)

    def offer_mention(self):
        if self.editor.toPlainText().strip() == "@":
            menu = QMenu(self)
            menu.addAction("AIpy 子 Agent · 替老板拟答", lambda: self.editor.setPlainText("@AIpy "))
            self._mention_menu = menu
            menu.popup(self.editor.mapToGlobal(self.editor.cursorRect().bottomLeft()))

    def request_draft(self):
        if self.chat_job and self.chat_job.isRunning():
            return
        import re
        instruction = re.sub(r"^\s*@(?:aipy|子智能体|子agent)\s*", "", self.editor.toPlainText(), flags=re.I).strip()
        context = "\n\n".join(("老板" if entry["role"] == "user" else "主 Agent") + "：" + entry["text"]
                                for entry in self.transcript[-8:])[-5000:]
        if not context and not instruction:
            self.statusBar().showMessage("先进行对话或说明需要代答的内容，再使用 @AIpy")
            return
        prompt = ("请替老板拟一段回复主 Agent 的草稿。根据已有决定回答，未知事实不要编造。"
                  "只返回可以直接发送的第一人称文字，不加称呼和分析，不执行文件或命令。\n\n"
                  "对话背景：\n" + context + "\n\n老板补充要求：\n" + (instruction or "替我回答主 Agent 最近的问题"))
        self.drafting = True
        self._draft_text = ""
        self._answer = ""
        self.chat_job = AgentJob(PiSession(self.agent_settings()), prompt, mode="draft", model_service=self.models)
        self.chat_job.event.connect(self.agent_event)
        self.chat_job.failed.connect(self.chat_failed)
        self.chat_job.finished.connect(self.chat_finished)
        self.send_button.setEnabled(False)
        self.draft_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.chat_job.start()

    def review_draft(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("AIpy 代答草稿 · 确认后再发送")
        dialog.resize(640, 420)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(self._draft_text)
        layout.addWidget(text)
        actions = QDialogButtonBox(QDialogButtonBox.Cancel)
        insert = actions.addButton("插入输入框", QDialogButtonBox.AcceptRole)
        copy = actions.addButton("复制草稿", QDialogButtonBox.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text.toPlainText()))
        insert.clicked.connect(dialog.accept)
        actions.rejected.connect(dialog.reject)
        layout.addWidget(actions)
        if dialog.exec():
            self.editor.setPlainText(text.toPlainText())
            self.editor.setFocus()

    def send(self):
        text = self.editor.toPlainText().strip()
        if not text or self.chat_job and self.chat_job.isRunning():
            return
        if text.lstrip().lower().startswith(("@aipy", "@子智能体", "@子agent")):
            self.request_draft()
            return
        self.editor.clear()
        self.transcript.append({"role": "user", "text": text})
        self.conversation.moveCursor(QTextCursor.End)
        self.conversation.insertPlainText("\n老板\n" + text + "\n\n主 Agent\n")
        self._answer = ""
        self.chat_job = AgentJob(self.agent, text, model_service=self.models)
        self.chat_job.event.connect(self.agent_event)
        self.chat_job.failed.connect(self.chat_failed)
        self.chat_job.finished.connect(self.chat_finished)
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.chat_job.start()

    def agent_event(self, event):
        kind = event["type"]
        if kind == "delta":
            self._answer += event["text"]
            self.conversation.moveCursor(QTextCursor.End)
            self.conversation.insertPlainText(event["text"])
        elif kind == "task":
            self.statusBar().showMessage("AIpy 正在执行：" + event["text"][:90])
            item = QListWidgetItem("执行中 · " + event["text"][:24])
            item.setData(Qt.UserRole, event["id"])
            self.task_items[event["id"]] = item
            self.task_list.addItem(item)
        elif kind == "task_end":
            self.statusBar().showMessage("子任务：" + event["state"])
            item = self.task_items.get(event["id"])
            if item:
                selected = f" · 选第{event['selected_attempt']}/{event.get('attempts', 1)}版" if event.get("selected_attempt") else ""
                label = {"completed": "完成", "needs_attention": "待老板或主 Agent 处理", "cancelled": "已取消"}.get(event["state"], "失败")
                item.setText(label + selected)
        elif kind == "error":
            self.chat_failed(event["text"])
        elif kind == "status":
            self.statusBar().showMessage(event["text"])
        elif kind == "draft":
            self._draft_text = event["text"]

    def chat_failed(self, text):
        self.conversation.moveCursor(QTextCursor.End)
        self.conversation.insertPlainText("\n运行提示：" + text)
        self.statusBar().showMessage("任务未完成，可调整模型或重试")

    def chat_finished(self):
        if self.drafting:
            self.drafting = False
            self.send_button.setEnabled(True)
            self.draft_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            if self._draft_text and not self.quitting:
                self.review_draft()
            return
        if self._answer:
            self.transcript.append({"role": "assistant", "text": self._answer})
        self.save_session()
        self.send_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def cancel_chat(self):
        if self.chat_job:
            self.chat_job.cancel()

    def save_session(self):
        if not self.transcript:
            return
        directory = DATA / "sessions"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.history_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"title": self.transcript[0]["text"][:30],
            "updated": datetime.now().isoformat(), "transcript": self.transcript,
            "messages": self.agent.messages, "pi_session_file": self.agent.session_file}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        self.refresh_sessions()

    def refresh_sessions(self):
        self.sessions.clear()
        for path in sorted((DATA / "sessions").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                item = QListWidgetItem(data["title"])
                item.setData(Qt.UserRole, str(path))
                self.sessions.addItem(item)
            except (ValueError, OSError, KeyError):
                continue

    def new_session(self):
        if self.chat_job and self.chat_job.isRunning():
            return
        self.save_session()
        self.history_id = uuid.uuid4().hex
        self.agent = PiSession(self.agent_settings())
        self.transcript = []
        self.conversation.clear()

    def session_menu(self, point):
        item = self.sessions.itemAt(point)
        if item is None:
            return
        self.sessions.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("删除这段历史对话", self.delete_session)
        menu.exec(self.sessions.viewport().mapToGlobal(point))

    def delete_session(self):
        item = self.sessions.currentItem()
        if item is None:
            self.statusBar().showMessage("请先选择要删除的历史对话")
            return
        if self.chat_job and self.chat_job.isRunning():
            self.statusBar().showMessage("请先停止当前任务，再删除会话")
            return
        from atlasforge.history import trash_session
        path = Path(item.data(Qt.UserRole))
        self.deleted_session = trash_session(path)
        self.undo_delete.setEnabled(True)
        if path.stem == self.history_id:
            self.history_id = uuid.uuid4().hex
            self.agent = PiSession(self.agent_settings())
            self.transcript = []
            self.conversation.clear()
        self.refresh_sessions()
        self.statusBar().showMessage("会话已删除，可撤销；子任务产物保留")

    def restore_deleted_session(self):
        if self.deleted_session:
            from atlasforge.history import restore_session
            restore_session(self.deleted_session)
            self.deleted_session = None
            self.undo_delete.setEnabled(False)
            self.refresh_sessions()
            self.statusBar().showMessage("会话已恢复")

    def load_session(self, item):
        if self.chat_job and self.chat_job.isRunning():
            return
        path = Path(item.data(Qt.UserRole))
        data = json.loads(path.read_text(encoding="utf-8"))
        self.history_id = path.stem
        self.transcript = data["transcript"]
        self.agent.messages = data["messages"]
        self.agent.session_file = data.get("pi_session_file")
        self.conversation.setPlainText("\n\n".join(("你" if m["role"] == "user" else "助手") + "\n" + m["text"] for m in self.transcript))

    def attach(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择交给 Agent 处理的文件")
        if path:
            self.editor.insertPlainText(f"\n输入文件：{path}\n")
            if Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
                self.attach_image(Path(path))

    def attach_image(self, path):
        if not self.settings.vision:
            self.statusBar().showMessage("当前模型未启用图片能力，可由子任务按文件处理")
            return
        import base64
        import io
        from PIL import Image
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((448, 448))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=90)
        self.agent.pending_images.append({"type": "image", "data": base64.b64encode(buffer.getvalue()).decode("ascii"), "mimeType": "image/jpeg"})
        self.statusBar().showMessage("图片已附加，下次发送时交给当前模型")


    def export_chat(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出对话", "对话.md", "Markdown (*.md)")
        if path:
            Path(path).write_text("\n\n".join(f"## {m['role']}\n{m['text']}" for m in self.transcript), encoding="utf-8")

    def open_tasks(self):
        directory = DATA / "tasks"
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(str(directory))


    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.result_settings
            self.agent.settings = self.agent_settings()
            self.statusBar().showMessage("人格、重做次数和关闭行为已生效；模型配置修改需重启")

    def closeEvent(self, event):
        if not self.quitting:
            event.ignore()
            action = self.settings.close_action
            if action == "minimize":
                self.showMinimized()
            elif action == "tray":
                self.hide()
            else:
                self.exit_app()
        else:
            event.accept()

    def exit_app(self):
        self.quitting = True
        self.cancel_chat()
        if self.chat_job:
            self.chat_job.wait(5000)
        self.save_session()
        self.tray.hide()
        self.exiting.emit()
