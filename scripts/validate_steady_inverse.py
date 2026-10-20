"""兼容入口：运行新网格、解析真值的跨河段单源验收。旧81/41节点结果仅保留为历史记录。"""
from pathlib import Path
import argparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validate_refined_inverse import run as _run, uniform_hydro


def run(output):
    return _run(output, group='cross_river')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parents[1]/'work'/'steady-inverse-validation')
    args = parser.parse_args()
    raise SystemExit(0 if run(args.output_dir) else 1)
