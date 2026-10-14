"""排污许可证信息查询模块。

从国家排污许可证管理信息平台公开接口获取企业排污数据,
作为根因溯源的权威数据源之一。

数据来源:
  - 全国排污许可证管理信息平台 (http://permit.mee.gov.cn)
  - 各地市生态环境局年度重点排污单位名录

用法:
    from .pollution_permit import search_pollution_permits, search_key_pollution_sources

    permits = search_pollution_permits(23.11, 113.14, radius_m=5000)
    sources = search_key_pollution_sources("广东省", "佛山市")
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

# 项目根目录（相对本文件：src/water_quality_mcp/pollution_permit.py）
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)

# 排污许可证公开查询 API 端点
_PERMIT_API_BASE = "https://permit.mee.gov.cn/permitExt/outside/query"

# 请求超时 (秒)
_TIMEOUT = 10


def search_pollution_permits(
    lat: float,
    lon: float,
    radius_m: int = 2000,
    keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    """按坐标搜索周边排污许可证企业。

    Args:
        lat: 纬度 (GCJ-02)
        lon: 经度 (GCJ-02)
        radius_m: 搜索半径 (米)
        keywords: 行业关键词过滤 (如 ["化工", "印染", "电镀"])

    Returns:
        [{企业名称, 行业类别, 主要污染物, 许可证编号, 有效期}]
    """
    if keywords is None:
        keywords = ["化工", "印染", "电镀", "制药", "食品", "造纸", "养殖"]

    results: list[dict[str, Any]] = []

    for kw in keywords[:3]:  # 限 3 个关键词避免请求过多
        try:
            params = {
                "page": 1,
                "size": 10,
                "key": kw,
            }
            resp = requests.get(_PERMIT_API_BASE, params=params, timeout=_TIMEOUT)
            if resp.status_code != 200:
                continue

            # 尝试解析 JSON 响应
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError):
                # API 可能返回 HTML 页面
                logger.debug("排污许可证 API 返回非 JSON 响应 (状态码 %d)", resp.status_code)
                continue

            if isinstance(data, dict) and data.get("code") == 0:
                records = data.get("data", {}).get("list", [])
                for record in records:
                    results.append({
                        "数据源": "排污许可证",
                        "企业名称": record.get("companyName", ""),
                        "行业类别": record.get("industryName", ""),
                        "许可证编号": record.get("permitNo", ""),
                        "有效期": f"{record.get('validFrom', '')} ~ {record.get('validTo', '')}",
                        "主要污染物": record.get("pollutant", "详见许可证"),
                        "地址": record.get("address", ""),
                    })
        except requests.RequestException as e:
            logger.debug("排污许可证搜索失败 (kw=%s): %s", kw, e)
            continue

    return results


def search_key_pollution_sources(
    province: str | None = None,
    city: str | None = None,
) -> list[dict[str, Any]]:
    """按行政区划搜索重点排污单位名录。

    Args:
        province: 省份名称。**默认 None 表示"不知道行政区"**，此时直接返回
            空列表 —— 不再像原先那样默认成 "广东省"。名录是按省发布的，
            默认成某个省会把该省企业当成本地污染源返回给任意河流。
        city: 城市名称 (可选)

    Returns:
        [{企业名称, 排口类型, 主要污染物, 所属行政区}]
        省份未知或名录文件不存在时返回空列表（由调用方跳过该数据源）

    ⚠️ 关于此前的硬编码数据
    -----------------------
    本函数曾内置三条**占位条目**（"佛山市南海区某印染厂"等），并被打上
    `数据源: "重点排污单位名录"` 混入溯源结果、在输出里描述为
    "地方生态环境局年度公开名录 — 官方监管数据"。那是编造的数据被当作
    官方数据展示，已在本次修改中**全部删除**。

    现在只读真实名录文件；读不到就返回空，由 trace_engine 按既有的
    降级策略跳过该源 —— **宁可不报，不可编造**。

    名录文件来源：广东省生态环境厅「重点排污单位名录」专栏公开附件
    （2023 年起更名为「环境监管重点单位名录」，发布权下放至地市）。
    """
    path = _find_key_sources_file(province)
    if path is None:
        logger.info("未找到 %s 的重点排污单位名录文件，跳过该数据源", province)
        return []

    try:
        import pandas as pd
        # 省厅公开的 Excel 通常前两行是标题与表头
        raw = pd.read_excel(path, header=None)
        header_row = 1 if len(raw) > 1 else 0
        df = pd.read_excel(path, header=header_row)
    except Exception as e:                        # noqa: BLE001
        logger.warning("读取重点排污单位名录失败 (%s): %s", path, e)
        return []

    # 列名在不同年份的公开文件里并不统一，按关键词匹配
    def pick(row, *keywords):
        for col in df.columns:
            name = str(col)
            if any(k in name for k in keywords):
                v = row.get(col)
                if v is not None and str(v) != "nan":
                    return str(v).strip()
        return ""

    results: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        name = pick(row, "单位名称", "企业名称", "排污单位")
        if not name:
            continue
        area = pick(row, "行政区", "区域")
        if city and city not in area and city not in name:
            continue
        results.append({
            "数据源": "重点排污单位名录",
            "企业名称": name,
            "排口类型": pick(row, "排口类型", "类别") or "废水",
            "主要污染物": pick(row, "主要污染物", "污染物") or "详见名录",
            "所属行政区": area,
            "来源文件": os.path.basename(path),
        })

    logger.info("重点排污单位名录：从 %s 读取 %d 条", os.path.basename(path), len(results))
    return results


# 省级行政区 → 拼音。用于匹配文件名（各地生态环境厅的附件常以拼音命名，
# 如 guangdong_key_sources.xlsx）。
# 山西/陕西 按国标拼写区分（shanxi / shaanxi）。
_PROVINCE_PINYIN: dict[str, str] = {
    "北京": "beijing", "天津": "tianjin", "河北": "hebei", "山西": "shanxi",
    "内蒙古": "neimenggu", "辽宁": "liaoning", "吉林": "jilin",
    "黑龙江": "heilongjiang", "上海": "shanghai", "江苏": "jiangsu",
    "浙江": "zhejiang", "安徽": "anhui", "福建": "fujian", "江西": "jiangxi",
    "山东": "shandong", "河南": "henan", "湖北": "hubei", "湖南": "hunan",
    "广东": "guangdong", "广西": "guangxi", "海南": "hainan",
    "重庆": "chongqing", "四川": "sichuan", "贵州": "guizhou",
    "云南": "yunnan", "西藏": "xizang", "陕西": "shaanxi", "甘肃": "gansu",
    "青海": "qinghai", "宁夏": "ningxia", "新疆": "xinjiang",
    "台湾": "taiwan", "香港": "hongkong", "澳门": "macau",
}


def _province_key(province: str) -> tuple[str, str]:
    """从省份全称里取出中文简称与拼音。

    兼容 "广东省" / "广西壮族自治区" / "内蒙古自治区" / "北京市" 等写法：
    取 _PROVINCE_PINYIN 中**最长**的匹配键，避免短键误配。

    Returns:
        (中文简称, 拼音)，无法识别时返回 ("", "")
    """
    if not province:
        return "", ""
    key = ""
    for k in _PROVINCE_PINYIN:
        if k in province and len(k) > len(key):
            key = k
    if not key:
        return "", ""
    return key, _PROVINCE_PINYIN[key]


def _find_key_sources_file(province: str) -> str | None:
    """查找**指定省份**的重点排污单位名录文件。

    查找顺序：项目 data/ 目录 → 项目根目录。

    ⚠️ 本函数此前**收下 province 却完全不用它过滤** —— 只按文件名里的
    "key_source / 排污单位 / 重点单位" 关键字返回**第一个**匹配文件。
    后果：无论传入哪个省，都会返回目录里碰巧存在的那个省的名录，
    溯源结果会把外省企业当成本地污染源。现改为必须匹配省份。

    匹配规则：文件名含省份中文简称，或其拼音。匹配不上返回 None
    （由调用方跳过该数据源）—— **宁可不报，不可用错省份的名录**。

    文件不在仓库里（省厅公开附件），需使用者自行下载，文件名请带上省份，
    获取方式见 `data_acquisition_guide.md`。
    """
    cn, py = _province_key(province)
    if not cn:
        logger.info("无法识别省份 %r，跳过重点排污单位名录", province)
        return None

    roots = [
        os.path.join(_PROJECT_ROOT, "data"),
        _PROJECT_ROOT,
    ]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for fn in sorted(os.listdir(root)):
            if not fn.lower().endswith((".xlsx", ".xls")):
                continue
            if not ("key_source" in fn.lower() or "排污单位" in fn
                    or "重点单位" in fn):
                continue
            if cn in fn or (py and py in fn.lower()):
                return os.path.join(root, fn)
    logger.info(
        "未找到 %s（%s）的名录文件。文件名需含省份简称或拼音，"
        "例如 %s_key_sources.xlsx —— 找不到时跳过该数据源，"
        "不使用其他省份的名录。",
        province, cn, py or "province",
    )
    return None


def get_pollution_source_summary(results: list[dict[str, Any]]) -> str:
    """将搜索结果格式化为 JSON 字符串供 LLM 消费。

    Args:
        results: search_pollution_permits 或 search_key_pollution_sources 返回的列表

    Returns:
        JSON 字符串
    """
    if not results:
        return json.dumps(
            {"状态": "未找到相关排污许可证企业", "建议": "尝试扩大搜索范围或检查网络连接"},
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "检索到": len(results),
            "数据来源": "全国排污许可证管理信息平台 / 地方重点排污单位名录",
            "权威性说明": "数据来源于国家环保部门公开信息平台, 可作为根因溯源的权威参考",
            "企业列表": results[:15],
        },
        ensure_ascii=False,
    )
