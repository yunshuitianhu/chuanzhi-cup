"""系统路由：健康检查、配置读取与修改。对应 app.py 的侧边栏配置区。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key

from ..deps import get_session, get_exclusive_session
from ..session import Session

router = APIRouter(tags=["system"])

# 与 analysis_config.GB3838_STANDARDS 一致
VALID_CLASSES = ["I", "II", "III", "IV", "V"]


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "water-quality-api"}


@router.get("/session")
def whoami(session: Session = Depends(get_session)) -> dict:
    """返回会话 id —— 前端首次调用时获取，之后放进 X-Session-Id 头。"""
    from ..session import STORE
    return {"session_id": session.id, "active_sessions": STORE.count}


@router.get("/config")
def read_config(session: Session = Depends(get_session)) -> dict:
    """当前配置状态。

    密钥只返回"是否已配置"，**绝不返回密钥本身** —— 前端只需要知道
    能不能用，不需要拿到值。
    """
    return {
        "keys": {
            "deepseek": bool(resolve_api_key("DEEPSEEK_API_KEY")),
            "amap": bool(resolve_api_key("AMAP_KEY")),
            "starcloud": bool(resolve_api_key("CLOUD_TOKEN")),
        },
        "target_water_class": session.target_water_class,
        "valid_classes": VALID_CLASSES,
    }


@router.post("/config/target-class")
def set_target_class(payload: dict, session: Session = Depends(get_exclusive_session)) -> dict:
    """切换目标水质等级（I~V）。

    这会改变黑臭检测的判定基线，因此**清除既有分析结果** ——
    否则界面上会残留按旧基线算出来的黑点数与图表，与原 app.py
    切换等级后行为不一致（原版是下次分析才生效，容易让人误读）。
    """
    cls = str(payload.get("target_water_class", "")).upper()
    if cls not in VALID_CLASSES:
        return {"ok": False, "error": f"无效等级 {cls!r}，可选 {VALID_CLASSES}"}
    changed = cls != session.target_water_class
    session.target_water_class = cls
    if changed:
        session.reset_analysis()
    return {"ok": True, "target_water_class": cls, "analysis_reset": changed}
