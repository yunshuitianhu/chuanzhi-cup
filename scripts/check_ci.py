"""跨平台CI入口：临时仿真样本/项目目录，无密钥、无模型下载，不使用桌面私有样本。"""
from pathlib import Path
import argparse
import os
import runpy
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def legacy(script, sample):
    """旧脚本仍运行真实内核/HTTP；外部Requests明确拒绝，走既有降级。"""
    import requests
    sys.path.insert(0, str(ROOT))
    sys.argv = [str(ROOT / script), str(sample)]
    if script == 'tests/前端字段名检查.py':
        sys.argv.append('--offline')
    with patch('water_quality_mcp.src.water_quality_mcp.config.resolve_api_key', return_value=''), \
         patch.object(requests.sessions.Session, 'request', side_effect=requests.ConnectionError('CI offline check')):
        runpy.run_path(str(ROOT / script), run_name='__main__')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy', nargs=2, metavar=('SCRIPT', 'SAMPLE'))
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.legacy:
        legacy(*args.legacy)
        return
    import numpy as np
    import pandas as pd
    with tempfile.TemporaryDirectory(prefix='wq-ci-') as temporary:
        work = Path(temporary)
        sample = work / 'CI_仿真字段样本_非实测.xlsx'
        n = 120
        pd.DataFrame({
            'datetime': pd.date_range('2025-01-01', periods=n, freq='10s'),
            'longitude': 113 + np.linspace(0, .003, n),
            'latitude': 23 + np.linspace(0, .003, n),
            'ammonia_nitrogen': 6 + np.sin(np.linspace(0, 4, n)),
            'dissolved_oxygen': .6 + .1*np.cos(np.linspace(0, 4, n)),
            'turbidity': np.full(n, 20.), 'ph': np.full(n, 7.), 'cod': np.full(n, 40.),
        }).to_excel(sample, index=False)
        if not sample.is_file():
            raise RuntimeError('CI仿真样本未生成，拒绝跳过字段检查')
        env = {**os.environ, 'WQ_PROJECT_HOME': str(work/'projects'), 'MPLCONFIGDIR': str(work/'mpl'),
               'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
        commands = [[sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v']]
        for script in ['字段名一致性检查.py', '前端字段名检查.py', '泛化能力检查.py', '后端接口检查.py']:
            commands.append([sys.executable, str(Path(__file__).resolve()), '--legacy', 'tests/'+script, str(sample)])
        commands.append([sys.executable, 'tests/密钥泄漏检查.py'])
        for command in commands:
            print('RUN:', ' '.join(command[1:]), flush=True)
            subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=600)
    print('Core CI checks passed. Synthetic/offline coverage only; no live RAG or field-accuracy claim.', flush=True)


if __name__ == '__main__':
    main()
