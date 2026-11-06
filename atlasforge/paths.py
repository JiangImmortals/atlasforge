"""每个项目独立数据目录；优先便携运行时，否则使用当前 Python / 系统 Node。"""
from pathlib import Path
import os,sys,shutil
ROOT=Path(__file__).resolve().parent.parent
DATA=Path(os.environ.get('DEMO_DATA_DIR',str(ROOT/'data'))).resolve()
PYTHON=ROOT/'runtime/python/python.exe'
if not PYTHON.is_file():PYTHON=Path(sys.executable)
NODE=ROOT/'runtime/node/node.exe'
if not NODE.is_file():NODE=Path(shutil.which('node') or 'node')
def resolve(value):
    path=Path(value)
    return path if path.is_absolute() else ROOT/path
