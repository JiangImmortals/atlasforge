"""官方 AIpy TaskManager 适配：原生角色、执行器、上下文与任务恢复保持不变。

GUI 只配置模型/工作目录、添加人格提示并导出有限摘要；不会替换 Step 或执行器。
代答独立调用 AIpy 的模型客户端，不进入任务执行循环，因此没有代码执行入口。
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys


def main():
    request = json.load(sys.stdin)
    directory = Path(request['directory']).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    profile = root / 'data/aipy-profile'
    profile.mkdir(parents=True, exist_ok=True)
    # 仅此子进程使用便携用户目录；官方 Path.home/config/plugin 逻辑无需修改。
    os.environ['USERPROFILE'] = str(profile)
    cfg = request['config']
    with (directory / 'execution.log').open('a', encoding='utf-8') as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            from loguru import logger
            logger.remove()
            logger.add(log, level='INFO')
            from aipyapp.aipy.config import ConfigManager
            from aipyapp.aipy import TaskManager
            from aipyapp.llm import MessageRole
            config_dir = profile / '.aipyapp'
            config_dir.mkdir(exist_ok=True)
            settings = ConfigManager(config_dir).get_config()
            settings.set('llm', {'portable': {
                'type': cfg.get('aipy_provider', 'openai'), 'base_url': cfg['base_url'],
                'model': cfg['model'], 'api_key': cfg['api_key'] or 'local',
                'default': True, 'enable': True,
            }}, merge=False)
            # 离线优先只关闭分享和远端诊断，不减少原生能力或覆盖轮数/输出限制。
            settings.update({'workdir': str(directory), 'gui': False, 'lang': 'zh',
                             'share_result': False, 'diagnose': {}})
            manager = TaskManager(settings)
            role = manager.role_manager.current_role
            role.add_tip('portable_persona', '软件中设置的附加人格', cfg.get('sub_system_prompt', ''))
            state_path = directory / 'native-state.json'
            task = manager.load_task(state_path) if state_path.exists() else manager.new_task()
            task_status = None
            if request.get('purpose') == 'draft':
                from aipyapp.aipy.client import StreamProcessor
                client = manager.client_manager.current
                response_message = client([
                    {'role': 'system', 'content': cfg.get('sub_system_prompt', '') + '\n只拟写可供老板审阅的第一人称文字，不调用工具。'},
                    {'role': 'user', 'content': request['task']},
                ], StreamProcessor(task, client.name))
            else:
                response = task.run(request['task'], lang='zh')
                response_message = response.message if response is not None else None
                if response is not None and response.task_status is not None:
                    task_status = response.task_status.model_dump()
                # 保存官方任务数据，反馈时用 TaskManager.load_task 恢复同一子上下文。
                task.to_file(state_path)
            if response_message is None or response_message.role == MessageRole.ERROR:
                raise RuntimeError(getattr(response_message, 'content', '') or 'AIpy 未返回结果')
            if request.get('purpose') == 'draft' and response_message.tool_calls:
                raise RuntimeError('代答返回了工具请求，已拒绝执行')
            text = response_message.content or ''
            if not text:
                raise RuntimeError('AIpy 返回空结果')
            artifacts = []
            for name in request.get('expected_files', []):
                path = Path(name)
                matches = [path] if path.is_absolute() else list(directory.rglob(path.name))
                found = [p for p in matches if p.is_file()]
                if not found:
                    raise RuntimeError('产物不存在：' + name)
                for path in found:
                    with path.open('rb') as source:
                        digest = hashlib.file_digest(source, 'sha256').hexdigest()
                    artifacts.append({'path': str(path.resolve()), 'bytes': path.stat().st_size, 'sha256': digest})
            report = {'summary': text[-4000:], 'artifacts': artifacts,
                      'task_status': task_status,
                      'native_state': str(state_path), 'role': role.name,
                      'features': role.get_features(),
                      'verification': 'files_exist_and_hashed' if artifacts else 'model_report_only'}
            (directory / 'full-result.txt').write_text(text, encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
