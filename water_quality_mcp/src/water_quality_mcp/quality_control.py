"""离线走航质控：仅标记需复核记录，不更改观测、阈值预警或排序。

借鉴QARTOD的范围、突刺、恒值和采样检查；默认参数为工程设置，未经仪器率定。
突刺会使用前后记录，恒值段会回标整段，因此不是实时因果检测器。
"""
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .data_quality import MEASUREMENTS, observed_mask

QC_VERSION = 'walk-qc-v1'
LABELS = {1: '未触发已执行规则', 2: '上下文不足', 3: '需质控复核', 4: '输入无效', 9: '关键指标缺测'}
TEST_LABELS = {'spike': '孤立突刺', 'flat_line': '持续近恒值',
               'duplicate_time': '重复时间戳', 'gap': '采样间隔过大',
               'invalid_time': '时间不可用', 'range': '超出输入范围或格式无效', 'missing': '缺少原始观测'}


@dataclass(frozen=True)
class QualityConfig:
    max_gap_s: float = 60.
    flat_duration_s: float = 60.
    flat_min_points: int = 5
    spike_thresholds: dict = field(default_factory=lambda: {'ammonia_nitrogen': 1., 'dissolved_oxygen': .5})
    flat_tolerances: dict = field(default_factory=lambda: {'ammonia_nitrogen': .001, 'dissolved_oxygen': .001})

    def __post_init__(self):
        for value in (self.max_gap_s, self.flat_duration_s):
            if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
                raise ValueError('质控时间参数须为正有限秒数')
        if type(self.flat_min_points) is not int or self.flat_min_points < 3:
            raise ValueError('恒值检查至少需要3个独立时间观测')
        for mapping, positive in ((self.spike_thresholds, True), (self.flat_tolerances, False)):
            if set(mapping) != set(MEASUREMENTS):
                raise ValueError('质控阈值须分别包含氨氮和溶解氧')
            if any(isinstance(v, bool) or not np.isfinite(v) or v < 0 or (positive and v == 0)
                   for v in mapping.values()):
                raise ValueError('质控幅度参数须为有限数，突刺阈值须大于零')


@dataclass
class QualityAssessment:
    rows: list
    summary: dict

    def with_alerts(self, alert_mask):
        mask = np.asarray(alert_mask, dtype=bool)
        if mask.shape != (len(self.rows),):
            raise ValueError('预警掩码必须与原记录逐行对齐')
        review = np.array([row['flag'] == 3 for row in self.rows])
        return {**self.summary, '原始阈值预警点数': int(mask.sum()),
            '需质控复核预警点数': int((mask & review).sum()),
            '其余阈值预警点数': int((mask & ~review).sum()),
            '预警解释': '复核标记不撤销任何阈值预警；其余预警也不代表已确认污染或传感器合格。'}


def assess_quality(df, config=None):
    config = config or QualityConfig()
    n = len(df)
    rows = [{'id': i, 'flag': 2, 'label': LABELS[2], 'reasons': [],
             'tests': {}, 'raw_values': {}, 'raw_text': {}, 'raw_preserved': {}} for i in range(n)]
    times = (pd.to_datetime(df['datetime'], errors='coerce', utc=True) if 'datetime' in df
             else pd.Series(pd.NaT, index=df.index, dtype='datetime64[ns, UTC]'))
    seconds = times.array.as_unit('ns').asi8.astype(float) / 1e9
    seconds[times.isna().to_numpy()] = np.nan
    order = np.argsort(np.where(np.isfinite(seconds), seconds, np.inf), kind='stable')
    duplicates = (times.notna() & times.duplicated(keep=False)).to_numpy()
    temporal = np.zeros(n, dtype=bool)
    counts = {key: set() for key in TEST_LABELS}

    def reason(i, test, parameter=None):
        rows[i]['reasons'].append({'test': test, 'parameter': parameter, 'message': TEST_LABELS[test]})
        counts[test].add(int(i))

    previous = None
    for i in order:
        if not np.isfinite(seconds[i]):
            reason(i, 'invalid_time')
            continue
        if duplicates[i]:
            reason(i, 'duplicate_time'); temporal[i] = True
        if previous is not None and seconds[i] - seconds[previous] > config.max_gap_s:
            reason(i, 'gap'); temporal[i] = True
        previous = i

    flags_by_channel = []
    for column, (label, low, high) in MEASUREMENTS.items():
        preserved = f'__raw_{column}' in df
        source = df.get(f'__raw_{column}', df.get(column, pd.Series(np.nan, index=df.index)))
        values = pd.to_numeric(source, errors='coerce').to_numpy(dtype=float, na_value=np.nan)
        blank = source.isna().to_numpy() | source.astype(str).str.strip().eq('').to_numpy()
        valid = observed_mask(df, column).to_numpy(dtype=bool)
        invalid = ~blank & (~np.isfinite(values) | (values < low) | (values > high))
        flags = np.full(n, 2, dtype=int)
        flags[~valid] = 9
        flags[invalid] = 4
        eligible = valid & ~invalid & np.isfinite(seconds) & ~duplicates
        for i in range(n):
            rows[i]['raw_values'][column] = float(values[i]) if preserved and np.isfinite(values[i]) else None
            rows[i]['raw_text'][column] = str(source.iloc[i])[:120] if preserved and not blank[i] else None
            rows[i]['raw_preserved'][column] = preserved
            rows[i]['tests'][column] = {'range': int(4 if invalid[i] else 1 if valid[i] else 9),
                                        'spike': 2, 'flat_line': 2}
            if invalid[i]:
                reason(i, 'range', column)
            elif not valid[i]:
                reason(i, 'missing', column)

        # 不跨缺测、重复时刻、长空档补邻居；不让重复记录充当独立证据。
        segments = []
        current = []
        for i in order:
            if not eligible[i]:
                if current: segments.append(current)
                current = []
                continue
            if current and seconds[i] - seconds[current[-1]] > config.max_gap_s:
                segments.append(current); current = []
            current.append(int(i))
        if current: segments.append(current)

        for segment in segments:
            ids = np.asarray(segment)
            if len(ids) >= 3:
                left, center, right = ids[:-2], ids[1:-1], ids[2:]
                threshold = config.spike_thresholds[column]
                # 按真实时间线性插值邻居基线，不把不等间隔误当等间隔。
                fraction = (seconds[center]-seconds[left])/(seconds[right]-seconds[left])
                baseline = values[left] + fraction*(values[right]-values[left])
                spikes = ((values[center]-values[left])*(values[center]-values[right]) > 0)
                spikes &= (np.abs(values[center]-values[left]) > threshold)
                spikes &= (np.abs(values[center]-values[right]) > threshold)
                spikes &= np.abs(values[left]-values[right]) <= threshold
                spikes &= np.abs(values[center]-baseline) > threshold
                flags[center] = 1
                for i in center:
                    rows[i]['tests'][column]['spike'] = 1
                for i in center[spikes]:
                    flags[i] = 3; rows[i]['tests'][column]['spike'] = 3
                    reason(int(i), 'spike', column)

            # 恒值段全范围差值，而非相邻差值，避免缓慢漂移连成伪恒值段。
            start = 0
            while start < len(ids):
                end = start+1
                lo = hi = values[ids[start]]
                while end < len(ids):
                    value = values[ids[end]]
                    if max(hi, value)-min(lo, value) > config.flat_tolerances[column]: break
                    lo, hi = min(lo, value), max(hi, value)
                    end += 1
                run = ids[start:end]
                if len(run) >= config.flat_min_points and seconds[run[-1]]-seconds[run[0]] >= config.flat_duration_s:
                    for i in run:
                        flags[i] = 3; rows[i]['tests'][column]['flat_line'] = 3
                        reason(int(i), 'flat_line', column)
                start = end
        flags_by_channel.append(flags)

    channel_flags = np.vstack(flags_by_channel) if n else np.empty((2, 0), dtype=int)
    for i, row in enumerate(rows):
        flags = channel_flags[:, i]
        primary = (4 if 4 in flags else 9 if 9 in flags else 3 if 3 in flags or temporal[i]
                   else 1 if np.all(flags == 1) else 2)
        row['flag'], row['label'] = primary, LABELS[primary]
    summary = {'版本': QC_VERSION, '记录数': n,
        '需复核记录数': sum(row['flag'] == 3 for row in rows),
        '输入无效记录数': sum(row['flag'] == 4 for row in rows),
        '关键指标缺测记录数': sum(row['flag'] == 9 for row in rows),
        '上下文不足记录数': sum(row['flag'] == 2 for row in rows),
        '未触发规则记录数': sum(row['flag'] == 1 for row in rows),
        '原始值已保留': {col: f'__raw_{col}' in df for col in MEASUREMENTS},
        '规则标记记录数': {TEST_LABELS[key]: len(ids) for key, ids in counts.items()},
        '参数': asdict(config),
        '检查范围': '单条走航的氨氮/DO，离线前后邻居突刺、近恒值和采样检查；非多仪器联合校准。',
        '局限': '阈值为未率定的工程配置。标记可能来自真实短排放、稳定水体、分辨率或仪器异常；不删除观测，不修改污染预警，不能据此确认仪器故障。'}
    return QualityAssessment(rows, summary)
