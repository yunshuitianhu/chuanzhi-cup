"""可解释的企业候选预测；排序分不是概率，缺资料不补造企业。"""
from __future__ import annotations

import math
import re

PREDICTION_RULES = (
    '用户问哪家企业、哪里排放或要求预测时，应主动查询当前项目候选并先给预测结果。'
    '有企业候选就直接说“预测首选/建议优先排查：企业名称”，随后列备选及支持依据；'
    '不能仅以“不能明确污染源”结束回答。预测和已确认责任不同，允许证据不完整的初步预测。'
    '只采用本轮服务器提供的预测企业名、排序分及依据，不编造企业或污染概率。'
    '弱证据仍给初步首选，明确哪些依据未核实；同分说明并列，不假装能区分。'
    '没有可定位企业时先给返回的预测方向和搜索范围，不从治理案例、技术介绍或历史答案猜企业。'
    '仿真不关联真实企业。预测名称不能写成已确认排污或责任主体。'
)


def wants_prediction(text):
    text = str(text or '')
    if any(word in text for word in ('什么是污染源', '污染源的定义', '解释污染源这个词')):
        return False
    return any(word in text for word in ('污染源', '哪家企业', '哪个企业', '谁在排污', '排污企业', '溯源预测'))


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _distance(row, center):
    for key in ('distance', '距离_m', '距离'):
        value = row.get(key)
        if isinstance(value, str):
            match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(km|公里|千米|m|米)?\s*', value, re.I)
            if not match:
                continue
            number = float(match[1])
            return number * (1000 if (match[2] or '').lower() in ('km', '公里', '千米') else 1)
        number = _number(value)
        if number is not None:
            return number
    if center and len(center) == 2:
        try:
            lat, lon = float(row['lat']), float(row['lon'])
            a, b = map(float, center)
            if not all(map(math.isfinite, (lat, lon, a, b))) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return None
            x, y = map(math.radians, (lat - a, lon - b))
            h = math.sin(x / 2)**2 + math.cos(math.radians(lat))*math.cos(math.radians(a))*math.sin(y / 2)**2
            return 6371000 * 2 * math.asin(min(1., math.sqrt(h)))
        except (KeyError, TypeError, ValueError):
            pass
    return None


def predict_sources(rows, *, center=None, radius_m=500, identity=None, pollutants=()):
    """仅对范围内可定位企业排序；全国无坐标许可证不进入首选。"""
    radius = _number(radius_m) or 500
    result = {'版本': 'enterprise-prediction-v1', '状态': 'no_candidates', '首选企业': None,
              '候选企业': [], '同分候选': [], '预测方向': '优先排查搜索范围内的入河排口及其连接企业。',
              '搜索半径_m': radius, '依据等级': '初步预测，尚未校准',
              '排序说明': '排序分=65×距离接近度+25×已披露污染物匹配+10×多来源同名支持；0—100分，不是概率。',
              '限制': ['未核验入河连接、水流方向和排放时段；不代表确认污染责任。'],
              '未纳入企业数': 0}
    if (identity or {}).get('kind') == 'synthetic':
        result.update(状态='synthetic', 预测方向='按仿真设定源排查；真实周边企业不作为仿真污染来源。')
        return result
    groups = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = str(row.get('数据源') or '')
        name = str(row.get('名称') or row.get('name') or row.get('企业名称') or '').strip()
        if not name or source == '国控站点' or row.get('站点名称') or row.get('站名'):
            continue
        if any(word in name for word in ('排污口', '排口', '采样点', '监测站', '工业园区')) and '公司' not in name:
            continue
        if any(word in name for word in ('宿舍', '公交站', '停车场')):
            continue
        distance = _distance(row, center)
        if distance is None or distance > radius:
            result['未纳入企业数'] += 1
            continue
        existing = groups.get(name)
        if existing is None:
            existing = groups[name] = {'名称': name, '距离_m': distance, '数据来源': set(), '匹配指标': set()}
        existing['距离_m'] = min(distance, existing['距离_m'])
        sources = row.get('来源列表')
        existing['数据来源'].update([v for v in sources if isinstance(v, str) and v]
            if isinstance(sources, list) else ([source] if source else []))
        declared = str(row.get('主要污染物') or '')
        for pollutant in pollutants:
            if pollutant and pollutant in declared:
                existing['匹配指标'].add(pollutant)
    ranked = []
    for entry in groups.values():
        near = max(0., 1 - entry['距离_m'] / radius)
        entry['排序分'] = round(65 * near + 25 * bool(entry['匹配指标']) + 10 * (len(entry['数据来源']) > 1), 1)
        entry['数据来源'] = sorted(entry['数据来源'])
        entry['匹配指标'] = sorted(entry['匹配指标'])
        entry['距离_m'] = round(entry['距离_m'], 1)
        entry['支持依据'] = [f"距本次搜索中心约{entry['距离_m']}米"]
        if entry['匹配指标']:
            entry['支持依据'].append('资料披露污染物包含本次监测指标：' + '、'.join(entry['匹配指标']))
        if len(entry['数据来源']) > 1:
            entry['支持依据'].append('多个实际返回来源提供同名企业资料')
        entry['缺口'] = ['排口入河连接、同期排放和水流输移尚未核实']
        if len(entry['数据来源']) > 1:
            entry['缺口'].append('多来源同名资料仍需核对是否为同一经营主体')
        if not entry['匹配指标']:
            entry['缺口'].append('未取得与监测指标匹配的排放资料；当前主要依据距离排序')
        ranked.append(entry)
    ranked.sort(key=lambda row: (-row['排序分'], row['距离_m'], row['名称']))
    if ranked:
        result.update(状态='predicted', 首选企业=ranked[0], 候选企业=ranked[:5],
                      同分候选=[row['名称'] for row in ranked if row['排序分'] == ranked[0]['排序分']],
                      预测方向='优先核查首选企业与当前河段的排口连接和同期排放。')
    if result['未纳入企业数']:
        result['限制'].append('缺少可定位信息或超出半径的企业未参与预测。')
    return result


def prediction_text(prediction):
    top = prediction.get('首选企业')
    if not top:
        return '预测方向：' + prediction['预测方向'] + ' 当前没有可定位企业候选，请在多源查询页补充坐标或扩大半径。'
    text = f"预测首选企业：{top['名称']}（初步预测，建议优先排查）。依据：" + '；'.join(top['支持依据']) + '。'
    others = [row['名称'] for row in prediction['候选企业'][1:3]]
    if others:
        text += '备选：' + '、'.join(others) + '。'
    if len(prediction['同分候选']) > 1:
        text += '同分候选：' + '、'.join(prediction['同分候选']) + '，现有依据不能区分先后。'
    return text + '这是候选预测，不代表已确认排放或责任。'
