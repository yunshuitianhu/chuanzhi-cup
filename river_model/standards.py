"""模拟结果的指标判定，使用与检测相同的等级配置。"""
from water_quality_mcp.src.water_quality_mcp.analysis_config import GB3838_STANDARDS


def constituent_limit(constituent, target_water_class='V'):
    keys = {'ammonia': 'nh3', 'do': 'do', 'cbod': 'bod'}
    if constituent not in keys:
        raise ValueError(f'没有独立的 {constituent} 模拟组分，不能用其他指标冒充')
    if target_water_class not in GB3838_STANDARDS:
        raise ValueError('无效目标水质等级')
    return GB3838_STANDARDS[target_water_class][keys[constituent]]
