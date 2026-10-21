"""会话配准配置与只读重算；配置随已有river_ui项目字段保存。"""
from typing import Literal
import json

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, field_validator

from river_model.cruise_alignment import align_cruise


class AlignmentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    reference_path: list[list[FiniteFloat]] = Field(min_length=2, max_length=500)
    model_origin: AwareDatetime = Field(strict=False)
    monitoring_timezone: str = Field('Asia/Shanghai', min_length=1, max_length=80)
    time_offset_s: FiniteFloat = Field(0., ge=-86400, le=86400)
    max_distance_m: FiniteFloat = Field(50., ge=.5, le=500)
    ambiguity_m: FiniteFloat = Field(5., ge=0, le=50)
    max_speed_mps: FiniteFloat = Field(10., ge=.1, le=50)
    confirmed: bool
    reference_note: str = Field(min_length=1, max_length=500)
    constituent: Literal['ammonia', 'do'] = 'ammonia'

    @field_validator('confirmed')
    @classmethod
    def confirmed_by_user(cls, value):
        if value is not True:
            raise ValueError('须确认参考线方向及模拟时间原点')
        return value


def alignment_result(session, config=None):
    stored = config if config is not None else session.river_ui.get('alignment')
    if stored is None:
        return {'status':'not_configured', 'summary':{'status':'not_configured',
            'notice':'尚未显式核对参考线、上游方向和模拟时间原点，不能将走航数据与模型作已配准对照。'}, 'rows':[]}
    model = session.river_state
    if session.df is None or model is None or not model.has_wq():
        raise ValueError('需要当前走航数据及污染情景模拟结果，才能逐点核对')
    if not model.has_hydro() or model.hydro_result.params.get('tidal_amplitude_m', 0):
        raise ValueError('时空对照目前只支持恒定流情景')
    # 项目恢复后的配置也重新验证；不信任历史缓存的对照指标。
    request = AlignmentRequest.model_validate_json(json.dumps(stored))
    arguments = request.model_dump(mode='json')
    result = align_cruise(session.df, model.wq_result, **arguments)
    return {'status':'computed_under_user_assumptions', 'config':arguments, **result}
