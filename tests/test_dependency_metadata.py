"""依赖元数据：CI 与锁定版本共用同一组版本，OpenCV 变体只在两处分别固定。"""
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def requirements(name):
    versions = {}
    for line in (ROOT / name).read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if '==' in line:
            package, version = line.split('==', 1)
            versions[package.strip()] = version.strip()
    return versions


class DependencyMetadataTests(unittest.TestCase):
    def test_ci_and_lock_share_versions_except_declared_variants(self):
        ci = requirements('requirements-ci.txt')
        lock = requirements('requirements-lock.txt')
        shared = set(ci) & set(lock)
        self.assertTrue(shared)
        for package in shared:
            self.assertEqual(ci[package], lock[package], package)
        # 产品锁使用 opencv-python，CI 使用无界面 headless；版本必须一致。
        self.assertEqual(lock['opencv-python'], ci['opencv-python-headless'])
        # 可选 RAG/Streamlit 依赖只进入完整锁定，不进入 CI 环境。
        for package in ('streamlit', 'chromadb', 'sentence-transformers'):
            self.assertIn(package, lock)
            self.assertNotIn(package, ci)

    def test_package_metadata_matches_verified_python_and_rag_floor(self):
        data = tomllib.loads((ROOT / 'water_quality_mcp' / 'pyproject.toml').read_text(encoding='utf-8'))
        project = data['project']
        self.assertEqual(project['requires-python'], '>=3.11')
        dependencies = {item.split('>=', 1)[0].strip(): item for item in project['dependencies']}
        self.assertIn('chromadb>=1.5.8', dependencies['chromadb'])
        self.assertIn('sentence-transformers>=5.5.1', dependencies['sentence-transformers'])


if __name__ == '__main__':
    unittest.main()
