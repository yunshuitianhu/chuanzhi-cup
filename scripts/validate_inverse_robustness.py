"""兼容入口：运行新网格单源压力检查，保留错误校准导致负荷混淆的已知盲点。"""
from pathlib import Path
import argparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validate_refined_inverse import run as _run


def run(output):
    passed = _run(output, group='robustness')
    (output/'robustness.json').write_bytes((output/'validation.json').read_bytes())
    return passed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parents[1]/'work'/'inverse-robustness')
    args = parser.parse_args()
    raise SystemExit(0 if run(args.output_dir) else 1)
