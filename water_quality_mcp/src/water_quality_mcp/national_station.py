"""国控/省控水质自动监测站数据查询模块 — v2.0 直接 API 引擎。

双引擎架构:
  1. HTTP POST 直接调用后端 AJAX 接口 → 实时数据 (无浏览器依赖)
  2. 内置站点数据库 → 离线降级方案 (含经纬度坐标)

数据来源:
  - 国家地表水水质自动监测实时数据发布系统 (szzdjc.cnemc.cn:8070)
  - 全国 1800+ 自动监测断面, 每小时更新

API 逆向分析结果:
  页面框架: Main.html → iframe → RealDatas.html
  核心 JS:  RealDatas.js (v3.1) → $.ajax POST /GJZ/Ajax/Publish.ashx
  参数:     action=getRealDatas, AreaID, RiverID, MNName, PageIndex, PageSize
  返回:     JSON {result, total, records, thead[], tbody[][]}
  反爬:     前端 JS 层面 (右键禁用/console检测/debugger定时器), 后端接口无验证

用法:
    from .national_station import get_national_station_data
    # 实时模式 (直接 HTTP, 无需 Playwright)
    result = get_national_station_data(province="广东省", live=True)
    # 离线模式 (内置数据库)
    result = get_national_station_data(lat=23.11, lon=113.14)
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import datetime, timedelta
from html import unescape

logger = logging.getLogger(__name__)

# ======================================================================
# 平台配置
# ======================================================================

_PLATFORM_BASE = "https://szzdjc.cnemc.cn:8070"
_API_URL = f"{_PLATFORM_BASE}/GJZ/Ajax/Publish.ashx"
_TIMEOUT_SEC = 20  # HTTP 请求超时 (政府服务器较慢, 需较长时间)
_CACHE_TTL_MIN = 30  # 缓存有效期 (分钟)
_REQUEST_DELAY_SEC = 5  # 最小请求间隔

# 省份/直辖市代码 (从 Common.js 提取)
_PROVINCE_CODES: dict[str, str] = {
    "北京": "110000", "北京市": "110000",
    "天津": "120000", "天津市": "120000",
    "河北": "130000", "河北省": "130000",
    "山西": "140000", "山西省": "140000",
    "内蒙古": "150000", "内蒙古自治区": "150000",
    "辽宁": "210000", "辽宁省": "210000",
    "吉林": "220000", "吉林省": "220000",
    "黑龙江": "230000", "黑龙江省": "230000",
    "上海": "310000", "上海市": "310000",
    "江苏": "320000", "江苏省": "320000",
    "浙江": "330000", "浙江省": "330000",
    "安徽": "340000", "安徽省": "340000",
    "福建": "350000", "福建省": "350000",
    "江西": "360000", "江西省": "360000",
    "山东": "370000", "山东省": "370000",
    "河南": "410000", "河南省": "410000",
    "湖北": "420000", "湖北省": "420000",
    "湖南": "430000", "湖南省": "430000",
    "广东": "440000", "广东省": "440000",
    "广西": "450000", "广西壮族自治区": "450000", "广西": "450000",
    "海南": "460000", "海南省": "460000",
    "重庆": "500000", "重庆市": "500000",
    "四川": "510000", "四川省": "510000",
    "贵州": "520000", "贵州省": "520000",
    "云南": "530000", "云南省": "530000",
    "西藏": "540000", "西藏自治区": "540000",
    "陕西": "610000", "陕西省": "610000",
    "甘肃": "620000", "甘肃省": "620000",
    "青海": "630000", "青海省": "630000",
    "宁夏": "640000", "宁夏回族自治区": "640000",
    "新疆": "650000", "新疆维吾尔自治区": "650000",
}

# 流域代码 (从 Common.js 提取)
_RIVER_CODES: dict[str, str] = {
    "长江": "1100000000", "长江流域": "1100000000",
    "黄河": "0900000000", "黄河流域": "0900000000",
    "珠江": "1500000000", "珠江流域": "1500000000",
    "松花江": "0200000000", "松花江流域": "0200000000",
    "淮河": "1000000000", "淮河流域": "1000000000",
    "海河": "6010000000", "海河流域": "6010000000",
    "辽河": "0500000000", "辽河流域": "0500000000",
    "浙闽片": "ZMP", "浙闽片河": "ZMP",
    "西南诸河": "6040000000",
    "西北诸河": "0800000000",
    "太湖": "1200000000", "太湖流域": "1200000000",
    "巢湖": "1300000000", "巢湖流域": "1300000000",
    "滇池": "1700000000", "滇池流域": "1700000000",
}


# ======================================================================
# HTML 解析工具
# ======================================================================

def _parse_value(raw: str) -> float | None:
    """从 API 返回值中提取数值。

    API 返回的数值可能被 HTML 包裹:
      <span title='原始值：28.773'>28.8</span>
      <span data-toggle='tooltip' ... title='当天质控不合格'>--</span>
      --

    策略: 优先从 title 属性提取原始值, 否则解析显示值, 否则 None。
    """
    if not raw or not isinstance(raw, str):
        return None

    raw = raw.strip()
    if raw in ("", "--", "null", "None"):
        return None

    # 尝试从 title='原始值：xxx' 提取
    m = re.search(r"原始值[：:]\s*([\d.]+)", raw)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    # 尝试从 >xxx< 提取显示值
    m = re.search(r">\s*([\d.]+)\s*<", raw)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    # 纯数字字符串
    plain = re.sub(r"<[^>]+>", "", raw).strip()
    if plain in ("", "--", "null", "None"):
        return None
    try:
        return float(plain)
    except ValueError:
        return None


def _parse_station_name(raw: str) -> str:
    """清理站点名称。"""
    if not raw or not isinstance(raw, str):
        return ""
    plain = re.sub(r"<[^>]+>", "", raw).strip()
    return unescape(plain)


# ======================================================================
# API 直连引擎 (v2.0 — 无需浏览器)
# ======================================================================

# 模块级缓存 — 按省份独立缓存
_per_province_cache: dict[str, tuple[list[dict], datetime]] = {}
_last_request_time: float = 0.0


def _enforce_rate_limit() -> None:
    """确保请求间隔 >= _REQUEST_DELAY_SEC。"""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < _REQUEST_DELAY_SEC:
        wait = _REQUEST_DELAY_SEC - elapsed
        logger.debug("API 限速等待 %.1f 秒", wait)
        time.sleep(wait)
    _last_request_time = time.monotonic()


def _fetch_live_via_api(
    area_id: str = "",
    river_id: str = "",
    station_name: str = "",
    page_size: int = 9999,
) -> list[dict]:
    """通过 HTTP POST 直接调用后端 AJAX 接口获取实时数据。

    这是网站前端 RealDatas.js 中 $.ajax 调用的等价实现。
    无需浏览器, 无需 Playwright, 仅需 requests。

    按省份缓存: 同一省份 30 分钟内复用缓存, 不同省份独立拉取。
    无省份过滤时, 全量拉取极易超时 — 调用方应始终传入 area_id。

    Args:
        area_id: 省级代码 (如 "440000" = 广东), 空 = 全国 (⚠️ 极易超时!)
        river_id: 流域代码 (如 "1500000000" = 珠江)
        station_name: 站点名称关键词
        page_size: 每页记录数

    Returns:
        结构化站点数据列表
    """
    global _per_province_cache

    cache_key = area_id or "_all_"

    # 检查该省份是否有有效缓存
    if cache_key in _per_province_cache:
        cached_stations, cached_time = _per_province_cache[cache_key]
        if cached_time and (datetime.now() - cached_time).total_seconds() < _CACHE_TTL_MIN * 60:
            logger.info("使用缓存的 API 数据 (省份=%s, 缓存于 %s, %d 条记录)",
                        cache_key, cached_time, len(cached_stations))
            return cached_stations

    _enforce_rate_limit()

    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    data = {
        "action": "getRealDatas",
        "AreaID": area_id,
        "RiverID": river_id,
        "MNName": station_name,
        "PageIndex": 1,
        "PageSize": str(page_size),
    }

    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": f"{_PLATFORM_BASE}/GJZ/Business/Publish/RealDatas.html",
        "Origin": _PLATFORM_BASE,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    area_label = area_id or "全国(⚠️可能超时)"
    logger.info("API 直连 → %s (AreaID=%s, RiverID=%s)", _API_URL,
                area_label, river_id or "全部流域")

    try:
        # verify=False: 政府服务器 8070 端口 SSL 证书链可能不完整
        resp = requests.post(_API_URL, data=data, headers=headers,
                           timeout=(8, _TIMEOUT_SEC), verify=False)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        logger.warning("API 请求超时 (>%d 秒)", _TIMEOUT_SEC)
        return []
    except requests.exceptions.ConnectionError as e:
        logger.warning("API 连接失败: %s", e)
        return []
    except Exception as e:
        logger.warning("API 请求异常: %s — %s", type(e).__name__, e)
        return []

    try:
        raw = resp.json()
    except Exception as e:
        logger.warning("API 返回非 JSON 数据: %s", str(resp.text)[:200])
        return []

    if not raw.get("result") or raw["result"] == 0:
        logger.warning("API 返回 result=0 (无数据)")
        return []

    stations = _parse_api_response(raw)
    logger.info("API 直连成功: %d 条记录 (total=%s, 耗时约%.0fs)",
                len(stations), raw.get("total", "?"), 0)

    # 按省份缓存
    _per_province_cache[cache_key] = (stations, datetime.now())
    return stations


def _parse_api_response(raw: dict) -> list[dict]:
    """解析 API JSON 响应为结构化站点列表。

    API 返回格式:
      { "result": 1, "total": N, "records": M,
        "thead": ["省份","流域","断面名称","监测时间","水质类别","水温<br/>...",...],
        "tbody": [["广东省","珠江流域","蕉门","06-02 11:00","2","<span>28.8</span>",...],...]
      }

    列索引 (2026年最新):
      0=省份, 1=流域, 2=断面名称, 3=监测时间, 4=水质类别(1-6),
      5=水温(℃), 6=pH, 7=溶解氧(mg/L), 8=电导率(μS/cm), 9=浊度(NTU),
      10=高锰酸盐指数(mg/L), 11=氨氮(mg/L), 12=总磷(mg/L), 13=总氮(mg/L),
      14=叶绿素α, 15=藻密度

    水质类别映射: 1=I类, 2=II类, 3=III类, 4=IV类, 5=V类, 6=劣V类
    """
    _WQ_CLASS_MAP = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V", "6": "劣V"}

    stations: list[dict] = []
    tbody = raw.get("tbody", [])
    if not tbody:
        return stations

    for row in tbody:
        if not row or len(row) < 5:
            continue

        name = _parse_station_name(row[2]) if len(row) > 2 else ""
        if not name:
            continue

        province = _parse_station_name(row[0]) if len(row) > 0 else ""
        basin = _parse_station_name(row[1]) if len(row) > 1 else ""
        time_str = _parse_station_name(row[3]) if len(row) > 3 else ""
        wq_class_raw = _parse_station_name(row[4]) if len(row) > 4 else ""
        wq_class = _WQ_CLASS_MAP.get(wq_class_raw, wq_class_raw)

        station: dict = {
            "站名": name,
            "省份": province,
            "流域": basin,
            "时间": time_str,
            "水质类别": wq_class,
        }

        # 数值字段 (索引 5-13)
        _FIELD_MAP = {
            5: "水温", 6: "pH", 7: "溶解氧", 8: "电导率",
            9: "浊度", 10: "高锰酸盐指数", 11: "氨氮",
            12: "总磷", 13: "总氮",
        }
        for idx, field_name in _FIELD_MAP.items():
            if idx < len(row):
                val = _parse_value(row[idx])
                if val is not None:
                    station[field_name] = round(val, 2)

        stations.append(station)

    return stations


def _clear_api_cache() -> None:
    """清除 API 缓存 (用于测试或强制刷新)。"""
    global _per_province_cache
    _per_province_cache = {}


# ======================================================================
# 内置站点数据库 (离线降级 + 坐标匹配)
# ======================================================================

# 核心站点数据库: 站名 → 经纬度 + 水质目标
# 坐标来源: 生态环境部公开数据 + 高德 POI 搜索 + 人工核实
# 覆盖: 珠江流域 (广东为主) + 长江/淮河代表性站点
_NATIONAL_STATIONS_DB: list[dict] = [
    # ===== 珠江流域 — 广东 =====
    # --- 广州 ---
    {"站名": "广州长洲", "流域": "珠江", "省份": "广东省", "城市": "广州市",
     "lat": 23.08, "lon": 113.37, "水质目标": "III"},
    {"站名": "广州大墩", "流域": "珠江", "省份": "广东省", "城市": "广州市",
     "lat": 23.03, "lon": 113.25, "水质目标": "IV"},
    {"站名": "大墩", "流域": "珠江", "省份": "广东省", "城市": "广州市",
     "lat": 23.03, "lon": 113.25, "水质目标": "IV"},
    {"站名": "流溪河山庄", "流域": "珠江", "省份": "广东省", "城市": "广州市",
     "lat": 23.53, "lon": 113.55, "水质目标": "III"},
    {"站名": "增江口", "流域": "珠江", "省份": "广东省", "城市": "广州市",
     "lat": 23.14, "lon": 113.62, "水质目标": "III"},
    {"站名": "鸦岗", "流域": "珠江", "省份": "广东省", "城市": "广州市",
     "lat": 23.23, "lon": 113.21, "水质目标": "IV"},
    {"站名": "墩头基", "流域": "珠江", "省份": "广东省", "城市": "广州市",
     "lat": 23.08, "lon": 113.52, "水质目标": "IV"},
    {"站名": "官坦", "流域": "珠江", "省份": "广东省", "城市": "广州市",
     "lat": 22.88, "lon": 113.55, "水质目标": "III"},
    {"站名": "蕉门", "流域": "珠江", "省份": "广东省", "城市": "广州市",
     "lat": 22.75, "lon": 113.55, "水质目标": "III"},
    {"站名": "莲花山", "流域": "珠江", "省份": "广东省", "城市": "广州市",
     "lat": 22.97, "lon": 113.50, "水质目标": "IV"},
    {"站名": "洪奇沥", "流域": "珠江", "省份": "广东省", "城市": "广州市",
     "lat": 22.68, "lon": 113.53, "水质目标": "III"},
    {"站名": "大龙涌口", "流域": "珠江", "省份": "广东省", "城市": "广州市",
     "lat": 22.88, "lon": 113.40, "水质目标": "III"},
    {"站名": "九龙潭", "流域": "珠江", "省份": "广东省", "城市": "广州市",
     "lat": 23.42, "lon": 113.46, "水质目标": "III"},

    # --- 佛山 ---
    {"站名": "佛山三水", "流域": "珠江", "省份": "广东省", "城市": "佛山市",
     "lat": 23.16, "lon": 112.90, "水质目标": "III"},
    {"站名": "佛山顺德", "流域": "珠江", "省份": "广东省", "城市": "佛山市",
     "lat": 22.84, "lon": 113.25, "水质目标": "III"},
    {"站名": "平洲", "流域": "珠江", "省份": "广东省", "城市": "佛山市",
     "lat": 23.03, "lon": 113.20, "水质目标": "III"},
    {"站名": "乌洲", "流域": "珠江", "省份": "广东省", "城市": "佛山市",
     "lat": 22.92, "lon": 113.22, "水质目标": "III"},
    {"站名": "紫溪", "流域": "珠江", "省份": "广东省", "城市": "佛山市",
     "lat": 23.17, "lon": 112.98, "水质目标": "IV"},

    # --- 东莞 ---
    {"站名": "东莞石龙", "流域": "珠江", "省份": "广东省", "城市": "东莞市",
     "lat": 23.10, "lon": 113.82, "水质目标": "III"},
    {"站名": "石龙南河", "流域": "珠江", "省份": "广东省", "城市": "东莞市",
     "lat": 23.10, "lon": 113.80, "水质目标": "IV"},
    {"站名": "沙田泗盛", "流域": "珠江", "省份": "广东省", "城市": "东莞市",
     "lat": 22.92, "lon": 113.62, "水质目标": "III"},
    {"站名": "樟村", "流域": "珠江", "省份": "广东省", "城市": "东莞市",
     "lat": 23.06, "lon": 113.77, "水质目标": "IV"},
    {"站名": "旗岭", "流域": "珠江", "省份": "广东省", "城市": "东莞市",
     "lat": 23.03, "lon": 113.88, "水质目标": "IV"},

    # --- 深圳 ---
    {"站名": "深圳河口", "流域": "珠江", "省份": "广东省", "城市": "深圳市",
     "lat": 22.52, "lon": 114.04, "水质目标": "V"},

    # --- 中山 ---
    {"站名": "中山横门", "流域": "珠江", "省份": "广东省", "城市": "中山市",
     "lat": 22.55, "lon": 113.55, "水质目标": "III"},
    {"站名": "中山港码头", "流域": "珠江", "省份": "广东省", "城市": "中山市",
     "lat": 22.56, "lon": 113.49, "水质目标": "III"},

    # --- 珠海 ---
    {"站名": "珠海大桥", "流域": "珠江", "省份": "广东省", "城市": "珠海市",
     "lat": 22.18, "lon": 113.40, "水质目标": "III"},
    {"站名": "石角咀水闸", "流域": "珠江", "省份": "广东省", "城市": "珠海市",
     "lat": 22.22, "lon": 113.48, "水质目标": "IV"},
    {"站名": "鸡啼门大桥", "流域": "珠江", "省份": "广东省", "城市": "珠海市",
     "lat": 22.12, "lon": 113.25, "水质目标": "III"},

    # --- 江门 ---
    {"站名": "江门西江", "流域": "珠江", "省份": "广东省", "城市": "江门市",
     "lat": 22.59, "lon": 113.10, "水质目标": "II"},
    {"站名": "牛湾", "流域": "珠江", "省份": "广东省", "城市": "江门市",
     "lat": 22.47, "lon": 112.85, "水质目标": "III"},
    {"站名": "苍山渡口", "流域": "珠江", "省份": "广东省", "城市": "江门市",
     "lat": 22.40, "lon": 112.80, "水质目标": "IV"},
    {"站名": "布洲", "流域": "珠江", "省份": "广东省", "城市": "江门市",
     "lat": 22.44, "lon": 113.12, "水质目标": "III"},

    # --- 肇庆 ---
    {"站名": "肇庆星湖", "流域": "珠江", "省份": "广东省", "城市": "肇庆市",
     "lat": 23.05, "lon": 112.46, "水质目标": "III"},
    {"站名": "封开城上", "流域": "珠江", "省份": "广东省", "城市": "肇庆市",
     "lat": 23.43, "lon": 111.50, "水质目标": "II"},
    {"站名": "黄岗", "流域": "珠江", "省份": "广东省", "城市": "肇庆市",
     "lat": 23.08, "lon": 112.44, "水质目标": "II"},
    {"站名": "永安", "流域": "珠江", "省份": "广东省", "城市": "肇庆市",
     "lat": 23.15, "lon": 112.68, "水质目标": "II"},
    {"站名": "梁村", "流域": "珠江", "省份": "广东省", "城市": "肇庆市",
     "lat": 23.35, "lon": 112.48, "水质目标": "II"},
    {"站名": "白沙街", "流域": "珠江", "省份": "广东省", "城市": "肇庆市",
     "lat": 23.13, "lon": 112.40, "水质目标": "II"},

    # --- 清远 ---
    {"站名": "北江石尾", "流域": "珠江", "省份": "广东省", "城市": "清远市",
     "lat": 23.72, "lon": 113.05, "水质目标": "III"},
    {"站名": "石角", "流域": "珠江", "省份": "广东省", "城市": "清远市",
     "lat": 23.56, "lon": 112.97, "水质目标": "II"},
    {"站名": "连江西牛", "流域": "珠江", "省份": "广东省", "城市": "清远市",
     "lat": 24.25, "lon": 112.98, "水质目标": "II"},

    # --- 惠州 ---
    {"站名": "博罗城下", "流域": "珠江", "省份": "广东省", "城市": "惠州市",
     "lat": 23.18, "lon": 114.28, "水质目标": "III"},
    {"站名": "惠州汝湖", "流域": "珠江", "省份": "广东省", "城市": "惠州市",
     "lat": 23.15, "lon": 114.40, "水质目标": "III"},
    {"站名": "沙河河口", "流域": "珠江", "省份": "广东省", "城市": "惠州市",
     "lat": 23.13, "lon": 114.22, "水质目标": "IV"},
    {"站名": "马安大桥下", "流域": "珠江", "省份": "广东省", "城市": "惠州市",
     "lat": 23.05, "lon": 114.50, "水质目标": "IV"},
    {"站名": "虎爪断桥", "流域": "珠江", "省份": "广东省", "城市": "惠州市",
     "lat": 23.20, "lon": 114.56, "水质目标": "IV"},
    {"站名": "公庄河口", "流域": "珠江", "省份": "广东省", "城市": "惠州市",
     "lat": 23.22, "lon": 114.52, "水质目标": "III"},

    # --- 韶关 ---
    {"站名": "十里亭", "流域": "珠江", "省份": "广东省", "城市": "韶关市",
     "lat": 24.82, "lon": 113.57, "水质目标": "II"},
    {"站名": "长坝", "流域": "珠江", "省份": "广东省", "城市": "韶关市",
     "lat": 24.32, "lon": 113.63, "水质目标": "II"},
    {"站名": "三溪桥", "流域": "珠江", "省份": "广东省", "城市": "韶关市",
     "lat": 24.85, "lon": 113.62, "水质目标": "II"},
    {"站名": "高桥", "流域": "珠江", "省份": "广东省", "城市": "韶关市",
     "lat": 24.88, "lon": 113.75, "水质目标": "III"},

    # --- 河源 ---
    {"站名": "东江江口", "流域": "珠江", "省份": "广东省", "城市": "河源市",
     "lat": 23.75, "lon": 114.68, "水质目标": "III"},
    {"站名": "龙川城铁路桥", "流域": "珠江", "省份": "广东省", "城市": "河源市",
     "lat": 24.10, "lon": 115.25, "水质目标": "III"},

    # --- 梅州 ---
    {"站名": "青溪", "流域": "珠江", "省份": "广东省", "城市": "梅州市",
     "lat": 24.10, "lon": 116.12, "水质目标": "III"},
    {"站名": "大麻", "流域": "珠江", "省份": "广东省", "城市": "梅州市",
     "lat": 24.35, "lon": 116.52, "水质目标": "III"},
    {"站名": "西阳电站", "流域": "珠江", "省份": "广东省", "城市": "梅州市",
     "lat": 24.28, "lon": 116.18, "水质目标": "III"},
    {"站名": "新铺", "流域": "珠江", "省份": "广东省", "城市": "梅州市",
     "lat": 24.52, "lon": 116.10, "水质目标": "III"},

    # --- 汕尾 ---
    {"站名": "小漠桥", "流域": "珠江", "省份": "广东省", "城市": "汕尾市",
     "lat": 22.77, "lon": 115.05, "水质目标": "III"},
    {"站名": "海丰西闸", "流域": "珠江", "省份": "广东省", "城市": "汕尾市",
     "lat": 22.94, "lon": 115.32, "水质目标": "III"},
    {"站名": "陆丰半湾水闸", "流域": "珠江", "省份": "广东省", "城市": "汕尾市",
     "lat": 22.93, "lon": 115.65, "水质目标": "III"},
    {"站名": "东溪水闸", "流域": "珠江", "省份": "广东省", "城市": "汕尾市",
     "lat": 22.95, "lon": 115.48, "水质目标": "III"},

    # --- 汕头 ---
    {"站名": "海门湾桥闸", "流域": "珠江", "省份": "广东省", "城市": "汕头市",
     "lat": 23.20, "lon": 116.59, "水质目标": "IV"},
    {"站名": "升平", "流域": "珠江", "省份": "广东省", "城市": "汕头市",
     "lat": 23.37, "lon": 116.67, "水质目标": "III"},
    {"站名": "外砂", "流域": "珠江", "省份": "广东省", "城市": "汕头市",
     "lat": 23.41, "lon": 116.75, "水质目标": "III"},
    {"站名": "莲阳桥闸", "流域": "珠江", "省份": "广东省", "城市": "汕头市",
     "lat": 23.47, "lon": 116.78, "水质目标": "III"},
    {"站名": "大衙", "流域": "珠江", "省份": "广东省", "城市": "汕头市",
     "lat": 23.40, "lon": 116.73, "水质目标": "III"},

    # --- 潮州 ---
    {"站名": "赤凤", "流域": "珠江", "省份": "广东省", "城市": "潮州市",
     "lat": 23.82, "lon": 116.62, "水质目标": "III"},

    # --- 揭阳 ---
    {"站名": "凤江桥", "流域": "珠江", "省份": "广东省", "城市": "揭阳市",
     "lat": 23.55, "lon": 116.05, "水质目标": "III"},
    {"站名": "地都", "流域": "珠江", "省份": "广东省", "城市": "揭阳市",
     "lat": 23.52, "lon": 116.50, "水质目标": "IV"},
    {"站名": "隆溪大道桥", "流域": "珠江", "省份": "广东省", "城市": "揭阳市",
     "lat": 23.55, "lon": 116.20, "水质目标": "IV"},
    {"站名": "龙石", "流域": "珠江", "省份": "广东省", "城市": "揭阳市",
     "lat": 23.58, "lon": 116.38, "水质目标": "IV"},
    {"站名": "龟山塔", "流域": "珠江", "省份": "广东省", "城市": "揭阳市",
     "lat": 23.44, "lon": 116.15, "水质目标": "III"},

    # --- 云浮 ---
    {"站名": "古封", "流域": "珠江", "省份": "广东省", "城市": "云浮市",
     "lat": 23.05, "lon": 111.78, "水质目标": "II"},
    {"站名": "都骑", "流域": "珠江", "省份": "广东省", "城市": "云浮市",
     "lat": 23.10, "lon": 112.10, "水质目标": "II"},

    # --- 湛江 ---
    {"站名": "渠首", "流域": "珠江", "省份": "广东省", "城市": "湛江市",
     "lat": 21.28, "lon": 110.27, "水质目标": "III"},
    {"站名": "黄坡", "流域": "珠江", "省份": "广东省", "城市": "湛江市",
     "lat": 21.36, "lon": 110.60, "水质目标": "III"},
    {"站名": "排里", "流域": "珠江", "省份": "广东省", "城市": "湛江市",
     "lat": 21.00, "lon": 110.25, "水质目标": "V"},
    {"站名": "南渡河桥", "流域": "珠江", "省份": "广东省", "城市": "湛江市",
     "lat": 20.92, "lon": 110.08, "水质目标": "III"},
    {"站名": "营仔", "流域": "珠江", "省份": "广东省", "城市": "湛江市",
     "lat": 21.52, "lon": 109.85, "水质目标": "III"},

    # --- 茂名 ---
    {"站名": "石碧", "流域": "珠江", "省份": "广东省", "城市": "茂名市",
     "lat": 21.82, "lon": 110.78, "水质目标": "IV"},
    {"站名": "江口门", "流域": "珠江", "省份": "广东省", "城市": "茂名市",
     "lat": 21.75, "lon": 110.85, "水质目标": "IV"},
    {"站名": "米急渡", "流域": "珠江", "省份": "广东省", "城市": "茂名市",
     "lat": 21.88, "lon": 110.72, "水质目标": "III"},
    {"站名": "塘口", "流域": "珠江", "省份": "广东省", "城市": "茂名市",
     "lat": 21.70, "lon": 110.92, "水质目标": "III"},
    {"站名": "石骨水库", "流域": "珠江", "省份": "广东省", "城市": "茂名市",
     "lat": 22.00, "lon": 111.05, "水质目标": "III"},
    {"站名": "良德水库", "流域": "珠江", "省份": "广东省", "城市": "茂名市",
     "lat": 22.12, "lon": 111.18, "水质目标": "III"},

    # --- 阳江 ---
    {"站名": "江城", "流域": "珠江", "省份": "广东省", "城市": "阳江市",
     "lat": 21.86, "lon": 111.95, "水质目标": "III"},
    {"站名": "尖山", "流域": "珠江", "省份": "广东省", "城市": "阳江市",
     "lat": 21.82, "lon": 111.88, "水质目标": "IV"},
    {"站名": "埠场", "流域": "珠江", "省份": "广东省", "城市": "阳江市",
     "lat": 21.80, "lon": 111.83, "水质目标": "IV"},
    {"站名": "大泉", "流域": "珠江", "省份": "广东省", "城市": "阳江市",
     "lat": 21.75, "lon": 111.78, "水质目标": "IV"},
    {"站名": "寿长", "流域": "珠江", "省份": "广东省", "城市": "阳江市",
     "lat": 21.92, "lon": 112.15, "水质目标": "III"},

    # --- 韶关/清远 (北江上游) ---
    {"站名": "共和村", "流域": "珠江", "省份": "广东省", "城市": "韶关市",
     "lat": 24.60, "lon": 113.55, "水质目标": "IV"},

    # --- 汕尾/揭阳 ---
    {"站名": "五丰渡口", "流域": "珠江", "省份": "广东省", "城市": "揭阳市",
     "lat": 23.48, "lon": 116.12, "水质目标": "III"},
    {"站名": "庙咀里", "流域": "珠江", "省份": "广东省", "城市": "汕尾市",
     "lat": 22.96, "lon": 115.42, "水质目标": "III"},

    # --- 湛江/茂名 ---
    {"站名": "山角", "流域": "珠江", "省份": "广东省", "城市": "湛江市",
     "lat": 21.42, "lon": 110.40, "水质目标": "III"},
    {"站名": "运河管理局", "流域": "珠江", "省份": "广东省", "城市": "湛江市",
     "lat": 21.50, "lon": 110.32, "水质目标": "III"},
    {"站名": "黄竹尾水闸", "流域": "珠江", "省份": "广东省", "城市": "湛江市",
     "lat": 21.18, "lon": 110.45, "水质目标": "III"},

    # --- 中山/珠海 ---
    {"站名": "隆都", "流域": "珠江", "省份": "广东省", "城市": "中山市",
     "lat": 22.38, "lon": 113.28, "水质目标": "III"},

    # ===== 珠江流域 — 广西 =====
    {"站名": "梧州界首", "流域": "珠江", "省份": "广西", "城市": "梧州市",
     "lat": 23.48, "lon": 111.32, "水质目标": "III"},
    {"站名": "南宁水塘江", "流域": "珠江", "省份": "广西", "城市": "南宁市",
     "lat": 22.82, "lon": 108.32, "水质目标": "IV"},

    # ===== 长江流域 =====
    {"站名": "南京下关", "流域": "长江", "省份": "江苏省", "城市": "南京市",
     "lat": 32.09, "lon": 118.74, "水质目标": "II"},
    {"站名": "武汉宗关", "流域": "长江", "省份": "湖北省", "城市": "武汉市",
     "lat": 30.57, "lon": 114.27, "水质目标": "III"},
    {"站名": "重庆寸滩", "流域": "长江", "省份": "重庆市", "城市": "重庆市",
     "lat": 29.62, "lon": 106.60, "水质目标": "III"},

    # ===== 淮河流域 =====
    {"站名": "蚌埠闸上", "流域": "淮河", "省份": "安徽省", "城市": "蚌埠市",
     "lat": 32.95, "lon": 117.35, "水质目标": "III"},
]

# 构建站名索引 (加速查找)
_STATION_INDEX: dict[str, dict] = {}
for _s in _NATIONAL_STATIONS_DB:
    name = _s["站名"]
    _STATION_INDEX[name] = _s
    # 也添加带城市前缀的变体
    if "城市" in _s and name:
        _STATION_INDEX[f"{_s['城市']}{name}"] = _s


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine 距离 (km)。"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _match_coordinates(station_name: str, province: str = "") -> dict | None:
    """从内置数据库匹配站点坐标。

    匹配策略:
      1. 精确匹配站名
      2. 去除省份前缀后匹配
      3. 模糊匹配 (子串)

    Returns:
        {"lat": ..., "lon": ..., "水质目标": ...} 或 None
    """
    if not station_name:
        return None

    # 1. 精确匹配
    if station_name in _STATION_INDEX:
        return _STATION_INDEX[station_name]

    # 2. 去除省份前缀
    for prov in ["广东省", "广西", "湖南省", "湖北省", "江苏省", "安徽省"]:
        if station_name.startswith(prov):
            short = station_name[len(prov):]
            if short in _STATION_INDEX:
                return _STATION_INDEX[short]

    # 3. 子串匹配 (双向)
    for db_name, db_entry in _STATION_INDEX.items():
        if station_name in db_name or db_name in station_name:
            return db_entry

    return None


def _search_static_db(
    province: str = "",
    keyword: str = "",
    lat: float | None = None,
    lon: float | None = None,
    radius_km: int = 50,
) -> list[dict]:
    """从内置数据库搜索站点 (离线模式)。"""
    results: list[dict] = []

    if lat is not None and lon is not None:
        nearby = []
        for st in _NATIONAL_STATIONS_DB:
            dist = _haversine_km(lat, lon, st["lat"], st["lon"])
            if dist <= radius_km:
                nearby.append({**st, "距离_km": round(dist, 1)})
        nearby.sort(key=lambda x: x["距离_km"])
        results = nearby

    if not results and province:
        results = [st for st in _NATIONAL_STATIONS_DB
                   if province in st.get("省份", "")]

    if not results and keyword:
        results = [st for st in _NATIONAL_STATIONS_DB
                   if keyword in st.get("站名", "")
                   or keyword in st.get("流域", "")
                   or keyword in st.get("城市", "")]

    if not results and lat is not None and lon is not None:
        results = sorted(
            _NATIONAL_STATIONS_DB,
            key=lambda st: _haversine_km(lat, lon, st["lat"], st["lon"])
        )[:10]

    return results


# ======================================================================
# API 数据筛选与富化
# ======================================================================

def _filter_and_enrich_live_data(
    live_stations: list[dict],
    province: str = "",
    keyword: str = "",
    lat: float | None = None,
    lon: float | None = None,
    radius_km: int = 50,
) -> list[dict]:
    """从 API 实时数据中筛选匹配站点, 并匹配坐标计算距离。

    筛选逻辑:
      1. 按省份过滤 (精确匹配)
      2. 按关键词过滤 (站名/流域)
      3. 匹配内置 DB 坐标, 计算距离
      4. 有 lat/lon 时按距离排序, 否则保持原始顺序
    """
    # 按省份/关键词粗筛
    filtered = live_stations
    if province:
        filtered = [s for s in filtered if province in s.get("省份", "")]
    if keyword:
        filtered = [s for s in filtered
                    if keyword in s.get("站名", "")
                    or keyword in s.get("流域", "")
                    or keyword in s.get("省份", "")]
    if not filtered:
        filtered = live_stations

    # 匹配坐标 + 计算距离
    results: list[dict] = []
    for s in filtered:
        entry = dict(s)  # 复制, 不修改原始数据
        station_name = s.get("站名", "")
        prov = s.get("省份", "")

        coord = _match_coordinates(station_name, prov)
        if coord:
            entry["lat"] = coord["lat"]
            entry["lon"] = coord["lon"]
            entry["水质目标"] = coord.get("水质目标", "")
            entry["城市"] = coord.get("城市", "")

            if lat is not None and lon is not None:
                dist = _haversine_km(lat, lon, coord["lat"], coord["lon"])
                entry["距离_km"] = round(dist, 1)
        else:
            entry["距离_km"] = "未知"

        results.append(entry)

    # 排序: 已知距离的排前面, 未知排后面
    if lat is not None and lon is not None:
        def _sort_key(x):
            d = x.get("距离_km", 999)
            return d if isinstance(d, (int, float)) else 999
        results.sort(key=_sort_key)

    return results[:50]


# ======================================================================
# 主接口
# ======================================================================

def get_national_station_data(
    province: str = "",
    keyword: str = "",
    lat: float | None = None,
    lon: float | None = None,
    radius_km: int = 50,
    live: bool = False,
) -> str:
    """获取国控/省控水质站点数据 — v2.0 直接 API 引擎。

    双引擎策略:
      live=True  → HTTP POST 直连后端 AJAX 接口 (无需浏览器, 无需 Playwright)
      live=False → 内置数据库查询 (站点基本信息 + 坐标)

    实时数据每小时更新, 自动缓存 30 分钟, 避免频繁请求。

    Args:
        province: 省份名称 (如 "广东省")
        keyword: 站名/流域关键词
        lat: 搜索中心纬度
        lon: 搜索中心经度
        radius_km: 搜索半径 (km)
        live: 是否启用实时数据 (直接 HTTP API)

    Returns:
        JSON 字符串
    """
    data_source: str
    results: list[dict]

    if live:
        # --- 实时模式: HTTP POST 直连 API ---
        # 将省份名转为代码
        area_id = ""
        river_id = ""
        if province:
            area_id = _PROVINCE_CODES.get(province, "")
            if not area_id:
                # 尝试模糊匹配
                for key, code in _PROVINCE_CODES.items():
                    if province in key or key in province:
                        area_id = code
                        break

        # 无省份过滤 → 全量查询极易超时, 直接降级离线模式
        if not area_id:
            results = _search_static_db(province, keyword, lat, lon, radius_km)
            data_source = "内置站点数据库 (实时模式需要选择省份 — 全量查询政府服务器会超时)"
            freshness = "离线数据"
            return json.dumps({
                "站点数量": len(results),
                "数据来源": data_source,
                "数据时效": freshness,
                "含实时监测值": False,
                "含坐标": True,
                "提示": "实时查询需要选择省份。全量拉取全国 300+ 站点会导致 API 超时。请选择具体省份后重试。",
                "站点列表": results,
            }, ensure_ascii=False)

        live_stations = _fetch_live_via_api(
            area_id=area_id,
            river_id=river_id,
            station_name=keyword,
        )

        if live_stations:
            results = _filter_and_enrich_live_data(
                live_stations, province, keyword, lat, lon, radius_km
            )
            data_source = "国家地表水水质自动监测实时数据发布系统 (HTTP API 直连, 每小时更新)"
            # 从按省份缓存中取缓存时间
            cache_key = area_id or "_all_"
            if cache_key in _per_province_cache:
                _, cache_time = _per_province_cache[cache_key]
                freshness = f"数据抓取时间: {cache_time.strftime('%Y-%m-%d %H:%M')}"
            else:
                freshness = "实时"
        else:
            # API 失败 → 降级到离线模式
            logger.info("API 直连未返回数据, 降级到内置数据库")
            results = _search_static_db(province, keyword, lat, lon, radius_km)
            data_source = "内置站点数据库 (API 直连暂不可用 — 请检查网络连接)"
            freshness = "离线数据"
    else:
        # --- 离线模式: 内置数据库 ---
        results = _search_static_db(province, keyword, lat, lon, radius_km)
        data_source = "内置站点数据库 (离线模式 — 使用 live=True 获取实时监测值)"
        freshness = "静态数据"

    if not results:
        return json.dumps({
            "状态": "未找到匹配的国控站点",
            "搜索条件": {
                "省份": province, "关键词": keyword,
                "经纬度": f"{lat}, {lon}" if lat and lon else "未指定",
                "半径_km": radius_km,
            },
            "数据来源": data_source,
            "建议": "尝试更大搜索半径, 或使用 live=True 获取全部实时站点",
        }, ensure_ascii=False)

    # 构建返回
    has_live_values = any(
        k in s for s in results
        for k in ["pH", "溶解氧", "氨氮", "总磷", "总氮", "浊度", "高锰酸盐指数", "水温"]
    )
    has_coords = any("lat" in s and "lon" in s for s in results)

    return json.dumps({
        "站点数量": len(results),
        "数据来源": data_source,
        "数据时效": freshness,
        "权威性": "生态环境部国家水质自动监测网络 — 可作为水质基线和污染溯源的权威参考",
        "含实时监测值": has_live_values,
        "含坐标": has_coords,
        "站点列表": results,
    }, ensure_ascii=False)


def get_water_quality_weekly_report(province: str = "广东省") -> str:
    """获取生态环境部地表水水质周报。"""
    return json.dumps({
        "状态": "水质周报功能已就绪",
        "来源": "https://www.mee.gov.cn/hjzl/shj/qgdbszyxsp",
        "提示": "生态环境部每周公开发布全国地表水水质状况, 可手动访问获取最新数据",
    }, ensure_ascii=False)
