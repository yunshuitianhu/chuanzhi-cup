"""河道工具的公共 Function Calling 定义；Web 与 Streamlit 共用。"""


def _spec(name, description, properties):
    return {'type': 'function', 'function': {
        'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties,
                       'additionalProperties': False},
    }}


RIVER_TOOL_SPECS = [
    _spec('init_river_model', '初始化河道模型。auto_detect=true 时从已上传数据推导参数。', {
        'n_cross_sections': {'type': 'integer', 'minimum': 3, 'maximum': 100},
        'mannings_n': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 0.2},
        'auto_detect': {'type': 'boolean'},
    }),
    _spec('run_hydrodynamic_simulation',
          '运行已初始化模型的恒定流模拟。潮汐求解器尚未通过质量守恒验收，不得用于报告。', {
        'upstream_flow_m3s': {'type': 'number'},
        'downstream_stage_m': {'type': 'number'},
        'tidal_amplitude_m': {'type': 'number', 'enum': [0]},
        'tidal_period_h': {'type': 'number', 'exclusiveMinimum': 0},
        'duration_h': {'type': 'number', 'minimum': 0.5, 'maximum': 48},
        'dt_s': {'type': 'number', 'minimum': 1, 'maximum': 300},
    }),
    _spec('simulate_pollution_event', '基于恒定流水动力结果模拟污染事件，仅作情景分析。', {
        'chainage_m': {'type': 'number', 'minimum': 0},
        'pollutant_type': {'type': 'string', 'enum': ['ammonia', 'cbod']},
        'load_kg': {'type': 'number', 'minimum': 0},
        'duration_min': {'type': 'number', 'exclusiveMinimum': 0},
        'source_start_h': {'type': 'number', 'minimum': 0,
                           'description': '相对模拟起点的排放开始时刻，单位小时'},
        'output_interval_s': {'type': 'number', 'minimum': 1, 'maximum': 3600},
        'simulation_hours': {'type': 'number', 'minimum': 0.5, 'maximum': 48},
    }),
    _spec('get_concentration_profile', '查询模拟结果在指定时刻的沿程浓度。', {
        'time_h': {'type': 'number', 'minimum': 0},
        'constituent': {'type': 'string', 'enum': ['ammonia', 'cbod', 'do']},
    }),
    _spec('compare_scenarios', '对比两个已保存模拟情景的参数。', {
        'scenario1_name': {'type': 'string'}, 'scenario2_name': {'type': 'string'},
    }),
    _spec('export_river_model_data', '导出当前模拟数据为 JSON 或 CSV 文件。', {
        'format': {'type': 'string', 'enum': ['json', 'csv']},
        'scenario_name': {'type': 'string'},
    }),
]
