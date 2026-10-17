"""河道模型路由：参数自动检测、水动力模拟、污染事件、浓度剖面。

⚠️ 已知局限（必须随响应一起返回给前端，不得省略）
------------------------------------------------
本模型的**非恒定（潮汐）工况存在已确认的数值缺陷**：连续性方程遗漏
水面宽 `B_mid`，导致质量不守恒（实测残差达蓄水率的 103%）。
详见 `docs/代码审计与修改方案.md` 第 2.8 节。

因此：
  - **恒定流工况（tidal_amplitude_m=0）** 仅作情景分析，样例不代表实际河道已标定
  - **非恒定/潮汐工况** 的结果不可作为结论依据
作品的说明文档与演示中不应把"潮汐模拟"作为宣传点。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from typing import Literal

from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key

from ..deps import get_session, get_exclusive_session
from ..session import Session

router = APIRouter(prefix="/river", tags=["river"])

MODEL_CAVEAT = (
    "非恒定（潮汐）工况存在已确认的数值缺陷：连续性方程遗漏水面宽，"
    "质量不守恒（残差达蓄水率 103%）。恒定流仅作情景分析，实际河道仍需校准；"
    "潮汐工况结果不可作为结论依据。详见 docs/代码审计与修改方案.md 第 2.8 节。"
)


def _json(raw) -> dict:
    result = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(result, dict):
        raise HTTPException(502, '模型返回格式无效')
    if 'error' in result:
        raise HTTPException(400, result['error'])
    return result


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class InitRequest(ModelRequest):
    n_cross_sections: int = Field(50, ge=3, le=100)
    mannings_n: FiniteFloat | None = Field(None, gt=0, le=.2)
    auto_detect: bool = False


class HydroRequest(ModelRequest):
    upstream_flow_m3s: FiniteFloat | None = None
    downstream_stage_m: FiniteFloat | None = None
    tidal_amplitude_m: FiniteFloat = Field(0, ge=0, le=10)
    tidal_period_h: FiniteFloat | None = Field(None, gt=0)
    duration_h: FiniteFloat = Field(6, ge=.5, le=48)
    dt_s: FiniteFloat = Field(60, ge=1, le=300)


class PollutionRequest(ModelRequest):
    chainage_m: FiniteFloat | None = Field(None, ge=0)
    pollutant_type: Literal['ammonia', 'cbod'] = 'ammonia'
    load_kg: FiniteFloat = Field(50, ge=0)
    duration_min: FiniteFloat = Field(30, gt=0)
    simulation_hours: FiniteFloat = Field(6, ge=.5, le=48)


@router.get("/params")
def detect_params(session: Session = Depends(get_session)) -> dict:
    """从走航数据自动推断河道参数（6 层检测管道）。

    最高优先级是"已知河道坐标库"—— 坐标落在某条已知河道的 bbox 内时
    直接返回其标定参数（置信度 0.95），无需联网。
    """
    if session.df is None or len(session.df) == 0:
        raise HTTPException(400, "数据未加载")

    from river_model.parameter_estimator import estimate_config_from_data

    cfg = estimate_config_from_data(
        session.df,
        amap_key=resolve_api_key("AMAP_KEY"),
        cloud_token=resolve_api_key("CLOUD_TOKEN"),
    )
    return {
        "river_name": cfg.river_name,
        "location": cfg.location,
        "length_m": cfg.length_m,
        "width_avg_m": cfg.width_avg_m,
        "depth_avg_m": cfg.depth_avg_m,
        "mannings_n": cfg.mannings_n,
        "longitudinal_slope": cfg.longitudinal_slope,
        "avg_discharge_m3s": cfg.avg_discharge_m3s,
        "tidal_range_m": cfg.tidal_range_m,
        "param_sources": cfg.param_sources,
        "param_confidence": cfg.param_confidence,
        "局限说明": MODEL_CAVEAT,
    }


@router.post("/init")
def init_model(payload: InitRequest | None = None,
               session: Session = Depends(get_exclusive_session)) -> dict:
    """初始化模型（生成断面序列）。auto_detect=true 时从数据推断参数。"""
    payload = payload or InitRequest()
    from river_model.tools import tool_river_init

    kwargs = payload.model_dump()
    if kwargs["auto_detect"]:
        if session.df is None or len(session.df) == 0:
            raise HTTPException(400, "自动检测模式需要先加载走航数据")
        kwargs["auto_detect_data"] = session.df
        kwargs["amap_key"] = resolve_api_key("AMAP_KEY")
        kwargs["cloud_token"] = resolve_api_key("CLOUD_TOKEN")

    result = _json(tool_river_init(session.get_river_state(), **kwargs))
    return {**result, "局限说明": MODEL_CAVEAT}


@router.post("/hydro")
def hydro(payload: HydroRequest | None = None,
          session: Session = Depends(get_exclusive_session)) -> dict:
    """运行水动力模拟。

    默认 `tidal_amplitude_m=0`（恒定流情景）。若要跑潮汐工况，
    需显式传入非零值，并留意返回的局限说明。
    """
    payload = payload or HydroRequest()
    from river_model.tools import tool_river_hydro_simulation

    st = session.get_river_state()
    if not getattr(st, "model_initialized", False):
        raise HTTPException(400, "请先调用 /api/river/init 初始化模型")

    result = _json(tool_river_hydro_simulation(st, **payload.model_dump(exclude_none=True)))
    amp = payload.tidal_amplitude_m
    return {
        **result,
        "工况": "非恒定（潮汐）" if amp else "恒定流",
        "结果可信度": MODEL_CAVEAT if amp else "恒定流情景模拟；样例验收不代表真实河道已标定，请核查断面和边界条件",
    }


@router.post("/pollution")
def pollution(payload: PollutionRequest | None = None,
              session: Session = Depends(get_exclusive_session)) -> dict:
    """模拟污染事件（ADR 对流-扩散-反应模型）。"""
    payload = payload or PollutionRequest()
    from river_model.tools import tool_river_pollution_event

    st = session.get_river_state()
    if not getattr(st, "has_hydro", lambda: False)():
        raise HTTPException(400, "请先运行水动力模拟")

    kwargs = payload.model_dump(exclude_none=True)
    kwargs.setdefault('chainage_m', float((st.hydro_result.chainage[0] + st.hydro_result.chainage[-1]) / 2))
    result = _json(tool_river_pollution_event(st, **kwargs, target_water_class=session.target_water_class,
                                            monitoring_data=session.df))
    return result


@router.get("/profile")
def profile(time_h: float = 6.0, constituent: str = "ammonia",
            session: Session = Depends(get_session)) -> dict:
    """查询指定时刻的沿程浓度剖面。"""
    from river_model.tools import tool_river_concentration_profile

    st = session.get_river_state()
    if not getattr(st, "has_wq", lambda: False)():
        raise HTTPException(400, "请先模拟污染事件")
    return _json(tool_river_concentration_profile(st, time_h, constituent, session.target_water_class))
