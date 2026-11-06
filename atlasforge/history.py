"""会话删除采用本地回收站，避免误删后无法恢复；不触及子任务产物。"""
from datetime import datetime
from pathlib import Path
from atlasforge.paths import DATA


def trash_session(path):
    path = Path(path).resolve()
    if path.parent != (DATA / "sessions").resolve() or path.suffix != ".json":
        raise ValueError("只能删除会话目录中的会话文件")
    directory = DATA / "session-trash"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{path.stem}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json"
    path.replace(target)
    return target


def restore_session(path):
    path = Path(path).resolve()
    if path.parent != (DATA / "session-trash").resolve():
        raise ValueError("只能恢复会话回收站中的文件")
    target = DATA / "sessions" / (path.name.split("-", 1)[0] + ".json")
    if target.exists():
        raise ValueError("同名会话已存在，未覆盖")
    path.replace(target)
    return target
