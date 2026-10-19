import base64
import io
import os
import re
import tempfile

import cv2
import numpy as np
import pandas as pd
from .data_quality import measurement_error, coordinate_mask
from .evidence import set_file_identity

# 抑制 FFmpeg H.264 解码警告，避免污染 stderr
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"


# ======================================================================
# 列名识别
# ======================================================================
# 不同单位采集的数据列名不可能统一。原先只认一份数据里的那套中文列名，
# 换一份数据就 KeyError 崩栈 —— 这是"只服务一条河"的直接原因。
#
# 匹配方式：先做归一化，再查别名表。归一化会去掉单位括号、空白与大小写
# 差异，因此 "氨氮(mg/L)" / "氨氮" / "NH3-N" 都能归到同一列。
#
# ⚠️ 不要用 pd.to_datetime 的自动推断兜底。曾因此静默丢掉 92% 的 GPS 点：
#    裸 to_datetime 从**首个值**推断格式，首值无秒会导致所有带秒的值变 NaT。
#    下面一律使用显式 format 列表逐级尝试。

_UNIT_PAREN = re.compile(r"[（(][^）)]*[）)]")


def _norm_col(name) -> str:
    """列名归一化：去单位括号、去空白、转小写。"""
    s = str(name).replace("　", " ").replace(" ", " ")
    s = _UNIT_PAREN.sub("", s)
    s = re.sub(r"\s+", "", s)
    return s.strip().lower()


# 完整时间戳列
_TS_FULL_ALIASES = {
    "时间", "监测时间", "采样时间", "采集时间", "检测时间", "日期时间",
    "datetime", "timestamp", "datetime(utc)", "记录时间",
}
# 日期与时刻分列的情况
_TS_DATE_ALIASES = {"采样日期", "监测日期", "检测日期", "日期", "date"}
_TS_TIME_ALIASES = {"采样时刻", "监测时刻", "检测时刻", "时刻", "time"}

# 其余参数的别名 → 规范列名
_PARAM_ALIASES = {
    "longitude": {"经度", "longitude", "lon", "lng", "gps经度"},
    "latitude": {"纬度", "latitude", "lat", "gps纬度"},
    "ammonia_nitrogen": {"氨氮", "氨氮浓度", "nh3-n", "nh3n", "nh4+-n", "nh4n",
                         "ammonia_nitrogen", "ammonia", "氨氮含量"},
    "dissolved_oxygen": {"溶解氧", "溶氧", "do", "dissolved_oxygen", "dissolvedoxygen"},
    "water_temperature": {"水温", "温度", "water_temperature", "temperature", "temp"},
    "cod": {"化学需氧量", "cod", "codcr", "cod_cr"},
    "ph": {"ph", "酸碱度", "ph值"},
    "turbidity": {"浑浊度", "浊度", "turbidity", "ntu"},
    "conductivity": {"电导率", "conductivity", "ec"},
    "toc": {"总有机碳", "toc"},
}

# 时间列解析用的显式格式，按顺序尝试（不使用自动推断）
_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y.%m.%d",
)


def _resolve_columns(df: "pd.DataFrame"):
    """把实际列名映射到规范列名。

    Returns:
        (column_map, time_columns)
        column_map: {实际列名: 规范列名}
        time_columns: ("full", 列名) 或 ("split", 日期列, 时刻列) 或 None
    """
    norm_to_actual = {}
    for c in df.columns:
        norm_to_actual.setdefault(_norm_col(c), c)

    col_map = {}
    # 时间列单独处理，避免与参数别名冲突
    time_cols = None

    for norm, actual in norm_to_actual.items():
        if norm in _TS_FULL_ALIASES:
            time_cols = ("full", actual)
            break

    if time_cols is None:
        date_col = next((norm_to_actual[n] for n in _TS_DATE_ALIASES
                         if n in norm_to_actual), None)
        time_col = next((norm_to_actual[n] for n in _TS_TIME_ALIASES
                         if n in norm_to_actual), None)
        if date_col is not None and time_col is not None:
            time_cols = ("split", date_col, time_col)
        elif date_col is not None:
            time_cols = ("full", date_col)

    for norm, actual in norm_to_actual.items():
        if time_cols and actual in time_cols:
            continue
        for canon, aliases in _PARAM_ALIASES.items():
            if norm in aliases:
                col_map[actual] = canon
                break

    return col_map, time_cols


def _parse_time_column(raw: "pd.Series") -> "pd.Series":
    """按显式格式列表逐级解析时间列，返回 datetime Series。"""
    ts = (raw.astype(str).str.strip()
          .str.replace("　", " ").str.replace("：", ":"))
    dt = pd.Series(pd.NaT, index=ts.index, dtype="datetime64[ns]")
    for fmt in _TS_FORMATS:
        mask = dt.isna()
        if not mask.any():
            break
        dt[mask] = pd.to_datetime(ts[mask], format=fmt, errors="coerce")
    return dt


def normalize_columns(df: "pd.DataFrame"):
    """按别名表重命名列，并解析出 datetime 列（原地修改 df）。

    **列名识别与时间解析的统一入口。** app.py 与 backend 都走这里。

    为什么单独抽出来：这两处的列映射此前各写了一份，修复时只改了一边就会
    留下死角（实际发生过 —— 新前端的列名兼容修好了，app.py 的同名函数还是旧的）。

    Returns:
        (df, error)；成功时 error 为 None，失败时 error 为可读说明。
    """
    if len(df) == 0:
        return df, "文件为空：没有读到任何数据行（请检查工作表是否为空或表头行是否正确）"

    col_map, time_cols = _resolve_columns(df)
    if time_cols is None:
        return df, (
            "未找到时间列，无法解析。可接受的列名：" + "、".join(sorted(_TS_FULL_ALIASES))
            + "；或同时给出日期列与时刻列（如「采样日期」+「采样时刻」）。"
            + f"当前文件的列为：{list(df.columns)}"
        )

    # 时间列照旧重命名为 timestamp —— 保持与既有下游代码的兼容。
    # 内部真正被消费的是下面的 datetime 列，但 timestamp 这一列必须保留：
    # 原来的实现会产出它，去掉就是行为变化。
    col_map[time_cols[1]] = "timestamp"
    df.rename(columns=col_map, inplace=True)

    if time_cols[0] == "full":
        dt = _parse_time_column(df["timestamp"])
    else:
        # 日期与时刻分列：日期列已改名为 timestamp，时刻列仍是原名
        date_part = df["timestamp"].astype(str).str.strip()
        time_part = df[time_cols[2]].astype(str).str.strip()
        dt = _parse_time_column(date_part + " " + time_part)

    df["datetime"] = dt
    n_raw = len(df)
    df.dropna(subset=["datetime"], inplace=True)
    if len(df) == 0:
        return df, (
            f"{n_raw} 行数据的时间列全部无法解析。支持的格式示例："
            "2025-08-01 10:33:00 / 2025-08-01 10:33 / 2025-08-01 / 2025/08/01 10:33"
        )
    df.sort_values("datetime", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df, None


def load_data(file_bytes: str, filename: str):
    """加载并清洗 CSV/Excel 走航数据。返回 (df, report_string)。"""
    raw = base64.b64decode(file_bytes)
    buf = io.BytesIO(raw)
    fn = filename.lower()
    declared_kind = 'unknown'
    try:
        if fn.endswith('.csv'):
            try:
                df = pd.read_csv(buf, encoding='utf-8')
            except UnicodeDecodeError:
                buf.seek(0)
                df = pd.read_csv(buf, encoding='gbk')
        elif fn.endswith(('.xls', '.xlsx')):
            with pd.ExcelFile(buf) as book:
                df = pd.read_excel(book, sheet_name=0)
                # 只消费已知说明表中的「数据性质」枚举，不把自由文本交给报告。
                for sheet in ('数据说明', '仿真说明', '说明'):
                    if sheet not in book.sheet_names:
                        continue
                    notes = pd.read_excel(book, sheet_name=sheet, header=None, nrows=32,
                                          usecols=lambda c: c in (0, 1))
                    if notes.shape[1] < 2:
                        continue
                    for _, row in notes.iterrows():
                        if str(row.iloc[0]).strip() != '数据性质':
                            continue
                        value = str(row.iloc[1]).strip().lower()
                        if re.search(r'仿真|合成|synthetic|simulation', value):
                            declared_kind = 'synthetic'
                        elif declared_kind != 'synthetic' and value in ('现场实测', '现场观测', '实测数据', '观测数据'):
                            declared_kind = 'declared_observation'
        else:
            return None, "不支持的文件格式，请上传 .csv / .xls / .xlsx"
    except Exception as e:
        return None, f"读取失败: {e}"

    df, err = normalize_columns(df)
    if err:
        return None, err
    set_file_identity(df, filename, declared_kind)

    numeric_cols = ['ammonia_nitrogen', 'dissolved_oxygen', 'water_temperature', 'cod', 'turbidity', 'ph']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    for col in ['ph', 'water_temperature']:
        if col in df.columns:
            df[col] = df[col].replace(0, np.nan)

    limits = {'ammonia_nitrogen': (0, 1000), 'dissolved_oxygen': (0, 20), 'water_temperature': (0, 45),
              'cod': (0, 500), 'turbidity': (0, 2000), 'ph': (3, 12)}
    for col, (lo, hi) in limits.items():
        if col in df.columns:
            df[col] = df[col].where((df[col] >= lo) & (df[col] <= hi), np.nan)

    # 插值可用于趋势展示，但不能制造实测证据。DO=0 是合法的严重低氧观测。
    for col in numeric_cols:
        if col in df:
            df[f'__observed_{col}'] = df[col].notna() & np.isfinite(df[col])
    error = measurement_error(df)
    if error:
        return None, error

    for col in numeric_cols:
        if col in df.columns and df[col].isna().sum() > 0:
            df[col] = df[col].interpolate(method='linear', limit_direction='both')
    df['__observed_coordinates'] = coordinate_mask(df)
    for col in ['longitude', 'latitude']:
        if col in df.columns:
            df[col] = df[col].ffill().bfill()

    report = [f"数据加载完成，共 {len(df)} 条记录", df.attrs['data_identity']['label']]
    for col in ['ammonia_nitrogen', 'dissolved_oxygen', 'turbidity', 'ph']:
        if col in df.columns:
            valid = df[col].dropna()
            if len(valid) > 0:
                report.append(f"   {col}: 有效值={len(valid)}, 均值={valid.mean():.2f}, 最小={valid.min():.2f}, 最大={valid.max():.2f}")
    return df, "\n".join(report)


def extract_video(file_bytes: str, interval: int = 5):
    """从视频文件中按间隔抽帧。返回 (frames_list, report_string)。

    优化：顺序读取帧（避免 CAP_PROP_POS_FRAMES seek 开销），
    非目标帧用 grab() 跳过（省去 retrieve 的颜色转换+内存分配），
    像素采样检测暗帧（10×10 网格采1像素），JPEG 质量 75。
    """
    raw = base64.b64decode(file_bytes)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
    tmp.write(raw)
    vpath = tmp.name
    tmp.close()

    cap = cv2.VideoCapture(vpath, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        os.unlink(vpath)
        return [], "无法打开视频文件，请检查格式"

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    interval_frame = max(1, int(fps * interval))

    frames, frame_idx, skipped = [], 0, 0
    while True:
        if frame_idx % interval_frame == 0:
            ret, frame = cap.read()
            if not ret:
                break
            if frame is not None and frame.size > 0:
                # 像素采样检测暗帧：每 10 像素取 1 个，减少 99% 计算量
                if frame[::10, ::10].mean() > 1.0:
                    _, buf = cv2.imencode('.jpg', frame,
                                          [cv2.IMWRITE_JPEG_QUALITY, 75])
                    frames.append({
                        'time_sec': round(frame_idx / fps, 1),
                        'image_base64': base64.b64encode(buf).decode()
                    })
                else:
                    skipped += 1
        else:
            # 非目标帧只需 grab() 推进解码器，跳过 retrieve 节省内存复制
            if not cap.grab():
                break
        frame_idx += 1

    cap.release()
    os.unlink(vpath)
    return frames, (
        f"视频抽帧完成，共 {len(frames)} 帧"
        f"（总 {total_frames} 帧，间隔 {interval}s，跳过 {skipped} 个暗帧/损坏帧）"
    )
