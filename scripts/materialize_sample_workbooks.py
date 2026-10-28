"""把生成器的 workbook_payload.json 物化为示例 Excel 工作簿。

V2 路线由 `scripts/generate_synthetic_monitoring.py` 生成。该脚本输出
`S??_workbook_payload.json`，本脚本把它写成 `仿真走航数据` / `数据说明`
两个工作表，供 `scripts/prepare_sample_data.py --source-dir` 导出 CSV。
输出到新目录，不覆盖原始 `仿真数据/` 或用户项目。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
NAMES = {
    'S01': 'S01_仿真_正常背景.xlsx',
    'S02': 'S02_仿真_单源持续排放.xlsx',
    'S03': 'S03_仿真_双源持续排放.xlsx',
    'S04': 'S04_仿真_单源两次间歇排放.xlsx',
    'S05': 'S05_仿真_双源氨氮与有机物耗氧.xlsx',
    'S06': 'S06_仿真_正常水体加仪器异常与缺测.xlsx',
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True,
                        help='generate_synthetic_monitoring.py 的输出目录')
    parser.add_argument('--output', type=Path, required=True,
                        help='新的 Excel 目录；必须不存在或为空')
    args = parser.parse_args()
    source = args.input.resolve()
    target = args.output.resolve()
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f'输出目录非空，拒绝覆盖: {target}')
    target.mkdir(parents=True, exist_ok=True)
    for sample_id, filename in NAMES.items():
        payload_path = source / f'{sample_id}_workbook_payload.json'
        if not payload_path.is_file():
            raise SystemExit(f'缺少生成器载荷: {payload_path}')
        payload = json.loads(payload_path.read_text(encoding='utf-8'))
        book = openpyxl.Workbook()
        monitor = book.active
        monitor.title = '仿真走航数据'
        monitor.append(payload['headers'])
        for row in payload['rows']:
            monitor.append(row)
        notes = book.create_sheet('数据说明')
        for row in payload['notes']:
            notes.append(row)
        book.save(target / filename)
        print(f'{sample_id}: {len(payload["rows"])} 行 -> {target / filename}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
