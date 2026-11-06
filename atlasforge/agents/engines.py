"""官方引擎版本管理：独立暂存、兼容性检查、原子切换和回退。

升级只写 runtime/engines，不覆盖 GUI Python 依赖、用户配置或任务历史。
当前任务继续使用启动时选中的版本；下一个任务才读取新版本。
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
import uuid
from atlasforge.paths import ROOT, DATA, NODE, PYTHON

PI_PACKAGE = '@earendil-works/pi-coding-agent'
MANIFEST = DATA / 'engines.json'


def selection():
    if MANIFEST.exists():
        result = json.loads(MANIFEST.read_text(encoding='utf-8'))
        for key in ('pi_dir', 'aipy_dir'):
            candidate = (ROOT / result[key]).resolve()
            if not candidate.is_relative_to((ROOT / 'runtime/engines').resolve()) or not candidate.is_dir():
                raise RuntimeError('引擎版本路径无效，可用回退功能恢复')
        return result
    return {'pi_dir': 'agent_runtime', 'aipy_dir': '', 'pi_version': version(), 'aipy_version': '0.5.0'}


def version():
    return json.loads((ROOT / 'agent_runtime/node_modules' / PI_PACKAGE / 'package.json').read_text(encoding='utf-8'))['version']


def adapter_path(active):
    """宿主适配代码随软件更新；上游依赖版本保持当前选择。"""
    directory = ROOT / active['pi_dir']
    if directory.resolve() != (ROOT / 'agent_runtime').resolve():
        for name in ('main.mjs', 'subagent.mjs'):
            content = (ROOT / 'agent_runtime' / name).read_bytes()
            target = directory / name
            if not target.exists() or target.read_bytes() != content:
                temporary = target.with_name(name + '.' + uuid.uuid4().hex + '.tmp')
                temporary.write_bytes(content)
                temporary.replace(target)
    return directory / 'main.mjs'


def environment(selected=None):
    selected = selected or selection()
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    env.update(PIP_CACHE_DIR=str(DATA / 'cache/pip'), NPM_CONFIG_CACHE=str(DATA / 'cache/npm'),
               MPLCONFIGDIR=str(DATA / 'cache/matplotlib'), HF_HOME=str(DATA / 'cache/huggingface'),
               XDG_CACHE_HOME=str(DATA / 'cache'))
    env['PATH'] = os.pathsep.join((str(NODE.parent), str(PYTHON.parent), str(ROOT / 'runtime/git/cmd'), env.get('PATH', '')))
    paths = [str(ROOT)]
    if selected.get('aipy_dir'):
        paths.insert(0, str(ROOT / selected['aipy_dir']))
    env['PYTHONPATH'] = os.pathsep.join(paths)
    return env


def _json(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def check():
    current = selection()
    latest = {'pi_version': _json('https://registry.npmjs.org/@earendil-works%2fpi-coding-agent/latest')['version'],
              'aipy_version': _json('https://pypi.org/pypi/aipyapp/json')['info']['version']}
    return {'current': current, 'latest': latest,
            'available': any(current[k] != v for k, v in latest.items())}


def _run(command, cwd, env):
    process = subprocess.run([str(part) for part in command], cwd=cwd, env=env,
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        creationflags=subprocess.CREATE_NO_WINDOW)
    if process.returncode:
        raise RuntimeError((process.stderr + process.stdout)[-5000:])
    return process.stdout


def validate(pi_dir, aipy_dir):
    """不调用付费模型，验证本机 SDK 导出、默认工具和 AIpy 默认角色可用。"""
    selected = {'aipy_dir': str(aipy_dir)}
    env = environment(selected)
    probe = """
import {createAgentSession,SessionManager,DefaultResourceLoader,ModelRuntime,SettingsManager} from '@earendil-works/pi-coding-agent';
const cwd=process.cwd(), agentDir=process.argv[2];
const modelRuntime=await ModelRuntime.create({authPath:agentDir+'/probe-auth.json',modelsPath:null});
modelRuntime.registerProvider('probe',{api:'openai-completions',apiKey:'local',baseUrl:'http://127.0.0.1:1/v1',
 models:[{id:'probe',name:'probe',reasoning:false,input:['text'],contextWindow:8192,maxTokens:4096,cost:{input:0,output:0,cacheRead:0,cacheWrite:0}}]});
const settingsManager=SettingsManager.inMemory();
const resourceLoader=new DefaultResourceLoader({cwd,agentDir,settingsManager,noExtensions:true,noSkills:true,noPromptTemplates:true,noThemes:true,noContextFiles:true,appendSystemPrompt:['probe-persona']});
await resourceLoader.reload();
const {session}=await createAgentSession({cwd,agentDir,modelRuntime,model:modelRuntime.getModel('probe','probe'),settingsManager,resourceLoader,sessionManager:SessionManager.inMemory(cwd)});
for(const tool of ['read','bash','edit','write'])if(!session.getActiveToolNames().includes(tool))throw Error('missing '+tool);
if(!session.systemPrompt.includes('probe-persona')||typeof session.prompt!=='function'||typeof session.subscribe!=='function')throw Error('Pi adapter incompatible');
session.dispose();console.log('Pi SDK OK');
"""
    # 使用文件保证 ESM 从待验证目录解析依赖。
    probe_path = pi_dir / 'engine-probe.mjs'
    probe_path.write_text(probe, encoding='utf-8')
    try:
        _run([NODE, probe_path, pi_dir], ROOT, env)
    finally:
        probe_path.unlink(missing_ok=True)
        (pi_dir / 'probe-auth.json').unlink(missing_ok=True)
    profile = DATA / 'aipy-profile'
    profile.mkdir(parents=True, exist_ok=True)
    env['USERPROFILE'] = str(profile)
    _run([PYTHON, '-c', 'from aipyapp.aipy import TaskManager; from aipyapp.aipy.config import ConfigManager; from aipyapp.aipy.role import Role; assert hasattr(TaskManager,"load_task"); print("AIpy SDK OK")'], ROOT, env)


def _save(record):
    DATA.mkdir(exist_ok=True)
    temp = MANIFEST.with_suffix('.tmp')
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(MANIFEST)


def _upgrade():
    status = check()
    if not status['available']:
        return '官方 Pi 和 AIpy 已是当前发布版'
    previous = status['current']
    versions = status['latest']
    destination = ROOT / 'runtime/engines' / (str(time.time_ns()) + '-' + uuid.uuid4().hex[:6])
    destination.mkdir(parents=True)
    pi_dir, aipy_dir = destination / 'pi', destination / 'python'
    pi_dir.mkdir(); aipy_dir.mkdir()
    env = environment()
    npm = ROOT / 'runtime/npm/bin/npm-cli.js'
    if not npm.is_file():
        npm_command = shutil.which('npm.cmd') or shutil.which('npm')
        if not npm_command: raise RuntimeError('请先安装 Node.js 与 npm')
        npm = Path(npm_command).resolve().parent / 'node_modules/npm/bin/npm-cli.js'
        if not npm.is_file(): raise RuntimeError('找不到 npm-cli.js，请配置便携 npm 路径')
    # 命令以参数数组运行，不依赖机器安装的 npm，也不会写全局 Python。
    _run([NODE, npm, 'install', '--prefix', pi_dir, '--registry=https://registry.npmjs.org',
          '--save-exact', f'{PI_PACKAGE}@{versions["pi_version"]}', '@sinclair/typebox@0.34.41'], ROOT, env)
    _run([PYTHON, '-m', 'pip', 'install', '--index-url', 'https://pypi.org/simple', '--target', aipy_dir,
          '--upgrade', f'aipyapp=={versions["aipy_version"]}'], ROOT, env)
    for name in ('main.mjs', 'subagent.mjs'):
        shutil.copyfile(ROOT / 'agent_runtime' / name, pi_dir / name)
    validate(pi_dir, aipy_dir)
    record = {**versions, 'pi_dir': str(pi_dir.relative_to(ROOT)),
              'aipy_dir': str(aipy_dir.relative_to(ROOT)), 'previous': previous}
    _save(record)
    return f'官方引擎已升级：Pi {versions["pi_version"]} / AIpy {versions["aipy_version"]}；下次任务生效，可回退'


def upgrade():
    DATA.mkdir(exist_ok=True)
    lock = DATA / 'engine-update.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError('另一个引擎升级正在进行；若上次异常退出，请退出软件后删除 data/engine-update.lock 再试')
    try:
        os.write(fd, str(os.getpid()).encode())
        return _upgrade()
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def automatic_update():
    """用户开启后，每天至多检查/升级一次；失败保留当前引擎。"""
    stamp = DATA / 'engine-last-check.txt'
    if stamp.exists() and time.time() - stamp.stat().st_mtime < 86400:
        return None
    result = upgrade()
    stamp.write_text(str(time.time()), encoding='ascii')
    return result


def rollback():
    current = selection()
    if not current.get('previous'):
        return '没有可回退的引擎版本'
    previous = current['previous']
    if previous['pi_dir'] == 'agent_runtime':
        MANIFEST.unlink(missing_ok=True)
    else:
        _save(previous)
    return '已恢复上一引擎版本；下次任务生效'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'upgrade', 'rollback', 'verify'])
    action = parser.parse_args().action
    if action == 'check':
        print(json.dumps(check(), ensure_ascii=False, indent=2))
    elif action == 'upgrade':
        print(upgrade())
    elif action == 'rollback':
        print(rollback())
    else:
        active = selection()
        validate(ROOT / active['pi_dir'], ROOT / active['aipy_dir'] if active['aipy_dir'] else PYTHON.parent / 'Lib/site-packages')
        print('官方引擎接口检查通过')


if __name__ == '__main__':
    main()
