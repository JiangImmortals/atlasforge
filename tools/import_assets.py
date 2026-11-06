"""显式从用户本地旧便携包导入本项目资产，不下载或上传任何内容。"""
import argparse,shutil
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--from',dest='source',required=True);args=parser.parse_args()
source=Path(args.source).expanduser().resolve();root=Path(__file__).resolve().parents[1]
if not source.is_dir():raise SystemExit('源目录不存在')
for name in ['models/minicpm', 'runtime/llama']:
    original=source/name;target=root/name
    if not target.resolve().is_relative_to(root):raise SystemExit('目标超出项目目录')
    if not original.exists():print('未找到：'+name);continue
    if target.exists():print('已存在，保留：'+name);continue
    target.parent.mkdir(parents=True,exist_ok=True)
    if original.is_dir():shutil.copytree(original,target)
    else:shutil.copy2(original,target)
    print('已导入：'+name)
