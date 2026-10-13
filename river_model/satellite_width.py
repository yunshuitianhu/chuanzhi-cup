"""卫星影像河道宽度估计 (实验性功能)。

沿河道中心线采样卫星影像瓦片, 在垂直于水流方向检测水陆边界,
估计河道宽度。

依赖 StarCloud (星图地球) 卫星瓦片服务 (需要 CLOUD_TOKEN)。
失败时优雅降级, 返回 None。

技术路线:
  1. 获取河道中心线 GPS 坐标 (来自 geo_mapping)
  2. 沿中心线均匀采样 N 个断面
  3. 在每个断面, 获取卫星瓦片, 沿垂直方向扫描像素
  4. 用 NDWI 或简单灰度阈值检测水-陆边界
  5. 从像素距离换算为实际宽度 (米)

注意:
  - 此模块标记为实验性, 精度取决于卫星影像分辨率和光照条件
  - 需要安装 opencv-python (已有) 和 numpy (已有)
  - StarCloud 瓦片级别 zoom=18 时分辨率约 0.6m/pixel
"""

import io
import math
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

# 尝试导入图像处理依赖
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# ======================================================================
# 公开 API
# ======================================================================


def estimate_width_from_satellite(
    gps_mapping: dict,
    cloud_token: str,
    sample_spacing_m: float = 100.0,
    zoom: int = 18,
    max_samples: int = 20,
    timeout_s: float = 5.0,
) -> Optional[dict]:
    """从卫星影像估计河道宽度。

    Args:
        gps_mapping: geo_mapping.build_chainage_gps_mapping() 返回的字典
        cloud_token: 星图地球 Cloud Token
        sample_spacing_m: 采样间距 (m)
        zoom: 卫星瓦片缩放级别 (18 = ~0.6m/pixel)
        max_samples: 最大采样断面数
        timeout_s: 每个瓦片的超时时间 (s)

    Returns:
        {
            "width_avg_m": float,       # 平均宽度 (m)
            "width_std_m": float,       # 宽度标准差 (m)
            "width_median_m": float,    # 宽度中位数 (m)
            "samples": [                 # 各断面采样结果
                {"chainage_m": float, "width_m": float, "lat": float, "lon": float}
            ],
            "n_valid": int,             # 有效样本数
            "confidence": float,         # 置信度 0-1
        }
        或 None (失败/无卫星数据)
    """
    if not HAS_CV2 or not HAS_REQUESTS:
        return None
    if not cloud_token:
        return None

    total_length = gps_mapping.get("total_length_m", 0)
    if total_length < 100:
        return None

    # 沿河道中心线采样断面位置
    n_samples = min(int(total_length / sample_spacing_m) + 1, max_samples)
    sample_chainages = np.linspace(0, total_length, n_samples)

    # 获取每个采样点的坐标
    chainage_to_lat = gps_mapping.get("_chainage_to_lat")
    chainage_to_lon = gps_mapping.get("_chainage_to_lon")
    if chainage_to_lat is None or chainage_to_lon is None:
        return None

    # 计算每个采样点的流向 (使用前后点差分)
    samples = []
    for i, ch in enumerate(sample_chainages):
        try:
            lat = float(chainage_to_lat(ch))
            lon = float(chainage_to_lon(ch))
        except (ValueError, OverflowError):
            continue

        # 计算流向方向 (切线)
        delta = total_length * 0.02  # 2% 的河道长度作为差分步长
        try:
            lat_ahead = float(chainage_to_lat(min(ch + delta, total_length)))
            lon_ahead = float(chainage_to_lon(min(ch + delta, total_length)))
            lat_behind = float(chainage_to_lat(max(ch - delta, 0)))
            lon_behind = float(chainage_to_lon(max(ch - delta, 0)))
        except (ValueError, OverflowError):
            continue

        dy = lat_ahead - lat_behind
        dx = lon_ahead - lon_behind
        mag = math.sqrt(dy * dy + dx * dx)
        if mag < 1e-10:
            continue

        # 流向单位向量
        flow_lat = dy / mag
        flow_lon = dx / mag
        # 垂直方向 (逆时针旋转 90°)
        perp_lat = -flow_lon
        perp_lon = flow_lat

        # 获取该点的卫星瓦片并估计宽度
        width = _estimate_width_at_point(lat, lon, perp_lat, perp_lon,
                                          cloud_token, zoom, timeout_s)
        if width is not None and width > 0:
            samples.append({
                "chainage_m": round(float(ch), 0),
                "width_m": round(width, 1),
                "lat": round(lat, 5),
                "lon": round(lon, 5),
            })

        # 限速: 避免请求过快
        time.sleep(0.1)

    if len(samples) < 3:
        return None

    widths = [s["width_m"] for s in samples]

    # 去除异常值 (IQR 方法)
    q1 = np.percentile(widths, 25)
    q3 = np.percentile(widths, 75)
    iqr = q3 - q1
    valid_widths = [w for w in widths if q1 - 1.5 * iqr <= w <= q3 + 1.5 * iqr]

    if len(valid_widths) < 2:
        valid_widths = widths

    # 置信度: 有效样本越多, 方差越小 → 置信度越高
    cv = np.std(valid_widths) / np.mean(valid_widths) if np.mean(valid_widths) > 0 else 1.0
    confidence = min(1.0, max(0.1, (len(valid_widths) / n_samples) * (1.0 - min(cv, 0.5))))

    return {
        "width_avg_m": round(float(np.mean(valid_widths)), 1),
        "width_std_m": round(float(np.std(valid_widths)), 1),
        "width_median_m": round(float(np.median(valid_widths)), 1),
        "samples": samples,
        "n_valid": len(valid_widths),
        "confidence": round(confidence, 2),
    }


# ======================================================================
# 内部实现
# ======================================================================


def _get_satellite_tile(lat: float, lon: float, zoom: int,
                         cloud_token: str, timeout_s: float) -> Optional[np.ndarray]:
    """获取单个卫星瓦片图像。

    StarCloud (星图地球) 瓦片 URL 格式:
    https://tiles.geovisearth.com/base/v1/img/{z}/{x}/{y}?token={token}
    """
    # 经纬度 → 瓦片坐标
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)

    url = f"https://tiles.geovisearth.com/base/v1/img/{zoom}/{x}/{y}?token={cloud_token}"

    try:
        resp = requests.get(url, timeout=timeout_s, headers={
            "User-Agent": "WaterQualityMonitor/1.0"
        })
        if resp.status_code != 200:
            return None
        img_array = np.frombuffer(resp.content, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        return img
    except Exception:
        return None


def _estimate_width_at_point(
    lat: float, lon: float,
    perp_lat: float, perp_lon: float,
    cloud_token: str, zoom: int, timeout_s: float,
    search_radius_px: int = 80,
) -> Optional[float]:
    """在单个断面点估计河道宽度。

    沿垂直于水流方向扫描像素, 检测水-陆边界。

    Args:
        lat, lon: 采样点坐标
        perp_lat, perp_lon: 垂直于流向的单位向量
        cloud_token: 星图地球 token
        zoom: 瓦片级别
        timeout_s: 超时
        search_radius_px: 搜索半径 (像素)

    Returns:
        估计宽度 (m), 或 None
    """
    img = _get_satellite_tile(lat, lon, zoom, cloud_token, timeout_s)
    if img is None:
        return None

    h, w = img.shape[:2]
    cx, cy = w // 2, h // 2

    # 沿垂直方向采样像素
    line_pixels = []
    for i in range(-search_radius_px, search_radius_px + 1):
        px = int(cx + perp_lon * i)
        py = int(cy + perp_lat * i)
        if 0 <= px < w and 0 <= py < h:
            pixel = img[py, px].astype(float)
            line_pixels.append(pixel)
        else:
            line_pixels.append(np.array([0, 0, 0], dtype=float))

    if not line_pixels:
        return None

    line_pixels = np.array(line_pixels)

    # 灰度化
    gray = 0.299 * line_pixels[:, 2] + 0.587 * line_pixels[:, 1] + 0.114 * line_pixels[:, 0]

    # 简化的水体检测: 水体通常比陆地暗 (低灰度值)
    # 使用 Otsu 阈值或固定阈值
    if len(gray) < 20:
        return None

    dark_threshold = np.percentile(gray, 30)  # 使用 30% 分位数作为暗阈值
    water_mask = gray < dark_threshold

    # 寻找从中心向两侧的水-陆边界
    center_idx = len(gray) // 2

    # 向左搜索陆地边界
    left_boundary = 0
    for i in range(center_idx, 0, -1):
        if not water_mask[i]:
            # 找到陆地 (亮像素)
            # 往前找连续3个陆地像素确认
            land_count = 0
            for j in range(i, max(i - 5, 0), -1):
                if not water_mask[j]:
                    land_count += 1
            if land_count >= 3:
                left_boundary = i
                break

    # 向右搜索陆地边界
    right_boundary = len(gray) - 1
    for i in range(center_idx, len(gray) - 1):
        if not water_mask[i]:
            land_count = 0
            for j in range(i, min(i + 5, len(gray))):
                if not water_mask[j]:
                    land_count += 1
            if land_count >= 3:
                right_boundary = i
                break

    water_pixels = right_boundary - left_boundary
    if water_pixels < 3 or water_pixels > search_radius_px * 1.8:
        return None

    # 像素 → 米换算
    # zoom=18: 分辨率约 0.597 m/pixel (赤道), 需要纬度校正
    lat_rad = math.radians(lat)
    meters_per_pixel = 156543.03392 * math.cos(lat_rad) / (2 ** zoom)

    width_m = water_pixels * meters_per_pixel
    return max(1.0, width_m)
