"""打开上游原版交互入口，使用同一便携环境；不经过 GUI 的执行循环。"""
import argparse
import json
import subprocess
from atlasforge.paths import ROOT, DATA, NODE, PYTHON
from atlasforge.settings import Settings
from pathlib import Path
from atlasforge.agents.engines import selection, environment


def command(engine, arguments=()):
    active = selection()
    env = environment(active)
    settings = Settings.load()
    if engine == 'pi':
        directory = DATA / 'pi'
        directory.mkdir(parents=True, exist_ok=True)
        env['PI_CODING_AGENT_DIR'] = str(directory)
        env['PORTABLE_API_KEY'] = settings.api_key or 'local'
        # 合并软件模型条目；保留原版配置中其他 provider、认证、扩展和技能。
        models_path = directory / 'models.json'
        models = json.loads(models_path.read_text(encoding='utf-8')) if models_path.exists() else {}
        models.setdefault('providers', {})['portable'] = {
            'api': 'openai-completions', 'baseUrl': settings.base_url, 'apiKey': 'PORTABLE_API_KEY',
            'models': [{'id': settings.model, 'contextWindow': settings.context_size,
                        'maxTokens': min(8192, settings.context_size // 2)}]}
        models_path.write_text(json.dumps(models, indent=2), encoding='utf-8')
        native_settings = directory / 'settings.json'
        saved = json.loads(native_settings.read_text(encoding='utf-8')) if native_settings.exists() else {}
        bash = ROOT / 'runtime/git/bin/bash.exe'
        if not bash.is_file():
            import shutil
            git = shutil.which('git')
            bash = Path(git).parent.parent / 'bin/bash.exe' if git else bash
        if bash.is_file(): saved['shellPath'] = str(bash)
        else: saved.pop('shellPath', None)
        native_settings.write_text(json.dumps(saved, indent=2), encoding='utf-8')
        cli = ROOT / active['pi_dir'] / 'node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js'
        args = [NODE, cli, '--provider', settings.pi_provider, '--model', settings.model, *arguments]
    else:
        profile = DATA / 'aipy-profile'
        config_dir = profile / '.aipyapp'
        config_dir.mkdir(parents=True, exist_ok=True)
        env['USERPROFILE'] = str(profile)
        # 原版 CLI 通过独立配置目录读取模型；其余原生配置原样保留。
        import tomli_w
        path = config_dir / 'aipyapp.toml'
        import tomllib
        saved = tomllib.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        saved.update(workdir=str(DATA / 'native-aipy-work'), share_result=False, diagnose={})
        saved.setdefault('llm', {})['portable'] = {
            'type': settings.aipy_provider, 'model': settings.model, 'base_url': settings.base_url,
            'api_key': settings.api_key or 'local', 'default': True, 'enable': True}
        path.write_text(tomli_w.dumps(saved), encoding='utf-8')
        # USERPROFILE 已指向便携配置目录。省略 -c，让上游 argparse 保留默认 Path，
        # 避免 0.5.0 将显式 -c 转为 str 后在 get_logo_text 中用 / 拼接而崩溃。
        args = [PYTHON, '-m', 'aipyapp', *arguments]
    return [str(arg) for arg in args], env


def launch(engine):
    return subprocess.Popen([str(PYTHON), '-m', 'atlasforge.agents.native', engine], cwd=ROOT,
        env=environment(), creationflags=subprocess.CREATE_NEW_CONSOLE)


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('engine', choices=['pi', 'aipy'])
    options, extra = parser.parse_known_args()
    args, env = command(options.engine, extra)
    service = None
    lease = None
    try:
        if not any(arg in ('--help', '-h', '--version', 'update', 'sync') for arg in extra):
            settings = Settings.load()
            if settings.backend == 'managed':
                from atlasforge.models import ModelService
                service = ModelService(settings)
                print('正在检查并启动本地模型服务，请稍候…', flush=True)
                pending_lease = service.lease(vision=settings.vision)
                pending_lease.__enter__()
                lease = pending_lease
        return subprocess.call(args, cwd=ROOT, env=env)
    finally:
        if lease:
            lease.__exit__(None, None, None)
        if service:
            service.stop()


if __name__ == '__main__':
    raise SystemExit(main())
