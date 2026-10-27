"""把 `仿真数据/S01—S06*.xlsx` 的监测工作表转换为可交付 CSV。

只读取「仿真走航数据」工作表，不输出隐藏真值/污染源参数/说明文本。
不从 `load_data` 的清洗后 DataFrame 反向导出，避免把插值和坐标填补当成原始样本。
默认不覆盖内容不同的既有 CSV；使用 --force 时才重写。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / '仿真数据'
DEFAULT_OUTPUT = ROOT / 'data' / 'samples'
MONITOR_SHEET = '仿真走航数据'
NOTES_SHEET = '数据说明'
EXPECTED_ROWS = 1801
EXPECTED_COLUMNS = 12

SCENARIOS = {
    'S01': ('S01_仿真_正常背景.xlsx', 'S01_仿真_正常背景.csv',
            'S01 仿真正常背景', '正常背景对照；零预警或零热点也可以是有效筛查结果。'),
    'S02': ('S02_仿真_单源持续排放.xlsx', 'S02_仿真_单源持续排放.csv',
            'S02 仿真单源持续排放', '单源持续排放情景；场景名称不代表已经成功反演出真实来源。'),
    'S03': ('S03_仿真_双源持续排放.xlsx', 'S03_仿真_双源持续排放.csv',
            'S03 仿真双源持续排放', '双源持续排放情景；热点数量不等于污染源数量。'),
    'S04': ('S04_仿真_单源两次间歇排放.xlsx', 'S04_仿真_单源两次间歇排放.csv',
            'S04 仿真单源两次间歇排放', '单源两次间歇排放情景；读数变化仍同时受走航位置和时间影响。'),
    'S05': ('S05_仿真_双源氨氮与有机物耗氧.xlsx', 'S05_仿真_双源氨氮与有机物耗氧.csv',
            'S05 仿真双源氨氮与有机物耗氧', '双源氨氮与有机物耗氧情景；按实际字段解释指标，不把CBOD当COD。'),
    'S06': ('S06_仿真_正常水体加仪器异常与缺测.xlsx', 'S06_仿真_正常水体加仪器异常与缺测.csv',
            'S06 仿真仪器异常与缺测', '正常水体叠加仪器异常与缺测情景；用于区分原始缺测、插值和有效零值。'),
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _notes(book) -> dict:
    if NOTES_SHEET not in book.sheet_names:
        return {}
    frame = pd.read_excel(book, sheet_name=NOTES_SHEET, header=None)
    result = {}
    for _, row in frame.iterrows():
        if len(row) < 2:
            continue
        key, value = str(row.iloc[0]).strip(), row.iloc[1]
        if not key or key.lower().startswith('unnamed') or pd.isna(value):
            continue
        result[key] = str(value).strip()
    return result


def _field_units(columns) -> dict:
    units = {}
    for column in columns:
        match = re.search(r'[（(]([^）)]*)[）)]', str(column))
        if match:
            units[str(column)] = match.group(1)
    return units


def _time_text(value) -> str:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError('示例时间列包含不可解析值')
    return stamp.strftime('%Y-%m-%d %H:%M:%S')


def build_frame(source: Path):
    with pd.ExcelFile(source) as book:
        if MONITOR_SHEET not in book.sheet_names:
            raise ValueError(f'{source.name} 缺少工作表 {MONITOR_SHEET}')
        raw = pd.read_excel(book, sheet_name=MONITOR_SHEET, dtype=object)
        notes = _notes(book)
    if len(raw) != EXPECTED_ROWS or raw.shape[1] != EXPECTED_COLUMNS:
        raise ValueError(f'{source.name} 监测表尺寸异常: {raw.shape}，预期 ({EXPECTED_ROWS}, {EXPECTED_COLUMNS})')
    if '时间' not in raw.columns:
        raise ValueError(f'{source.name} 监测表缺少时间列')
    return raw, notes


def to_csv_bytes(raw: pd.DataFrame) -> bytes:
    # 只写监测表；保留空单元格、原始行顺序、数字精度和坐标。
    text = raw.to_csv(index=False, na_rep='', float_format='%.15g', lineterminator='\n')
    return text.encode('utf-8')


def metadata(sample_id: str, source: Path, csv_name: str, raw: pd.DataFrame,
             notes: dict, csv_bytes: bytes, title: str, description: str) -> dict:
    times = pd.to_datetime(raw['时间'], errors='coerce')
    if times.isna().any():
        raise ValueError(f'{source.name} 存在无法解析的时间')
    time_range = [times.min().strftime('%Y-%m-%d %H:%M:%S'),
                  times.max().strftime('%Y-%m-%d %H:%M:%S')]
    notice = notes.get('使用限制') or '仅为程序生成的仿真数据，不能作为现场实测或污染责任依据。'
    if '仿真' not in notice and '实测' not in notice:
        notice += '；仅为仿真数据，不能作为现场实测或污染责任依据。'
    return {
        'id': sample_id,
        'title': title,
        'description': description,
        'file': csv_name,
        'data_kind': 'synthetic',
        'version': 'v1-' + _sha(csv_bytes)[:12],
        'sha256': _sha(csv_bytes),
        'size_bytes': len(csv_bytes),
        'rows': int(len(raw)),
        'columns': [str(column) for column in raw.columns],
        'time_range': time_range,
        'time_zone': '无时区偏移（naive），未声明时区；按原始时间字符串解释',
        'coordinate_system': notes.get('坐标系', '未提供坐标系；不能凭地名推断'),
        'source_reference': f'仿真数据/{source.name} · 工作表 {MONITOR_SHEET}',
        'has_video': False,
        'notice': notice[:2000],
        'field_units': _field_units(raw.columns),
        'missing_rule': 'CSV保留原始空单元格；加载器另行标记有效原始观测与插值，空单元格不等于0。',
    }


def write_if_needed(path: Path, raw: bytes, force: bool) -> bool:
    if path.exists():
        old = path.read_bytes()
        if old == raw:
            return False
        if not force:
            raise SystemExit(f'{path} 已存在且内容不同；确认后可加 --force 重新生成')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--force', action='store_true', help='允许覆盖内容不同的既有 CSV')
    args = parser.parse_args()
    source_dir = args.source_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not source_dir.is_dir():
        raise SystemExit(f'示例源目录不存在: {source_dir}')
    samples = []
    for sample_id in sorted(SCENARIOS):
        source_name, csv_name, title, description = SCENARIOS[sample_id]
        source = source_dir / source_name
        if not source.is_file():
            raise SystemExit(f'缺少示例源文件: {source}')
        raw, notes = build_frame(source)
        csv_bytes = to_csv_bytes(raw)
        target = output_dir / csv_name
        changed = write_if_needed(target, csv_bytes, args.force)
        samples.append(metadata(sample_id, source, csv_name, raw, notes, csv_bytes, title, description))
        print(f'{sample_id}: {len(raw)} 行 -> {target} ({len(csv_bytes)} bytes, {"写入" if changed else "未变"})')
    manifest = {
        'manifest_version': 1,
        'data_kind': 'synthetic',
        'notice': '所有示例均为程序生成的仿真数据，不是现场实测、当地污染事实或已确认污染源；'
                  '不包含隐藏污染源真值、真实负荷或准确率结论。',
        'samples': samples,
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    write_if_needed(output_dir / 'manifest.json', manifest_bytes, args.force)
    print(f'manifest: {output_dir / "manifest.json"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
