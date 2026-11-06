"""无模型的源码与分离边界检查；不会执行工具或访问网络。"""
import ast,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class SourceTests(unittest.TestCase):
    def test_syntax(self):
        for path in (ROOT/'atlasforge').rglob('*.py'):ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
    def test_no_other_product_import(self):
        forbidden={'fluxkeys', 'astralens'}
        for path in (ROOT/'atlasforge').rglob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                module=node.module if isinstance(node,ast.ImportFrom) else ''
                if module:self.assertNotIn(module.split('.')[0],forbidden)
    def test_no_runtime_in_source(self):
        self.assertTrue((ROOT/'main.py').is_file())
        self.assertTrue((ROOT/'THIRD_PARTY.md').is_file())
if __name__=='__main__':unittest.main()
