"""河道模型工具包装函数 — 供 app.py 和 MCP server 调用。

每个函数返回 JSON 字符串, 遵循现有的 tool 返回格式 (≤2000 字符摘要)。
"""

import json
import os
import uuid
import tempfile
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from .config import RiverConfig, DEFAULT_CONFIG
from .cross_sections import generate_cross_sections
from .hydro_engine import run_hydrodynamic, get_engine_info
from .state import RiverModelState
from .visualization import (
    plot_water_level_profile,
    plot_discharge_profile,
    plot_concentration_contour,
    plot_concentration_profile,
    plot_concentration_timeseries,
    plot_hydro_wq_dashboard,
)
from .wq_engine import solve_water_quality
from .standards import constituent_limit

_PLOT_LOCK = threading.Lock()  # matplotlib有全局状态；不同项目绘图也应互斥。


def _generate_files(jobs, output_dir=None):
    """只发布本次成功生成的文件，旧文件不能冒充本次输出。"""
    generated, warnings = [], []
    directory = Path(output_dir) if output_dir else Path.cwd()
    directory.mkdir(parents=True, exist_ok=True)
    for filename, generate in jobs:
        try:
            with tempfile.TemporaryDirectory(prefix='.river-output-', dir=directory) as tmp:
                candidate = Path(tmp) / filename
                with _PLOT_LOCK:
                    generate(str(candidate))
                if not candidate.is_file() or candidate.stat().st_size == 0:
                    raise ValueError('生成器未写入有效文件')
                destination = directory / filename
                os.replace(candidate, destination)
            generated.append(str(destination) if output_dir else filename)
        except Exception as exc:
            warnings.append({'文件': filename, '原因': str(exc)})
    return generated, warnings


# ---- Tool 1: 初始化模型 ----

def tool_river_init(
    state: RiverModelState,
    n_cross_sections: int = 50,
    mannings_n: float = None,
    auto_detect: bool = False,
    auto_detect_data: "pd.DataFrame | None" = None,
    auto_detect_gps_mapping: dict = None,
    amap_key: str = None,
    cloud_token: str = None,
    parameters: dict | None = None,
) -> str:
    """初始化通用河段模型。

    parameters 为用户情景参数；auto_detect 可从当前 DataFrame/GPS 给出初始建议。
    缺失 GPS 或参数估计失败时报错，不自动套用其它河道参数；无输入时使用明确的理想模板。
    """
    if not 3 <= n_cross_sections <= 100 or (mannings_n is not None and not 0 < mannings_n <= .2):
        return json.dumps({'error': '断面数须为 3~100，糙率须在 (0, 0.2]'}, ensure_ascii=False)
    state.model_initialized = False
    state.invalidate_hydro()
    state.cross_sections = []
    state.geometry_revision += 1
    if auto_detect:
        # ---- 自动检测模式 ----
        try:
            from .parameter_estimator import (
                RiverParameterEstimator,
                estimate_config_from_data,
                estimate_config_from_gps,
            )

            if auto_detect_data is not None:
                config = estimate_config_from_data(
                    auto_detect_data, amap_key=amap_key, cloud_token=cloud_token
                )
                source_info = f"自动检测 (来自 {len(auto_detect_data)} 条监测记录)"
            elif auto_detect_gps_mapping is not None:
                config = estimate_config_from_gps(
                    auto_detect_gps_mapping, amap_key=amap_key, cloud_token=cloud_token
                )
                source_info = f"自动检测 (来自 {auto_detect_gps_mapping.get('point_count', '?')} 个GPS点)"
            else:
                raise ValueError('缺少参数估计数据，请提供走航 GPS 或使用手工河段配置')

            # 如果用户指定了 mannings_n, 覆盖自动检测值
            if mannings_n is not None:
                config.mannings_n = mannings_n
                config.param_sources["mannings_n"] = "user_scenario"
                config.param_confidence.pop('mannings_n', None)

            # 使用检测到的参数
            n_cs = n_cross_sections
            length_m = config.length_m
            slope = config.longitudinal_slope
            bed_el = config.bed_elevation_avg_m
            bw_m = getattr(config, "bottom_width_m", config.width_avg_m * 0.73)

        except Exception as e:
            return json.dumps({'error': f'参数估计失败: {e}；请核实 GPS 或使用手工配置'}, ensure_ascii=False)

    else:
        # ---- 传统模式 ----
        config = RiverConfig(
            n_cross_sections=n_cross_sections,
            mannings_n=mannings_n if mannings_n is not None else 0.028,
        )
        n_cs = n_cross_sections
        length_m = config.length_m
        slope = config.longitudinal_slope
        bed_el = config.bed_elevation_avg_m
        bw_m = getattr(config, "bottom_width_m", 40.0)
        source_info = "通用情景模板，未经现场率定"

    try:
        from .parameters import configure_river
        config = configure_river(config, parameters)
        n_cs, length_m, slope, bed_el, bw_m = (n_cross_sections, config.length_m,
            config.longitudinal_slope, config.bed_elevation_avg_m, config.bottom_width_m)
        config.n_cross_sections = n_cs
        if parameters:
            source_info = '用户设置的河段情景参数，未经现场率定'
    except (ValueError, TypeError) as exc:
        return json.dumps({'error': str(exc)}, ensure_ascii=False)

    # 生成断面
    cs_list = generate_cross_sections(
        n_total=n_cs,
        channel_length_m=length_m,
        bed_elevation_avg_m=bed_el,
        bed_slope=slope,
        bottom_width_m=bw_m,
        mannings_n=config.mannings_n,
        bank_elevation_m=config.bank_elevation_m,
        side_slope=config.side_slope,
    )
    state.config = config
    state.cross_sections = cs_list
    state.model_initialized = True
    state.engine_type = get_engine_info()

    # 引擎标签：state.engine_type 来自 get_engine_info()，它描述的是**本机能力**
    # （HEC-RAS COM 是否可用），而非本次模拟实际会用哪个引擎。
    # 因为非恒定流（潮汐）工况一律强制走纯 Python 求解器（见 hydro_engine），
    # 直接给一个 "HEC-RAS" 会让用户以为潮汐模拟也是 HEC-RAS 算的。
    # 实测过：初始化输出写着 "计算引擎 = HEC-RAS"，随后水动力工具的返回里
    # 却是 "引擎 = pure_python_preissmann"，前后矛盾。
    engine_label = state.engine_type

    # 参数来源摘要 (自动检测模式)
    extra_info = {}
    if config.param_sources:
        extra_info["参数来源"] = source_info
        extra_info["参数置信度"] = config.param_confidence

    return json.dumps({
        "状态": "模型已初始化",
        "河道名称": config.river_name,
        "位置": config.location,
        "河段长度_km": config.length_km,
        "断面数": len(cs_list),
        "断面间距_m": round(length_m / (len(cs_list) - 1), 1) if len(cs_list) > 1 else 0,
        "平均河宽_m": config.width_avg_m,
        "平均水深_m": config.depth_avg_m,
        "河底宽_m": round(bw_m, 1),
        "曼宁糙率": config.mannings_n,
        "纵向坡降": config.longitudinal_slope,
        "下游潮差_m": config.tidal_range_m,
        "平均流量_m3s": config.avg_discharge_m3s,
        "计算引擎": engine_label,
        **extra_info,
        "提示": "模型就绪。可调用 run_hydrodynamic_simulation 运行水动力模拟, "
                "或 simulate_pollution_event 模拟污染事件。",
    }, ensure_ascii=False)


# ---- Tool 2: 水动力模拟 ----

def tool_river_hydro_simulation(
    state: RiverModelState,
    upstream_flow_m3s: Optional[float] = None,
    downstream_stage_m: Optional[float] = None,
    tidal_amplitude_m: Optional[float] = None,
    tidal_period_h: Optional[float] = None,
    duration_h: float = 24.0,
    dt_s: float = 60.0,
) -> str:
    """运行一维水动力模拟。

    四个边界参数默认取 **state.config 的值**（可能是从走航数据自动推导出来的），
    而不是写死的常数。原先写死导致：自动检测推导出的流量/潮差**根本进不了
    求解器** —— 求解器永远收到 6.9 / 0.83 / 0.8 / 12.42。

    传了显式值仍以显式值为准（向后兼容）。
    """
    if not state.model_initialized:
        return json.dumps({"error": "请先调用 init_river_model 初始化模型"}, ensure_ascii=False)

    state.invalidate_hydro()  # 旧水质和情景不属于这次边界条件，失败后也不可继续使用。
    # 未显式指定时回落到 config。
    # ⚠️ config 的默认值恰好等于原先写死的常数
    #    （avg_discharge 6.9 / high_water 0.83 / tidal_range÷2 = 0.8 / m2_period 12.42），
    #    因此对本项目样本数据的输出**完全不变**；只有自动推导出别的值时才不同。
    cfg = state.config
    if upstream_flow_m3s is None:
        upstream_flow_m3s = cfg.avg_discharge_m3s
    if downstream_stage_m is None:
        downstream_stage_m = cfg.high_water_mean_m
    if tidal_amplitude_m is None:
        tidal_amplitude_m = 0.0
    if tidal_period_h is None:
        tidal_period_h = cfg.m2_period_h

    try:
        result = run_hydrodynamic(
            state.config, state.cross_sections,
            upstream_flow_m3s, downstream_stage_m,
            tidal_amplitude_m, tidal_period_h,
            duration_h, dt_s, 30.0,
        )
    except Exception as e:
        return json.dumps({"error": f"水动力模拟失败: {str(e)}"}, ensure_ascii=False)

    result.params.update(run_id=uuid.uuid4().hex, geometry_revision=state.geometry_revision)
    state.hydro_result = result

    files, warnings = _generate_files([
        ('river_hydro_profile.png', lambda path: plot_water_level_profile(result, state.config, path)),
        ('river_discharge_profile.png', lambda path: plot_discharge_profile(result, path)),
    ], state.output_dir)

    # 统计摘要
    wl_max = float(np.max(result.water_level))
    wl_min = float(np.min(result.water_level))
    v_max = float(np.max(result.velocity))
    v_mean = float(np.mean(np.abs(result.velocity)))
    q_max = float(np.max(np.abs(result.discharge)))

    return json.dumps({
        "状态": "水动力模拟完成",
        "模拟时长_h": duration_h,
        "时间步长_s": dt_s,
        "输出帧数": len(result.t),
        "最高水位_m": round(wl_max, 2),
        "最低水位_m": round(wl_min, 2),
        "水位变幅_m": round(wl_max - wl_min, 2),
        "最大流速_ms": round(v_max, 3),
        "平均流速_ms": round(v_mean, 3),
        "最大流量_m3s": round(q_max, 1),
        "引擎": result.params.get("engine", "unknown"),
        "输出文件": files,
        "文件生成说明": warnings,
        "提示": "水动力场就绪。可调用 simulate_pollution_event 模拟污染事件。",
    }, ensure_ascii=False)


# ---- Tool 3: 污染事件模拟 ----

def tool_river_pollution_event(
    state: RiverModelState,
    chainage_m: float | None = None,
    pollutant_type: str = "ammonia",
    load_kg: float = 50.0,
    duration_min: float = 30.0,
    simulation_hours: float = 24.0,
    target_water_class: str = 'V',
    monitoring_data=None,
    source_start_h: float = 0.0,
    output_interval_s: float = 60.0,
) -> str:
    """在当前河段指定位置模拟污染事件。"""
    if not state.has_hydro():
        return json.dumps({
            "error": "请先运行 run_hydrodynamic_simulation",
            "hint": "水动力模拟提供流速场, 是水质模拟的前提",
        }, ensure_ascii=False)

    valid_types = ["ammonia", "cbod"]
    if pollutant_type not in valid_types:
        return json.dumps({"error": f"无效污染物类型。可选: {valid_types}"}, ensure_ascii=False)

    state.wq_result = None
    if chainage_m is None:
        chainage_m = float(np.mean([state.hydro_result.chainage[0], state.hydro_result.chainage[-1]]))
    try:
        std_limit = constituent_limit(pollutant_type, target_water_class)
        result = solve_water_quality(
            state.config,
            state.hydro_result,
            pollutant_type=pollutant_type,
            load_kg=load_kg,
            chainage_m=chainage_m,
            duration_min=duration_min,
            simulation_hours=simulation_hours,
            dt_s=60.0,
            source_start_h=source_start_h,
            output_interval_s=output_interval_s,
        )
    except Exception as e:
        return json.dumps({"error": f"水质模拟失败: {str(e)}"}, ensure_ascii=False)

    result.params.update(hydro_run_id=state.hydro_result.params.get('run_id'),
                         hydro_tidal_amplitude_m=state.hydro_result.params.get('tidal_amplitude_m', 0))
    state.wq_result = result
    scenario_name = f"{pollutant_type}_{load_kg}kg_{chainage_m}m_start{source_start_h}h_dur{duration_min}min"
    state.scenarios[scenario_name] = result.params

    # 每张图独立生成；某张失败不会阻止其余图，也不会把旧文件列作新结果。
    stations = len(result.chainage)
    jobs = [
        ('river_wq_contour.png', lambda path: plot_concentration_contour(
            result, pollutant_type, path, state.config)),
        ('river_wq_profile.png', lambda path: plot_concentration_profile(
            result, simulation_hours * .5, pollutant_type, path, state.config)),
        ('river_wq_timeseries.png', lambda path: plot_concentration_timeseries(
            result, [0, stations // 4, stations // 2, 3 * stations // 4, stations - 1],
            pollutant_type, path)),
        ('river_dashboard.png', lambda path: plot_hydro_wq_dashboard(
            state.hydro_result, result, path, state.config)),
    ]

    def generate_animation(path):
        if monitoring_data is None or len(monitoring_data) == 0:
            raise ValueError('当前会话没有提供监测轨迹，已跳过动画')
        from .geo_mapping import build_chainage_gps_mapping
        from .animation import build_pollution_map_animation
        gps_mapping = build_chainage_gps_mapping(monitoring_data, smoothing_sigma=3.0)
        if float(result.chainage[-1]) > gps_mapping['total_length_m'] + 1:
            raise ValueError('当前轨迹的桩号范围未覆盖模拟河段；请核实映射，已跳过动画')
        build_pollution_map_animation(gps_mapping, result, pollutant_type,
                                     source_chainage_m=chainage_m, output_path=path, max_frames=40)

    jobs.append(('river_pollution_animation.html', generate_animation))
    files, warnings = _generate_files(jobs, state.output_dir)

    # 分析影响
    if pollutant_type == "ammonia" and result.ammonia is not None:
        data = result.ammonia
    elif pollutant_type in ("cbod", "cod") and result.cbod is not None:
        data = result.cbod
    else:
        data = result.ammonia if result.ammonia is not None else result.cbod

    peak_c = float(np.max(data)) if data is not None else 0.0
    peak_time_idx = int(np.argmax(np.max(data, axis=1))) if data is not None else 0
    peak_time_h = float(result.t[peak_time_idx])

    # 影响范围 —— 同时给出两把尺子的结果。
    #
    # 这里原来只报"相对监测基线 1.5 倍"的断面数。当监测基线本身已经很高时，
    # 该指标会给出反直觉的 0，与同一份 JSON 里的"超标倍数"（以 GB3838 限值
    # 为基准）直接冲突。实测（真实样本数据，氨氮基线中位数 4.6 mg/L）：
    #   峰值 5.30 mg/L -> 超标倍数 2.7（对 V 类标准 2.0）
    #                  -> 显著影响断面数 0（对基线 1.5 倍 = 6.9）
    # 两个数字各自都对，但放在一起会被读成矛盾。故两个口径都输出、并写明基准。
    n_affected = 0
    affected_chainage_range = (0, 0)
    n_exceed_std = 0
    exceed_std_chainage_range = (0, 0)
    if data is not None:
        baseline = data[0, :]

        # 口径一：相对监测基线（回答"这次排放改变了什么"）
        affected_mask = np.any(data > baseline * 1.5, axis=0)
        n_affected = int(np.sum(affected_mask))
        affected_chainage_range = (
            float(result.chainage[affected_mask][0]) if n_affected > 0 else 0,
            float(result.chainage[affected_mask][-1]) if n_affected > 0 else 0,
        )

        # 口径二：相对标准限值（回答"水体是否超标"）
        exceed_mask = np.any(data > std_limit, axis=0)
        n_exceed_std = int(np.sum(exceed_mask))
        exceed_std_chainage_range = (
            float(result.chainage[exceed_mask][0]) if n_exceed_std > 0 else 0,
            float(result.chainage[exceed_mask][-1]) if n_exceed_std > 0 else 0,
        )

    # 这里检测的是下游浓度首次超过初始值 1.3 倍，不是污染峰到达。
    arrival_h = None
    if data is not None:
        last_cs = data[:, -1]
        exceed_factor = 1.3
        exceeded = np.where(last_cs > last_cs[0] * exceed_factor)[0]
        arrival_h = float(result.t[exceeded[0]]) if len(exceeded) > 0 else None

    return json.dumps({
        "状态": "污染事件模拟完成",
        "反应模型": result.params.get('reaction_model', {}),
        "反应核算": result.params.get('reaction_diagnostics', {}),
        "质量核算": result.params.get('mass_balance', {}),
        "污染物": pollutant_type,
        "排放量_kg": load_kg,
        "排放位置_m": chainage_m,
        "排放时长_min": duration_min,
        "排放开始_h": source_start_h,
        "实际注入量_kg": result.params.get('mass_balance', {}).get(pollutant_type, {}).get('injected_kg'),
        "源位置离散": {
            "实际计算桩号_m": result.params.get('source_effective_chainage_m'),
            "与请求位置距离_m": result.params.get('source_snap_distance_m'),
            "说明": "负荷注入最近断面；断面间距限制源位置分辨率",
        },
        "输出时间点数": len(result.t),
        "输出间隔_s": output_interval_s,
        "模拟时长_h": simulation_hours,
        "峰值浓度_mgL": round(peak_c, 2),
        "峰值时刻_h": round(peak_time_h, 4),
        "超标倍数": round(peak_c / std_limit, 1) if std_limit > 0 else 0,
        # 两个口径分别标注基准，避免被读成互相矛盾
        "相对标准限值": {
            "目标水质": target_water_class,
            "基准_mgL": std_limit,
            "说明": ("CBOD 与 BOD 标准不是同一量测口径，仅作参考，不能据此认定达标"
                     if pollutant_type == 'cbod' else "浓度超过所选等级标准限值的断面"),
            "断面数": n_exceed_std,
            "桩号范围_m": list(exceed_std_chainage_range),
        },
        "相对监测基线": {
            "基准_mgL": round(float(data[0, :].max()), 2) if data is not None else 0.0,
            "说明": "浓度超过监测基线 1.5 倍的断面（衡量本次排放带来的增量影响）",
            "断面数": n_affected,
            "桩号范围_m": list(affected_chainage_range),
        },
        "下游到达时间_h": round(arrival_h, 4) if arrival_h is not None else None,
        "下游到达判定": {
            "已达到阈值": arrival_h is not None,
            "判定口径": "下游末断面浓度首次超过该断面初始浓度的 1.3 倍；不代表污染峰到达",
            "说明": "已达到浓度阈值" if arrival_h is not None else "本模拟时段内未达到浓度阈值",
        },
        "情景名称": scenario_name,
        "输出文件": files,
        "文件生成说明": warnings,
        "提示": (f"峰值浓度 {peak_c:.1f} mg/L，为标准({std_limit} mg/L)的 {peak_c/std_limit:.1f} 倍；"
                 f"相对监测基线则放大至 {peak_c / max(float(data[0, :].max()), 1e-9):.2f} 倍"
                 f"（基线 {float(data[0, :].max()):.1f} mg/L 本身可能已超标，故两者结论未必一致）。"
                 if data is not None else ""),
    }, ensure_ascii=False)


# ---- Tool 4: 浓度剖面查询 ----

def tool_river_concentration_profile(
    state: RiverModelState,
    time_h: float = 6.0,
    constituent: str = "ammonia",
    target_water_class: str = 'V',
) -> str:
    """查询指定时刻的沿程浓度剖面。"""
    if not state.has_wq():
        return json.dumps({"error": "请先运行 simulate_pollution_event"}, ensure_ascii=False)

    wq = state.wq_result
    try:
        std = constituent_limit(constituent, target_water_class)
    except ValueError as exc:
        return json.dumps({'error': str(exc)}, ensure_ascii=False)
    if constituent == "ammonia":
        data = wq.ammonia
    elif constituent in ("cbod", "cod"):
        data = wq.cbod
    elif constituent == "do":
        data = wq.dissolved_oxygen
    else:
        return json.dumps({"error": f"未知组分: {constituent}"}, ensure_ascii=False)

    if data is None:
        return json.dumps({"error": f"组分 {constituent} 数据不可用"}, ensure_ascii=False)

    from .observations import sample_water_quality
    try:
        profile = sample_water_quality(wq, np.full(len(wq.chainage), time_h), wq.chainage, constituent)
    except ValueError as exc:
        return json.dumps({'error': str(exc)}, ensure_ascii=False)
    chainage = wq.chainage

    # 超标断面
    exceeded_mask = profile < std if constituent == 'do' else profile > std
    exceeded_sections = [
        {"桩号_m": round(float(chainage[i]), 0), "浓度": round(float(profile[i]), 2)}
        for i in range(len(profile)) if exceeded_mask[i]
    ]

    return json.dumps({
        "时刻_h": float(time_h),
        "取值方法": "在模型已保存的时间点间线性插值；超出模拟范围时拒绝查询",
        "组分": constituent,
        "标准限值_mgL": std,
        "目标水质": target_water_class,
        "限值方向": '下限' if constituent == 'do' else '上限',
        "口径说明": 'CBOD 使用 BOD 参考值，不能据此认定达标' if constituent == 'cbod' else '所选等级限值',
        "沿程浓度": [round(float(c), 2) for c in profile[::5]],  # 抽样
        "桩号_m": [round(float(ch), 0) for ch in chainage[::5]],
        "最大浓度_mgL": round(float(np.max(profile)), 2),
        "最大浓度位置_m": round(float(chainage[int(np.argmax(profile))]), 0),
        "超标断面数": int(np.sum(exceeded_mask)),
        "超标断面": exceeded_sections[:8],  # 最多8个
        "影响范围_m": round(float(
            chainage[exceeded_mask][-1] - chainage[exceeded_mask][0]
        ) if np.sum(exceeded_mask) >= 1 else 0, 0),
    }, ensure_ascii=False)


# ---- Tool 5: 情景对比 ----

def tool_river_compare_scenarios(
    state: RiverModelState,
    scenario1_name: str = "",
    scenario2_name: str = "",
) -> str:
    """对比两个已保存的模拟情景。"""
    if not state.scenarios:
        return json.dumps({"error": "没有已保存的情景。请先运行 simulate_pollution_event"}, ensure_ascii=False)

    names = list(state.scenarios.keys())
    if not scenario1_name and len(names) >= 1:
        scenario1_name = names[-1] if len(names) >= 2 else names[0]
    if not scenario2_name and len(names) >= 2:
        scenario2_name = names[-2]
    elif not scenario2_name:
        return json.dumps({
            "error": "需要至少两个情景才能对比",
            "可用情景": names,
        }, ensure_ascii=False)

    s1 = state.scenarios.get(scenario1_name, {})
    s2 = state.scenarios.get(scenario2_name, {})

    return json.dumps({
        "情景1": {"名称": scenario1_name, **s1},
        "情景2": {"名称": scenario2_name, **s2},
        "可用情景列表": names,
    }, ensure_ascii=False)


# ---- Tool 6: 数据导出 ----

def tool_river_export_data(
    state: RiverModelState,
    format: str = "json",
    scenario_name: str = "latest",
) -> str:
    """导出模拟结果数据。"""
    wq = state.wq_result
    if wq is None:
        return json.dumps({"error": "没有可用的模拟结果"}, ensure_ascii=False)

    if format == "json":
        # 导出为精简 JSON (浓度沿程剖面, 所有时刻)
        export = {
            "chainage_m": wq.chainage.tolist(),
            "time_h": wq.t.tolist(),
            "model_metadata": wq.params,
            "units": {"ammonia_mgL": "mgN/L", "nitrate_n_mgL": "mgN/L",
                      "cbod_mgL": "mgO2/L", "do_mgL": "mgO2/L"},
        }
        if wq.ammonia is not None:
            export["ammonia_mgL"] = wq.ammonia.tolist()
        if wq.cbod is not None:
            export["cbod_mgL"] = wq.cbod.tolist()
        if wq.dissolved_oxygen is not None:
            export["do_mgL"] = wq.dissolved_oxygen.tolist()
        if wq.nitrate is not None:
            export["nitrate_n_mgL"] = wq.nitrate.tolist()

        out_path = str(Path(state.output_dir) / 'river_model_export.json') if state.output_dir else 'river_model_export.json'
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(export, f, ensure_ascii=False, indent=2)
        return json.dumps({
            "状态": "数据已导出",
            "文件": out_path,
            "格式": "JSON",
            "大小_字节": os.path.getsize(out_path),
        }, ensure_ascii=False)

    elif format == "csv":
        import pandas as pd
        out_path = str(Path(state.output_dir) / 'river_model_export.csv') if state.output_dir else 'river_model_export.csv'
        rows = []
        for ti in range(len(wq.t)):
            for ci in range(len(wq.chainage)):
                row = {"time_h": wq.t[ti], "chainage_m": wq.chainage[ci]}
                if wq.cbod is not None:
                    row["cbod_mgL"] = wq.cbod[ti, ci]
                if wq.ammonia is not None:
                    row["ammonia_mgL"] = wq.ammonia[ti, ci]
                if wq.dissolved_oxygen is not None:
                    row["do_mgL"] = wq.dissolved_oxygen[ti, ci]
                if wq.nitrate is not None:
                    row["nitrate_n_mgL"] = wq.nitrate[ti, ci]
                rows.append(row)
        df = pd.DataFrame(rows)
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        return json.dumps({
            "状态": "数据已导出",
            "文件": out_path,
            "格式": "CSV",
            "行数": len(df),
        }, ensure_ascii=False)

    else:
        return json.dumps({"error": f"不支持的格式: {format}, 可选 'json' 或 'csv'"}, ensure_ascii=False)


# ---- Tool 7: 地图污染扩散动画 ----

def tool_river_map_animation(
    state: RiverModelState,
    excel_path: str = "苗圃杯/无人船智能水质监测分析和因果溯源样本数据/河道巡航走航样本数据.xlsx",
    constituent: str = "ammonia",
    output_path: str = "river_pollution_animation.html",
    source_chainage_m: float | None = None,
    dual_view: bool = True,
    max_frames: int = 60,
) -> str:
    """生成污染扩散地图动画 HTML。

    基于走航船 GPS 轨迹和 ADR 污染模拟结果，
    生成带时间轴的 Folium 地图动画和 Plotly 浓度剖面图。
    """
    if not state.has_wq():
        return json.dumps({
            "error": "请先运行 simulate_pollution_event",
            "hint": "需要污染模拟结果作为动画数据源",
        }, ensure_ascii=False)

    # 推断污染源桩号
    wq = state.wq_result
    if source_chainage_m is None and wq.params:
        source_chainage_m = wq.params.get("chainage_m", float((wq.chainage[0]+wq.chainage[-1])/2))

    try:
        from .geo_mapping import build_chainage_gps_mapping
        from .animation import build_pollution_map_animation, build_dual_view_animation

        # 第一步: 建立 GPS 映射
        gps_mapping = build_chainage_gps_mapping(excel_path, smoothing_sigma=3.0)

        # 第二步: 生成动画
        if dual_view:
            result_path = build_dual_view_animation(
                gps_mapping, wq, constituent,
                source_chainage_m=source_chainage_m,
                output_path=output_path,
                max_frames=max_frames,
            )
        else:
            result_path = build_pollution_map_animation(
                gps_mapping, wq, constituent,
                source_chainage_m=source_chainage_m,
                output_path=output_path,
                max_frames=max_frames,
            )

        file_size_mb = round(os.path.getsize(result_path) / 1048576, 2)
        return json.dumps({
            "状态": "动画已生成",
            "HTML文件": result_path,
            "大小_MB": file_size_mb,
            "组分": constituent,
            "时间帧数": min(max_frames, len(wq.t)),
            "时间范围_h": f"{wq.t[0]:.1f} ~ {wq.t[-1]:.1f}",
            "河道长度_km": round(gps_mapping["total_length_m"] / 1000, 2),
            "GPS点数": gps_mapping["point_count"],
            "污染源桩号_m": source_chainage_m,
            "标准": "GB3838-2002",
            "播放": "用浏览器打开 HTML 文件，点击播放按钮即可观看",
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": f"动画生成失败: {str(e)}"}, ensure_ascii=False)
