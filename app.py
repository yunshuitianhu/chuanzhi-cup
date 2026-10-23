import streamlit as st
import pandas as pd
import numpy as np
import os, json, base64, time, tempfile, asyncio, math, httpx
from datetime import timedelta
from openai import OpenAI
import requests
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 把项目根目录的 .env 载入环境变量（该文件已被 .gitignore 排除，绝不入库）。
# 必须在任何读取密钥的代码之前执行。
try:
    from water_quality_mcp.src.water_quality_mcp.config import load_env
    load_env()
except Exception as _e:  # 包未就绪时不阻断应用启动
    print(f"[warn] .env 未加载: {_e}")

st.set_page_config(page_title="水质监测溯源助手", layout="wide")

@st.cache_resource
def _get_font_prop():
    from water_quality_mcp.src.water_quality_mcp.font_utils import discover_font, configure_matplotlib_font
    font_prop = discover_font()
    configure_matplotlib_font(font_prop)
    return font_prop

# 星图地球数据云 Token — 优先从环境变量读取, 否则使用用户配置
def _get_starcloud_token():
    token = os.environ.get("CLOUD_TOKEN", "")
    if token and "your-" not in token:
        return token
    return st.session_state.get("_starcloud_token", "")

# 初始化 Session State
if "df" not in st.session_state: st.session_state.df = None
if "black_spots" not in st.session_state: st.session_state.black_spots = None
if "video_frames" not in st.session_state: st.session_state.video_frames = []
if "messages" not in st.session_state: st.session_state.messages = []
if "full_messages" not in st.session_state: st.session_state.full_messages = []
if "analysis_done" not in st.session_state: st.session_state.analysis_done = False
if "charts_generated" not in st.session_state: st.session_state.charts_generated = False
if "anim_html" not in st.session_state: st.session_state.anim_html = None
if "anim_file_size" not in st.session_state: st.session_state.anim_file_size = 0
if "cross_validation_result" not in st.session_state: st.session_state.cross_validation_result = None
if "chart_captions" not in st.session_state: st.session_state.chart_captions = {}
if "river_state" not in st.session_state:
    from river_model.state import RiverModelState
    st.session_state.river_state = RiverModelState()
if "analysis_config" not in st.session_state:
    from water_quality_mcp.src.water_quality_mcp.analysis_config import AnalysisConfig
    st.session_state.analysis_config = AnalysisConfig()
if "font_prop" not in st.session_state:
    st.session_state.font_prop = _get_font_prop()

# ---------- API 密钥管理 ----------

def _load_api_keys_from_files():
    """收集已配置的 API 密钥。

    优先级：环境变量（启动时由 load_env() 从项目根目录的 .env 载入）
            > .mcp.json（MCP 客户端配置）。

    两个文件都在 .gitignore 中，密钥不会进入仓库；
    源码与示例文件中一律只出现占位符。
    """
    def _clean(v):
        v = (v or "").strip()
        # 占位符一律视为未配置，否则会拿去请求、得到难定位的认证错误
        if not v or "your-" in v.lower() or "你的" in v:
            return ""
        return v

    keys = {
        "deepseek": _clean(os.environ.get("DEEPSEEK_API_KEY"))
                    or _clean(os.environ.get("DASHSCOPE_API_KEY")),
        "amap": _clean(os.environ.get("AMAP_KEY")),
        "starcloud": _clean(os.environ.get("CLOUD_TOKEN")),
    }

    # 环境变量缺失的项，回退到 .mcp.json
    if not all(keys.values()):
        mcp_path = os.path.join(os.path.dirname(__file__), ".mcp.json")
        if os.path.exists(mcp_path):
            try:
                with open(mcp_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                env_vars = cfg.get("mcpServers", {}).get("water-quality-mcp", {}).get("env", {})
                keys["amap"] = keys["amap"] or _clean(env_vars.get("AMAP_KEY"))
                keys["deepseek"] = keys["deepseek"] or _clean(env_vars.get("DEEPSEEK_API_KEY"))
                keys["starcloud"] = keys["starcloud"] or _clean(env_vars.get("CLOUD_TOKEN"))
            except Exception:
                pass
    return keys

def _save_api_keys_to_files(deepseek_key, amap_key, starcloud_token):
    """保存 API key 到 .env 和 .mcp.json 文件。"""
    import os as _os
    root = os.path.dirname(__file__)
    # 写 .env
    with open(_os.path.join(root, ".env"), "w", encoding="utf-8") as f:
        f.write(f"DEEPSEEK_API_KEY={deepseek_key}\n")
    # 写 .mcp.json
    mcp_config = {
        "mcpServers": {
            "water-quality-mcp": {
                "command": "python",
                "args": ["-m", "water_quality_mcp.server"],
                "env": {
                    "AMAP_KEY": amap_key or "",
                    "CLOUD_TOKEN": starcloud_token or "",
                    "DEEPSEEK_API_KEY": deepseek_key or "",
                }
            }
        }
    }
    with open(_os.path.join(root, ".mcp.json"), "w", encoding="utf-8") as f:
        json.dump(mcp_config, f, indent=2, ensure_ascii=False)

def _show_setup_wizard():
    """首次运行或 key 缺失时弹出 API 密钥配置界面。"""
    if st.session_state.get("_setup_done"):
        return True

    keys = _load_api_keys_from_files()
    has_keys = bool(keys["deepseek"])

    if has_keys and "_setup_done" not in st.session_state:
        st.session_state._setup_done = True
        st.session_state._deepseek_key = keys["deepseek"]
        st.session_state._amap_key = keys["amap"]
        st.session_state._starcloud_token = keys["starcloud"]
        return True

    # 显示配置向导
    st.markdown("""
    ## 🔐 首次运行 — 配置 API 密钥

    本工具依赖以下第三方服务，请填写您的 API 密钥后点击"保存并开始使用"。

    | 服务 | 用途 | 获取地址 |
    |------|------|---------|
    | **DeepSeek**（必填） | LLM 自动分析 & 溯源报告 | [platform.deepseek.com](https://platform.deepseek.com) |
    | 高德地图（可选） | 逆地理编码 & POI 搜索 | [console.amap.com](https://console.amap.com) |
    | 星图地球（可选） | 卫星影像底图 | [datacloud.geovisearth.com](https://datacloud.geovisearth.com) |
    """)

    with st.form("setup_form"):
        dk = st.text_input("DeepSeek API Key *", type="password",
                          value=st.session_state.get("_deepseek_key", ""),
                          placeholder="sk-...")
        ak = st.text_input("高德地图 API Key（可选）", type="password",
                          value=st.session_state.get("_amap_key", ""),
                          placeholder="留空则跳过地理相关功能")
        sc = st.text_input("星图地球 Token（可选）", type="password",
                          value=st.session_state.get("_starcloud_token", ""),
                          placeholder="留空则使用默认瓦片")
        submitted = st.form_submit_button("💾 保存并开始使用", type="primary", use_container_width=True)

        if submitted:
            if not dk.strip():
                st.error("DeepSeek API Key 为必填项")
                return False
            _save_api_keys_to_files(dk.strip(), ak.strip(), sc.strip())
            st.session_state._setup_done = True
            st.session_state._deepseek_key = dk.strip()
            st.session_state._amap_key = ak.strip()
            st.session_state._starcloud_token = sc.strip()
            st.rerun()
    return False

# ---------- MCP 客户端 ----------
class MCPClient:
    def __init__(self): self.process = None
    async def start(self):
        self.process = await asyncio.create_subprocess_exec("water-quality-mcp", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    async def stop(self):
        if self.process: self.process.terminate(); await self.process.wait()
    async def call_tool(self, tool_name, arguments=None):
        if not self.process: await self.start()
        req = {"jsonrpc":"2.0","method":"tools/call","params":{"name":tool_name,"arguments":arguments or {}},"id":1}
        self.process.stdin.write((json.dumps(req)+"\n").encode()); await self.process.stdin.drain()
        line = await self.process.stdout.readline()
        if line:
            res = json.loads(line.decode())
            return res.get("result", res.get("error","无响应"))
        return "无响应"

# ---------- 数据加载（已修复 pH 0 值问题 + 自动清除旧图表）----------
def load_data(uploaded_file):
    """加载并清洗走航数据。

    Returns:
        **(msg, ok)** —— ok 为 False 时 msg 是**错误说明**，调用方必须用
        `st.error` 而不是 `st.success` 展示。

        原先只返回一个字符串，调用方一律 `st.success(msg)`，于是"未找到
        时间列"这类错误被显示在**绿色的成功框**里。又因为解析失败时
        已清空分析状态却没清 df，界面会出现"✅ 数据 1805 条"配着全空的
        分析结果 —— 自相矛盾。现已把副作用全部挪到**解析成功之后**，
        并把成败显式返回给调用方。
    """
    if uploaded_file is None: return "请先上传Excel或CSV文件", False
    try:
        fn = uploaded_file.name.lower()
        if fn.endswith('.csv'):
            try: df = pd.read_csv(uploaded_file, encoding='utf-8')
            except: df = pd.read_csv(uploaded_file, encoding='gbk')
        elif fn.endswith(('.xls','.xlsx')): df = pd.read_excel(uploaded_file)
        else: return "不支持的文件格式", False
    except Exception as e: return f"读取失败: {e}", False

    # ---- 列名识别与时间解析：走 loaders 的统一实现 ----
    # 这里原先有一份**独立的**列映射与时间解析，与 loaders.load_data 里的
    # 那份是两套代码。后果：修列名兼容时只改了 loaders，app.py 仍是旧的
    # （只认一种列命名、时间格式也只认两种）。改为共用，避免再次漏改一边。
    from water_quality_mcp.src.water_quality_mcp.loaders import normalize_columns
    df, _norm_err = normalize_columns(df)
    if _norm_err:
        return _norm_err, False

    # ---- 重置所有分析状态 ----
    st.session_state.black_spots = None
    st.session_state.video_frames = []
    st.session_state.cross_validation_result = None
    st.session_state.chart_captions = {}
    st.session_state.anim_html = None
    st.session_state.anim_file_size = 0
    st.session_state.analysis_done = False
    st.session_state.charts_generated = False
    st.session_state.messages = []
    st.session_state.full_messages = []
    from river_model.state import RiverModelState
    st.session_state.river_state = RiverModelState()

    # ---- 清除所有旧图表文件 ----
    _CHART_FILES = [
        "time_series.png", "trace_map.html",
        "cross_validation.png", "correlation_heatmap.png",
        "river_hydro_profile.png", "river_discharge_profile.png",
        "river_wq_contour.png", "river_wq_profile.png",
        "river_wq_timeseries.png", "river_dashboard.png",
        "river_pollution_animation.html", "_uploaded_data.xlsx",
    ]
    for f in _CHART_FILES:
        if os.path.exists(f):
            try: os.remove(f)
            except OSError: pass

    # ---- 保存上传数据副本供 build_trace_animation 使用 ----
    try:
        df.to_excel("_uploaded_data.xlsx", index=False)
    except Exception:
        pass

    # 数值清洗
    numeric_cols = ['ammonia_nitrogen','dissolved_oxygen','water_temperature','cod','turbidity','ph']
    for col in numeric_cols:
        if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce')

    # 剔除物理上不可能的0值（pH、溶解氧、水温）
    for col in ['ph','dissolved_oxygen','water_temperature']:
        if col in df.columns: df[col] = df[col].replace(0, np.nan)

    # 范围限定
    limits = {'ammonia_nitrogen':(0,1000),'dissolved_oxygen':(0,20),'water_temperature':(0,45),
              'cod':(0,500),'turbidity':(0,2000),'ph':(3,12)}
    for col, (lo,hi) in limits.items():
        if col in df.columns: df[col] = df[col].where((df[col]>=lo)&(df[col]<=hi), np.nan)

    # 线性插值
    for col in numeric_cols:
        if col in df.columns and df[col].isna().sum()>0:
            df[col] = df[col].interpolate(method='linear', limit_direction='both')
    for col in ['longitude','latitude']:
        if col in df.columns: df[col] = df[col].ffill().bfill()

    st.session_state.df = df
    # 返回验证信息
    report = [f"✅ 数据加载完成，共 {len(df)} 条记录"]
    for col in ['ammonia_nitrogen','dissolved_oxygen','turbidity','ph']:
        valid = df[col].dropna()
        report.append(f"   {col}: 有效值={len(valid)}, 均值={valid.mean():.2f}, 最小={valid.min():.2f}, 最大={valid.max():.2f}")
    return "\n".join(report), True

def extract_video(video_file, interval=5):
    """抽帧。返回 (msg, ok)；ok=False 时 msg 是错误说明。

    与 load_data 同一约定 —— 调用方曾一律用 st.success，把错误
    显示成绿色成功框。
    """
    import cv2
    if video_file is None: st.session_state.video_frames = []; return "未上传视频文件", False
    # 分块写入临时文件，避免大视频 OOM
    with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp:
        vpath = tmp.name
        chunk_size = 64 * 1024 * 1024  # 64MB 分块
        while True:
            chunk = video_file.read(chunk_size)
            if not chunk:
                break
            tmp.write(chunk)
    file_size_mb = os.path.getsize(vpath) / (1024 * 1024)
    if file_size_mb < 0.01:
        os.unlink(vpath)
        return "视频文件为空，请检查上传是否完整", False
    # 抑制 FFmpeg H.264 解码警告输出（模块级别已设置，此处兜底）
    os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
    cap = cv2.VideoCapture(vpath, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        os.unlink(vpath)
        return f"无法解码视频（{file_size_mb:.0f}MB），请检查编码格式是否为 H.264/H.265", False
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
                    frames.append({'time_sec': round(frame_idx / fps, 1),
                                   'image_base64': base64.b64encode(buf).decode()})
                else:
                    skipped += 1
        else:
            # 非目标帧只需 grab() 推进解码器，跳过 retrieve 节省内存复制
            if not cap.grab():
                break
        frame_idx += 1
    cap.release(); os.unlink(vpath)
    st.session_state.video_frames = frames
    return f"视频抽帧完成，共 {len(frames)} 帧（总 {total_frames} 帧，间隔 {interval}s，跳过 {skipped} 个暗帧/损坏帧）", True

# ---------- 工具函数 ----------
def tool_data_summary():
    df = st.session_state.df
    if df is None or len(df) == 0: return "数据未加载或无有效记录"
    # 只聚合实际存在的列
    avail_cols = [c for c in ['ammonia_nitrogen','dissolved_oxygen','turbidity','ph','cod'] if c in df.columns]
    if not avail_cols: return "数据中未检测到可识别的监测指标列"
    s = df[avail_cols].agg(['mean','max','min'])
    # 安全的时间范围
    if 'datetime' in df.columns:
        dt_min = df['datetime'].min()
        dt_max = df['datetime'].max()
        time_range = f"{dt_min} ~ {dt_max}" if pd.notna(dt_min) else "时间解析失败"
    else:
        time_range = "无时间列"
    result = {"记录数": len(df), "时间范围": time_range}
    col_labels = {
        'ammonia_nitrogen': ('氨氮(mg/L)', 'mean', 'max'),
        'dissolved_oxygen': ('溶解氧(mg/L)', 'mean', 'min'),
        'turbidity': ('浑浊度(NTU)', 'mean', 'max'),
        'ph': ('pH', 'mean', 'min'),
        'cod': ('COD(mg/L)', 'mean', 'max'),
    }
    for col, (label, s1, s2) in col_labels.items():
        if col in s.columns:
            v1 = s[col].get(s1, 0)
            v2 = s[col].get(s2, 0)
            result[label] = {"均值" if s1=='mean' else s1: round(float(v1), 2),
                           "最大" if s2=='max' else "最小": round(float(v2), 2)}
    return json.dumps(result, ensure_ascii=False, default=str)

# ---------- 黑点分析辅助函数 ----------
def tool_find_black_spots():
    """黑臭检测 - 委托给 MCP analysis 模块统一实现。使用 session_state.analysis_config。"""
    from water_quality_mcp.src.water_quality_mcp.analysis import find_black_spots as _find
    config = st.session_state.analysis_config
    result, black = _find(st.session_state.df, config)
    if black is not None:
        st.session_state.black_spots = black
    return result

def tool_video_analysis():
    import cv2
    frames = st.session_state.video_frames
    if not frames: return "无视频帧"
    results = []
    for f in frames[:20]:
        buf = base64.b64decode(f['image_base64']); img = cv2.imdecode(np.frombuffer(buf,np.uint8), cv2.IMREAD_COLOR)
        if img is None: continue
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV); gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        s,v = hsv[:,:,1].mean(), hsv[:,:,2].mean(); lap = cv2.Laplacian(gray, cv2.CV_64F).var()
        turb = "清澈" if lap>200 else ("轻微浑浊" if lap>100 else ("中度浑浊" if lap>30 else "重度浑浊"))
        col = "疑似黑臭" if (s<40 and v<70) else "正常"
        risk = "需关注" if turb in ["中度浑浊","重度浑浊"] or col=="疑似黑臭" else "正常"
        results.append(f"时间{f['time_sec']}s: 浑浊度={turb}, 颜色={col}, 风险={risk}")
    return "\n".join(results)

def tool_cross_validate_video(time_offset_sec=0, time_window_sec=30):
    from water_quality_mcp.src.water_quality_mcp.analysis import cross_validate_video_with_monitoring
    from water_quality_mcp.src.water_quality_mcp.state import WaterQualityState
    ws = WaterQualityState()
    ws.df = st.session_state.df
    ws.video_frames = st.session_state.video_frames
    ws.black_spots = st.session_state.black_spots
    result = cross_validate_video_with_monitoring(ws, time_offset_sec, time_window_sec)
    st.session_state.cross_validation_result = json.loads(result) if isinstance(result, str) else result
    return result

def tool_generate_cross_validation_chart(time_offset_sec=0):
    from water_quality_mcp.src.water_quality_mcp.charts import generate_cross_validation_chart
    from water_quality_mcp.src.water_quality_mcp.state import WaterQualityState
    ws = WaterQualityState()
    ws.df = st.session_state.df
    ws.video_frames = st.session_state.video_frames
    ws.black_spots = st.session_state.black_spots
    result = generate_cross_validation_chart(ws, "cross_validation.png", time_offset_sec)
    _build_cross_validation_caption()
    return result

def tool_get_comprehensive_summary():
    from water_quality_mcp.src.water_quality_mcp.analysis import build_comprehensive_summary
    from water_quality_mcp.src.water_quality_mcp.state import WaterQualityState
    ws = WaterQualityState()
    ws.df = st.session_state.df
    ws.black_spots = st.session_state.black_spots
    ws.video_frames = st.session_state.video_frames
    ws.cross_validation_result = st.session_state.cross_validation_result
    config = st.session_state.analysis_config
    return build_comprehensive_summary(ws, st.session_state.river_state, config)

def search_nearby_pollution_sources(lat, lon, amap_key, radius=500):
    config = st.session_state.analysis_config
    keywords = config.pollution_keywords
    all_pois = []
    for kw in keywords:
        url = "https://restapi.amap.com/v3/place/around"
        params = {"location":f"{lon},{lat}","keywords":kw,"radius":radius,"key":amap_key,"offset":15}
        try:
            resp = requests.get(url, params=params, timeout=8); data = resp.json()
            if data.get("status")=="1":
                for p in data.get("pois",[]):
                    loc = p.get("location","").split(",")
                    all_pois.append({"name":p.get("name",""),"type":p.get("type",""),"address":p.get("address",""),
                                     "distance":p.get("distance",""),"lat":float(loc[1]) if len(loc)==2 else lat,"lon":float(loc[0]) if len(loc)==2 else lon})
        except: continue
    # 按距离排序
    def dist_num(poi):
        d = poi.get('distance')
        if d is None:
            return 999999
        digits = ''.join(filter(str.isdigit, str(d)))
        return int(digits) if digits else 999999
    all_pois.sort(key=dist_num)
    return all_pois

def tool_generate_trace_map(amap_key=None, cloud_token=None):
    import folium
    from folium.plugins import HeatMap
    from water_quality_mcp.src.water_quality_mcp.analysis import get_severity_info, get_cluster_extent
    df = st.session_state.df; black = st.session_state.black_spots
    if df is None or black is None: return "数据或黑点未就绪"
    coords = df[['latitude','longitude']].dropna().values
    m = folium.Map(location=[df['latitude'].mean(), df['longitude'].mean()], zoom_start=15, tiles=None)
    # —— 底图（多源 LayerControl 切换，应对部分服务器在国内不可达）——
    folium.TileLayer(
        tiles='https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png',
        name='CartoDB Voyager (WGS-84 推荐)',
        attr='CartoDB', max_zoom=19, show=True,
        subdomains='abc',
    ).add_to(m)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        name='Esri 卫星图 (WGS-84 无偏移)',
        attr='Esri', max_zoom=19, show=False,
    ).add_to(m)
    folium.TileLayer(
        tiles='OpenStreetMap',
        name='OSM 街道图 (WGS-84)',
        attr='OSM', max_zoom=19, show=False,
    ).add_to(m)
    folium.TileLayer(
        tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png',
        name='CartoDB Light (WGS-84)',
        attr='CartoDB', max_zoom=19, show=False,
        subdomains='abc',
    ).add_to(m)
    token = cloud_token or _get_starcloud_token()
    if token and "你的星图" not in token:
        geo_tile_url = f'https://tiles1.geovisearth.com/base/v1/img/{{z}}/{{x}}/{{y}}?format=webp&tmsIds=w&token={token}'
        folium.TileLayer(
            tiles=geo_tile_url,
            name='星图地球卫星图 (国内可能GCJ-02偏移)',
            attr='星图地球', max_zoom=18, show=False,
        ).add_to(m)
    if amap_key:
        tile_url = 'https://webst{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}'
        folium.TileLayer(
            tiles=tile_url,
            name='高德卫星图 (GCJ-02 有偏移)',
            attr='高德', max_zoom=18, show=False,
            subdomains=['01', '02', '03', '04'],
        ).add_to(m)
    # 轨迹 + 起点终点
    folium.PolyLine(coords, weight=2, color='blue', opacity=0.5).add_to(m)
    folium.map.Marker(coords[0], icon=folium.DivIcon(html='<div style="color:green;font-weight:bold;">起点</div>')).add_to(m)
    folium.map.Marker(coords[-1], icon=folium.DivIcon(html='<div style="color:red;font-weight:bold;">终点</div>')).add_to(m)
    # 热力图
    HeatMap(black[['latitude','longitude']].values, radius=20, blur=10).add_to(m)
    # 采样点（每30个）
    sample = df.iloc[::30]
    for _, row in sample.iterrows():
        nh3 = f"{row['ammonia_nitrogen']:.1f}" if pd.notna(row.get('ammonia_nitrogen')) else '--'
        do = f"{row['dissolved_oxygen']:.1f}" if pd.notna(row.get('dissolved_oxygen')) else '--'
        cod = f"{row['cod']:.0f}" if pd.notna(row.get('cod')) else '--'
        turb = f"{row['turbidity']:.0f}" if pd.notna(row.get('turbidity')) else '--'
        popup_html = f"<b>{row['datetime']}</b><br>氨氮:{nh3} mg/L<br>DO:{do} mg/L<br>COD:{cod} mg/L<br>浊度:{turb} NTU"
        folium.CircleMarker([row['latitude'], row['longitude']], radius=3, color='darkblue', fill=True, fill_opacity=0.6,
                            popup=folium.Popup(popup_html, max_width=220)).add_to(m)
    # 聚类渲染（按严重等级着色 + 虚线范围圆 + 每聚类搜 POI）
    for cid in sorted(black['cluster_id'].unique()):
        if cid == -1: continue
        cluster = black[black['cluster_id'] == cid]
        avg_score = float(cluster['black_score'].mean())
        _, sev_label, sev_color = get_severity_info(avg_score)
        # 聚类范围虚线圆
        clat, clon, extent_r = get_cluster_extent(cluster)
        folium.Circle([clat, clon], radius=extent_r, color=sev_color, weight=1.5, fill=True,
                      fill_opacity=0.06, dash_array='5,5').add_to(m)
        # 聚类中心实心圆
        rad = max(12, min(30, len(cluster) * 1.5))
        t_start = cluster['datetime'].min().strftime('%H:%M')
        t_end = cluster['datetime'].max().strftime('%H:%M')
        nh3_m = f"{cluster['ammonia_nitrogen'].mean():.2f}" if 'ammonia_nitrogen' in cluster.columns else '--'
        do_m = f"{cluster['dissolved_oxygen'].mean():.2f}" if 'dissolved_oxygen' in cluster.columns else '--'
        popup_html = (f"<b>热点 {cid} — {sev_label}</b><br>"
                      f"采样点: {len(cluster)} 个<br>时间: {t_start} ~ {t_end}<br>"
                      f"评分均值: {avg_score:.2f}<br>氨氮均值: {nh3_m} mg/L<br>DO均值: {do_m} mg/L")
        if 'cod' in cluster.columns:
            popup_html += f"<br>COD均值: {cluster['cod'].mean():.0f} mg/L"
        if 'turbidity' in cluster.columns:
            popup_html += f"<br>浊度均值: {cluster['turbidity'].mean():.0f} NTU"
        if 'ph' in cluster.columns:
            popup_html += f"<br>pH均值: {cluster['ph'].mean():.1f}"
        folium.CircleMarker([clat, clon], radius=rad, color=sev_color, fill=True, fill_opacity=0.55,
                            weight=3, popup=folium.Popup(popup_html, max_width=280)).add_to(m)
        # 对每个聚类搜索周边污染源
        if amap_key:
            pois = search_nearby_pollution_sources(clat, clon, amap_key)
            for poi in pois[:5]:
                folium.Marker([poi['lat'], poi['lon']],
                              popup=f"{poi['name']}({poi['type']})<br>{poi.get('address','')}",
                              icon=folium.Icon(color='black', icon='industry')).add_to(m)
    # 污染最严重 Top-5 个体点位标记
    top5 = black.nlargest(5, 'black_score')
    for i, (_, row) in enumerate(top5.iterrows(), 1):
        sev_color = row['severity_color']
        _, sev_label, _ = get_severity_info(row['black_score'])
        icon_html = (
            f'<div style="background:{sev_color};color:#fff;width:26px;height:26px;'
            f'border-radius:50%;text-align:center;line-height:26px;'
            f'font-weight:bold;font-size:14px;border:2px solid #fff;'
            f'box-shadow:0 0 8px rgba(0,0,0,0.6);">{i}</div>'
        )
        t_str = row['datetime'].strftime('%Y-%m-%d %H:%M:%S')
        nh3_v = f"{row['ammonia_nitrogen']:.2f}" if pd.notna(row.get('ammonia_nitrogen')) else '--'
        do_v = f"{row['dissolved_oxygen']:.2f}" if pd.notna(row.get('dissolved_oxygen')) else '--'
        cod_v = f"{row['cod']:.0f}" if pd.notna(row.get('cod')) else '--'
        turb_v = f"{row['turbidity']:.0f}" if pd.notna(row.get('turbidity')) else '--'
        ph_v = f"{row['ph']:.1f}" if pd.notna(row.get('ph')) else '--'
        popup_html = (
            f"<b>第{i}严重点位 — {sev_label}</b><br>"
            f"评分: {row['black_score']:.2f}<br>"
            f"时间: {t_str}<br>"
            f"氨氮: {nh3_v} mg/L | DO: {do_v} mg/L<br>"
            f"COD: {cod_v} mg/L | 浊度: {turb_v} NTU<br>"
            f"pH: {ph_v}"
        )
        folium.Marker(
            [row['latitude'], row['longitude']],
            icon=folium.DivIcon(html=icon_html),
            popup=folium.Popup(popup_html, max_width=280)
        ).add_to(m)

    # 严重程度图例 (GB3838 V类 + 内梅罗指数)
    legend_html = '''
    <div style="position:fixed;bottom:30px;left:30px;z-index:9999;
        background:rgba(255,255,255,0.92);padding:10px 14px;
        border-radius:6px;font-size:12px;line-height:1.7;
        box-shadow:0 0 6px rgba(0,0,0,0.25);">
    <b>GB3838-2002 V类 污染等级 (内梅罗指数)</b><br>
    <span style="color:#2E8B57;">&#9679;</span> 达V类<br>
    <span style="color:#FFA500;">&#9679;</span> 轻污染 (超V类)<br>
    <span style="color:#FF4500;">&#9679;</span> 污染 (超V类2-3倍)<br>
    <span style="color:#DC143C;">&#9679;</span> 重污染 (超V类3-5倍)<br>
    <span style="color:#8B0000;">&#9679;</span> 严重污染 (超V类&gt;5倍)<br>
    <span style="color:gray;">---</span> 虚线圆 = 聚类范围<br>
    <span style="background:#DC143C;color:#fff;border-radius:50%;display:inline-block;
    width:14px;height:14px;text-align:center;line-height:14px;font-size:10px;">1</span>
    数字标记 = 污染最严重点位 (Top 5)
    </div>
    '''
    m.get_root().html.add_child(folium.Element(legend_html))
    folium.LatLngPopup().add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.save('trace_map.html'); st.session_state.charts_generated = True
    # 生成图表说明
    _build_trace_map_caption(black, amap_key)
    # 返回数据摘要供 LLM 消费
    black = st.session_state.black_spots
    summary = {"图表": "交互式溯源地图已生成", "文件": "trace_map.html"}
    if black is not None and len(black) > 0:
        clusters_out = []
        for cid in sorted(black['cluster_id'].unique()):
            if cid == -1: continue
            c = black[black['cluster_id'] == cid]
            clusters_out.append({
                "聚类ID": int(cid), "点数": len(c), "内梅罗均值": round(float(c['black_score'].mean()), 2),
                "主导黑臭等级": c['severity_label'].mode().iloc[0] if len(c) > 0 else "",
                "中心经纬度": f"{c['latitude'].mean():.5f},{c['longitude'].mean():.5f}",
                "氨氮均值": round(float(c['ammonia_nitrogen'].mean()), 1),
                "DO均值": round(float(c['dissolved_oxygen'].mean()), 2),
            })
        summary["聚类概览"] = clusters_out
        top3 = black.nlargest(3, 'black_score')
        summary["最严重3点"] = [{
            "排名": i+1, "经纬度": f"{r['latitude']:.5f},{r['longitude']:.5f}",
            "内梅罗指数": round(float(r['black_score']), 2), "黑臭等级": r['severity_label'],
            "氨氮": round(float(r['ammonia_nitrogen']), 1), "DO": round(float(r['dissolved_oxygen']), 2),
        } for i, (_, r) in enumerate(top3.iterrows())]
    return json.dumps(summary, ensure_ascii=False)

# ---------- 图表说明自动生成 ----------

def _build_time_series_caption(df, black):
    """根据数据自动生成时间序列图说明"""
    if df is None:
        st.session_state.chart_captions["time_series"] = "时间序列图：展示走航监测过程中氨氮、溶解氧、浊度、pH 四项指标随时间的变化趋势。"
        return
    t_start = df['datetime'].min().strftime('%Y-%m-%d %H:%M')
    t_end = df['datetime'].max().strftime('%H:%M')
    nh3_peak = df['ammonia_nitrogen'].max() if 'ammonia_nitrogen' in df.columns else 0
    do_min = df['dissolved_oxygen'].min() if 'dissolved_oxygen' in df.columns else 0
    parts = [
        f"**时间序列图** — 监测时段 {t_start} ~ {t_end}，共 {len(df)} 条记录。",
        f"氨氮峰值 {nh3_peak:.1f} mg/L，溶解氧最低 {do_min:.2f} mg/L。",
    ]
    if black is not None and len(black) > 0:
        n_black = len(black)
        sev_dist = black['severity_label'].value_counts().to_dict()
        mild = sev_dist.get('轻度黑臭', 0)
        severe = sev_dist.get('重度黑臭', 0)
        parts.append(f"检出黑臭点位 {n_black} 个（轻度 {mild}，重度 {severe}），散点颜色标注于图中。")
    parts.append("红色虚线 = GB3838-2002 V类标准限值（氨氮≤2.0 mg/L，DO≥2.0 mg/L）。")
    st.session_state.chart_captions["time_series"] = " ".join(parts)

def _build_trace_map_caption(black, amap_key):
    """根据数据自动生成溯源地图说明"""
    if black is None or len(black) == 0:
        st.session_state.chart_captions["trace_map"] = "交互式溯源地图：显示走航轨迹、采样点分布和污染热力图。"
        return
    clusters = [c for c in sorted(black['cluster_id'].unique()) if c != -1]
    n_clusters = len(clusters)
    worst_sev = black['severity_label'].mode().iloc[0] if len(black) > 0 else "未知"
    parts = [
        f"**交互式溯源地图** — DBSCAN 聚类识别 {n_clusters} 个污染热点区域，最严重等级为「{worst_sev}」。",
        f"虚线圆 = 聚类影响范围，实心圆 = 聚类中心（大小反映样本密度），数字标记 = Top 5 最严重点位。",
    ]
    if amap_key:
        parts.append("黑色工厂图标 = 周边疑似污染源（高德POI搜索）。")
    parts.append("底图支持切换高德卫星图（GCJ-02坐标系，有偏移），点击标记可查看详情。")
    st.session_state.chart_captions["trace_map"] = " ".join(parts)

def _build_cross_validation_caption():
    """根据交叉印证结果生成说明"""
    cv = st.session_state.cross_validation_result
    if cv is None:
        st.session_state.chart_captions["cross_validation"] = "视频-传感器交叉印证图：上图为视频 Laplacian 方差与传感器浊度对比，下图为 HSV 亮度与氨氮对比。"
        return
    parts = ["**交叉印证图** — 上：视频 Laplacian 方差（绿线，越高越清晰）vs 传感器浊度（蓝线，越高越浑浊），预期负相关。"]
    parts.append("下：视频 HSV 亮度（绿线，越低越暗）vs 传感器氨氮（红线），预期负相关（黑臭水体色深氨氮高）。")

    # 键名以 analysis.cross_validate_video_with_monitoring 的实际产出为准。
    # 此处曾经整段读的是英文键名（correlation_pairs / pair / pearson_r /
    # agreement / agreement_rate），后端一个都没有 —— 说明文字里因此
    # 从未出现过任何相关系数信息。
    corr = cv.get("相关系数矩阵", [])
    if isinstance(corr, list) and corr:
        # corr 已按 |r| 降序排列, 取前 3 条最强的
        for item in corr[:3]:
            if not isinstance(item, dict):
                continue
            pair = f"{item.get('视频指标', '?')} × {item.get('传感器指标', '?')}"
            r = item.get("Pearson_r")
            sig = item.get("显著性", "")
            if isinstance(r, (int, float)):
                parts.append(f"{pair}: r={r:.3f}{'' if sig == '不显著' else ' ' + str(sig)}")

    agree = cv.get("黑臭判定一致性", {})
    if isinstance(agree, dict):
        rate = agree.get("一致率_%")
        if isinstance(rate, (int, float)):
            parts.append(f"黑臭判定一致率: {rate}%")
        # 后端已内置一句人话解读, 直接用, 避免自行表述产生偏差
        interp = agree.get("解读", "")
        if interp:
            parts.append(str(interp))

    findings = cv.get("关键发现", [])
    if isinstance(findings, list):
        parts.extend(str(f) for f in findings[:2])

    st.session_state.chart_captions["cross_validation"] = "\n\n".join(parts)

def _build_correlation_heatmap_caption():
    """根据交叉印证结果生成相关系数热力图说明"""
    cv = st.session_state.cross_validation_result
    if cv is None:
        st.session_state.chart_captions["correlation_heatmap"] = "相关系数热力图：展示视频视觉指标（Laplacian方差/HSV饱和度/亮度）与传感器指标（浊度/氨氮/DO/COD）的 Pearson 相关系数矩阵。"
        return
    # 键名同上, 以 cross_validate_video_with_monitoring 的实际产出为准
    corr = cv.get("相关系数矩阵", [])
    parts = ["**相关系数热力图** — 视频视觉指标 × 传感器指标的 Pearson r 矩阵。"]
    if isinstance(corr, list) and corr:
        sig_items = [c for c in corr if isinstance(c, dict) and c.get("显著性") != "不显著"]
        best = sig_items[0] if sig_items else None
        if best:
            parts.append(
                f"最强显著相关: {best.get('视频指标', '?')} × {best.get('传感器指标', '?')} "
                f"(r={best.get('Pearson_r', '?')}, {best.get('显著性', '')})。"
            )
        else:
            parts.append("未发现显著相关项。")
    parts.append("红色=正相关，蓝色=负相关，颜色越深相关性越强。")
    st.session_state.chart_captions["correlation_heatmap"] = " ".join(parts)

def _get_wq_conc(wq):
    """获取 WQResult 中的主要污染物浓度数组。"""
    if wq.ammonia is not None:
        return wq.ammonia
    if wq.cbod is not None:
        return wq.cbod
    return wq.dissolved_oxygen

def _build_river_animation_caption(anim_path=None):
    """根据河道模拟结果生成污染溯源动画说明"""
    rs = st.session_state.river_state
    parts = ["**污染溯源动画** — 基于真实走航数据 + Preissmann 非恒定流模型 + ADR 水质模型生成。"]
    if rs.has_hydro():
        h = rs.hydro_result
        parts.append(f"模拟时长 {h.t[-1]:.0f}h，{len(h.chainage)} 个断面，潮汐振幅 {h.params.get('tidal_amplitude_m',0.8):.2f}m。")
    if rs.has_wq():
        wq = rs.wq_result
        conc = _get_wq_conc(wq)
        peak_c = float(np.max(conc))
        peak_t = float(wq.t[int(np.argmax(np.max(conc, axis=1)))])
        parts.append(f"污染物峰值浓度 {peak_c:.2f} mg/L，出现在模拟第 {peak_t:.1f}h。")
    parts.append("色标基于 GB3838-2002 五类水质标准，支持播放/暂停、时间滑块和速度调节。")
    st.session_state.chart_captions["river_animation"] = " ".join(parts)

def _render_river_chart_caption(fname, title, rs):
    """为河道模型图表渲染数据驱动的说明"""
    hydro = rs.hydro_result if rs.has_hydro() else None
    wq = rs.wq_result if rs.has_wq() else None
    if "hydro_profile" in fname:
        if hydro is not None:
            z_max = float(np.max(hydro.water_level)); z_min = float(np.min(hydro.water_level))
            st.caption(f"沿程水位剖面 — 最高水位 {z_max:.3f}m，最低 {z_min:.3f}m，{len(hydro.chainage)} 个断面。表征潮汐影响下的水面线变化。")
        else:
            st.caption(f"沿程水位剖面 — 基于 Saint-Venant 方程 Preissmann 隐式格式求解，展示各断面水位沿程分布。")
    elif "discharge_profile" in fname:
        if hydro is not None:
            q_max = float(np.max(np.abs(hydro.discharge)))
            st.caption(f"沿程流量剖面 — 最大流量 {q_max:.2f} m³/s，正值为顺流向（落潮），负值为逆流向（涨潮）。")
        else:
            st.caption(f"沿程流量剖面 — 展示各断面流量沿程分布，受潮汐边界条件影响。")
    elif "wq_contour" in fname:
        if wq is not None:
            conc = _get_wq_conc(wq)
            peak_c = float(np.max(conc)); peak_t = float(wq.t[int(np.argmax(np.max(conc, axis=1)))])
            st.caption(f"浓度时空分布 — 峰值 {peak_c:.2f} mg/L，出现在 {peak_t:.1f}h。横轴为时间，纵轴为桩号，颜色深浅反映污染物浓度。")
        else:
            st.caption(f"浓度时空分布 — 横轴时间、纵轴桩号，颜色深浅反映污染物浓度沿河道随时间的变化。")
    elif "wq_profile" in fname:
        if wq is not None:
            st.caption(f"浓度沿程剖面 — 展示特定时刻各断面污染物浓度，用于识别污染峰值位置和影响范围。")
        else:
            st.caption(f"浓度沿程剖面 — 展示特定时刻各断面污染物浓度沿程分布。")
    elif "wq_timeseries" in fname:
        if wq is not None:
            st.caption(f"浓度过程线 — 展示特定断面污染物浓度随时间变化，反映污染团经过时的浓度升降过程。")
        else:
            st.caption(f"浓度过程线 — 展示特定断面污染物浓度随时间的变化趋势。")
    elif "dashboard" in fname:
        if hydro is not None and wq is not None:
            st.caption(f"综合仪表盘 — 汇总水位、流量、流速及水质浓度关键指标，一图纵览模拟全貌。")
        else:
            st.caption(f"综合仪表盘 — 汇总水动力和水质模拟关键结果。")
    else:
        st.caption(f"{title} — 基于雅瑶水道一维水动力-水质模型生成。")

# ---------- 图表工具函数 ----------

def tool_generate_time_series():
    import matplotlib.pyplot as plt
    df = st.session_state.df; black = st.session_state.black_spots
    if df is None: return "数据未加载"
    font = st.session_state.font_prop; fig, axes = plt.subplots(4,1,figsize=(12,10),sharex=True)
    axes[0].plot(df['datetime'], df['ammonia_nitrogen'], linewidth=0.8); axes[0].axhline(2.0,color='r',ls='--')
    if black is not None and len(black) > 0:
        sc_colors = black['severity_color'].values if 'severity_color' in black.columns else 'red'
        axes[0].scatter(black['datetime'], black['ammonia_nitrogen'], c=sc_colors, s=10, edgecolors='black', linewidths=0.2)
    axes[0].set_ylabel('氨氮 (mg/L)', fontproperties=font); axes[0].grid(True,alpha=0.3)
    axes[1].plot(df['datetime'], df['dissolved_oxygen'], linewidth=0.8); axes[1].axhline(2.0,color='r',ls='--')
    if black is not None and len(black) > 0:
        sc_colors = black['severity_color'].values if 'severity_color' in black.columns else 'red'
        axes[1].scatter(black['datetime'], black['dissolved_oxygen'], c=sc_colors, s=10, edgecolors='black', linewidths=0.2)
    axes[1].set_ylabel('溶解氧 (mg/L)', fontproperties=font); axes[1].grid(True,alpha=0.3)
    axes[2].plot(df['datetime'], df['turbidity'], linewidth=0.8)
    if black is not None and len(black) > 0:
        sc_colors = black['severity_color'].values if 'severity_color' in black.columns else 'red'
        axes[2].scatter(black['datetime'], black['turbidity'], c=sc_colors, s=10, edgecolors='black', linewidths=0.2)
    axes[2].set_ylabel('浑浊度 (NTU)', fontproperties=font); axes[2].grid(True,alpha=0.3)
    axes[3].plot(df['datetime'], df['ph'], linewidth=0.8, color='green'); axes[3].axhline(6.0,color='gray',ls='--'); axes[3].axhline(9.0,color='gray',ls='--')
    if black is not None and len(black) > 0:
        sc_colors = black['severity_color'].values if 'severity_color' in black.columns else 'red'
        axes[3].scatter(black['datetime'], black['ph'], c=sc_colors, s=10, edgecolors='black', linewidths=0.2)
    axes[3].set_ylabel('pH', fontproperties=font); axes[3].set_xlabel('时间', fontproperties=font); axes[3].grid(True,alpha=0.3)
    # 黑臭等级图例 (散点颜色来源: 《城市黑臭水体整治工作指南》)
    from matplotlib.patches import Patch
    legend_patches = [
        Patch(color='#FFA500', label='轻度黑臭'),
        Patch(color='#8B0000', label='重度黑臭'),
    ]
    fig.legend(handles=legend_patches, loc='lower center', ncol=4, fontsize=8, framealpha=0.8)
    plt.suptitle('水质时间序列与黑点分布', fontsize=14, fontproperties=font); plt.tight_layout()
    fig.savefig('time_series.png', dpi=150); plt.close(fig)
    st.session_state.charts_generated = True
    # 生成图表说明
    _build_time_series_caption(df, black)
    # 返回数据摘要供 LLM 消费 (非截断)
    black = st.session_state.black_spots
    summary = {"图表": "时间序列图已生成", "文件": "time_series.png"}
    if black is not None and len(black) > 0:
        summary["黑点概览"] = {
            "总黑点数": len(black),
            "黑臭分布": black['severity_label'].value_counts().to_dict(),
            "氨氮峰值_mgL": round(float(black['ammonia_nitrogen'].max()), 1),
            "DO最低_mgL": round(float(black['dissolved_oxygen'].min()), 2),
        }
    return json.dumps(summary, ensure_ascii=False)

def reverse_geocode(lat, lon, amap_key):
    url = "https://restapi.amap.com/v3/geocode/regeo"
    params = {"location":f"{lon},{lat}","key":amap_key,"radius":1000,"extensions":"all"}
    try:
        resp = requests.get(url, params=params, timeout=10); data = resp.json()
        if data.get("status")=="1":
            regeo = data["regeocode"]; address = regeo.get("formatted_address","")
            province = regeo.get("addressComponent",{}).get("province","")
            pois = [{"名称":p.get("name",""),"类型":p.get("type",""),"距离":p.get("distance","")} for p in (regeo.get("pois") or [])[:10]]
            return json.dumps({"地址":address,"省份":province,"周边POI":pois}, ensure_ascii=False)
        return "逆地理编码失败"
    except: return "逆地理编码异常"

async def _call_mcp(tool_name, params=None):
    client = MCPClient(); await client.start(); result = await client.call_tool(tool_name, params); await client.stop(); return result

def tool_get_national_station_data(province="", keyword="", live=True):
    """获取国控水质站点数据 (v2.0 HTTP API 直连)。
    live=True → 直接调用国家监测平台后端接口, 返回实时监测值 (pH/DO/氨氮/总磷/总氮等)
    live=False → 内置站点数据库
    """
    from water_quality_mcp.src.water_quality_mcp.national_station import get_national_station_data
    return get_national_station_data(province=province, keyword=keyword, live=live)

def tool_get_satellite_image(lat, lon, zoom=16, cloud_token=None):
    token = cloud_token or _get_starcloud_token()
    if not token or "你的星图" in token:
        return "未配置星图地球 Token，请在侧边栏填写"
    # 将经纬度转为 XYZ 瓦片编号
    n = 2 ** zoom
    x_tile = int((lon + 180.0) / 360.0 * n)
    y_tile = int((1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
    tile_url = f"https://tiles1.geovisearth.com/base/v1/img/{zoom}/{x_tile}/{y_tile}?format=webp&tmsIds=w&token={token}"
    try:
        resp = requests.get(tile_url, timeout=15)
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
            return json.dumps({
                "状态": "卫星影像可用",
                "坐标": f"{lat:.6f}, {lon:.6f}",
                "缩放级别": zoom,
                "瓦片编号": f"z={zoom}, x={x_tile}, y={y_tile}",
                "影像大小": f"{len(resp.content)} 字节"
            }, ensure_ascii=False)
        else:
            return f"卫星影像获取失败: HTTP {resp.status_code}，缩放级别 {zoom} 可能超出覆盖范围"
    except Exception as e:
        return f"卫星影像请求异常: {e}"

def _try_retrieve_treatment_cases(summary, top_k=3):
    """尝试从 RAG 知识库检索相似治理案例。失败时返回空字符串（优雅降级）。"""
    try:
        from water_quality_mcp.src.water_quality_mcp.rag_engine import (
            get_rag_engine,
        )
        engine = get_rag_engine()
        engine._collection = None  # 强制从磁盘刷新
        engine._chroma_client = None  # 也重置客户端
        query = engine.build_query_from_summary(summary)
        results = engine.retrieve(query, top_k=top_k)
        return engine.format_context(results)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("RAG 检索失败（将降级处理）: %s", e)
        return ""

def tool_search_treatment_cases(query, top_k=5):
    """RAG 工具：从知识库检索相似治理案例。"""
    try:
        from water_quality_mcp.src.water_quality_mcp.rag_engine import (
            search_treatment_cases,
        )
        return search_treatment_cases(query, top_k)
    except Exception as e:
        import json as _json
        return _json.dumps({
            "状态": "RAG 检索失败",
            "错误": str(e)[:200],
            "建议": "请先运行 python scripts/build_rag_index.py 构建索引，或检查 chromadb 是否正常安装",
        }, ensure_ascii=False)

def tool_generate_report(api_key, summary):
    # RAG 检索：获取相似治理案例
    reference_cases = _try_retrieve_treatment_cases(summary, top_k=3)

    if reference_cases and "暂无相关治理案例" not in reference_cases:
        prompt = (
            "你是水环境溯源专家。请依据《城市黑臭水体整治工作指南》(2015)和GB 3838-2002撰写溯源报告。\n\n"
            "## 参考治理案例（从知识库检索的相似案例，仅借鉴治理思路和方法论）\n"
            f"{reference_cases}\n\n"
            "## 当前监测数据综合分析\n"
            f"{summary}\n\n"
            "撰写规则（严格遵守）：\n"
            "1. 报告的主体内容必须完全基于「当前监测数据综合分析」中的数据，不得使用参考案例中的数据（如案例中的氨氮值、COD值、治理周期、具体地点等）替代当前水体的实际情况。\n"
            "2. 参考案例仅供借鉴治理思路和技术路线，引用时必须注明来源，例如：参考类似案例，建议采用截污纳管与生态修复相结合的方式。\n"
            "3. 严禁将参考案例的地点、数据、治理效果直接当作当前水体的结论输出。当前水体的污染程度、地理位置、污染成因必须严格来源于监测数据。\n"
            "4. 治理建议应结合参考案例的治理方法，但需根据当前水体的实际污染物浓度、水体类型、周边环境做适配，不可照搬。"
        )
    else:
        prompt = f"你是水环境溯源专家。请严格依据GB 3838-2002撰写溯源报告。\n{summary}"

    client = OpenAI(base_url=BASE_URL, api_key=api_key,
                    timeout=httpx.Timeout(60.0, connect=10.0))
    resp = client.chat.completions.create(model=MODEL_NAME, messages=[{"role":"user","content":prompt}], temperature=0.1, max_tokens=2000)
    return resp.choices[0].message.content

# ---------- 工具注册 ----------
from river_model.tool_specs import RIVER_TOOL_SPECS

tools = [
    {"type":"function","function":{"name":"get_data_summary","description":"获取水质数据统计","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"find_black_spots","description":"基于《城市黑臭水体整治工作指南》(2015)和GB3838-2002发现水质黑点并DBSCAN聚类，返回黑臭等级分布(轻度/重度)和聚类详情","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"analyze_video","description":"分析视频帧","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"cross_validate_video","description":"将视频帧视觉特征(Loaclacian方差/HSV)与走航监测数据(浊度/氨氮/DO/COD)交叉印证,计算Pearson相关系数和黑臭判定一致率","parameters":{"type":"object","properties":{"time_offset_sec":{"type":"integer","description":"视频第0秒相对第一条记录的时间偏移,默认0"},"time_window_sec":{"type":"integer","description":"匹配窗口宽度秒,默认30"}}}}},
    {"type":"function","function":{"name":"generate_cross_validation_chart","description":"生成视频-传感器交叉印证双面板对比图","parameters":{"type":"object","properties":{"time_offset_sec":{"type":"integer","description":"时间偏移秒,默认0"}}}}},
    {"type":"function","function":{"name":"get_comprehensive_summary","description":"获取综合分析摘要JSON,聚合数据概览/黑臭检测(聚类详情+Top5严重点位)/空间分析/交叉印证/河道模型所有结果","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"search_treatment_cases","description":"检索并合并同来源治理参考案例；具体方法或数值须引用返回的证据ID和原文片段，资料未核验且不是责任依据","parameters":{"type":"object","properties":{"query":{"type":"string","description":"检索查询(如'氨氮超标 黑臭水体 城市河道治理')"},"top_k":{"type":"integer","minimum":1,"maximum":10,"description":"返回案例数(默认5)"}},"required":["query"]}}},
    {"type":"function","function":{"name":"generate_final_report","description":"生成溯源报告","parameters":{"type":"object","properties":{"summary":{"type":"string"}},"required":["summary"]}}},
    {"type":"function","function":{"name":"generate_time_series_chart","description":"绘制时间序列图","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"generate_trace_map","description":"生成交互地图","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"reverse_geocode","description":"将精确经纬度转为地址/省份","parameters":{"type":"object","properties":{"lat":{"type":"number"},"lon":{"type":"number"}},"required":["lat","lon"]}}},
    {"type":"function","function":{"name":"search_nearby_pollution_sources","description":"搜索附近污染源（按距离排序）","parameters":{"type":"object","properties":{"lat":{"type":"number"},"lon":{"type":"number"},"radius":{"type":"number"}},"required":["lat","lon"]}}},
    {"type":"function","function":{"name":"get_national_station_data","description":"获取国控站点实时数据 (v2.0 HTTP API 直连, 无需 Playwright)。返回站名/pH/溶解氧/氨氮/总磷/总氮等实时监测值。数据每小时更新。","parameters":{"type":"object","properties":{"province":{"type":"string","description":"省份(如'广东省')"},"keyword":{"type":"string","description":"站名或流域关键词"},"live":{"type":"boolean","description":"是否获取实时数据(默认true,直接HTTP API)"}}}}},
    {"type":"function","function":{"name":"get_satellite_image","description":"获取卫星影像","parameters":{"type":"object","properties":{"lat":{"type":"number"},"lon":{"type":"number"},"zoom":{"type":"integer","default":16}},"required":["lat","lon"]}}},
    *RIVER_TOOL_SPECS,
]

MODEL_NAME = "deepseek-chat"
BASE_URL = "https://api.deepseek.com/v1"

def execute_tool(name, args, api_key, amap_key, cloud_token=None):
    if name == "get_data_summary": return tool_data_summary()
    elif name == "find_black_spots": return tool_find_black_spots()
    elif name == "analyze_video": return tool_video_analysis()
    elif name == "cross_validate_video": return tool_cross_validate_video(**args)
    elif name == "generate_cross_validation_chart": return tool_generate_cross_validation_chart(**args)
    elif name == "get_comprehensive_summary": return tool_get_comprehensive_summary()
    elif name == "search_treatment_cases": return tool_search_treatment_cases(**args)
    elif name == "generate_final_report": return tool_generate_report(api_key, **args)
    elif name == "generate_time_series_chart": return tool_generate_time_series()
    elif name == "generate_trace_map": return tool_generate_trace_map(amap_key, cloud_token)
    elif name == "reverse_geocode": return reverse_geocode(args.get("lat"), args.get("lon"), amap_key)
    elif name == "search_nearby_pollution_sources":
        black = st.session_state.black_spots
        if black is not None and len(black)>0:
            best_cluster_id = black['cluster_id'].value_counts().idxmax()
            best_cluster = black[black['cluster_id']==best_cluster_id]
            lat = best_cluster['latitude'].mean()
            lon = best_cluster['longitude'].mean()
        else:
            lat = args.get("lat"); lon = args.get("lon")
        config = st.session_state.analysis_config
        # 使用多渠道溯源引擎
        from water_quality_mcp.src.water_quality_mcp.trace_engine import multi_source_trace
        return multi_source_trace(lat, lon, amap_key, config)
    elif name == "get_national_station_data":
        return tool_get_national_station_data(args.get("province",""), args.get("keyword",""))
    elif name == "get_satellite_image":
        return tool_get_satellite_image(args.get("lat"), args.get("lon"), args.get("zoom",16), cloud_token)
    elif name in ("init_river_model", "run_hydrodynamic_simulation", "simulate_pollution_event",
                   "get_concentration_profile", "compare_scenarios", "export_river_model_data"):
        from river_model.tools import (
            tool_river_init, tool_river_hydro_simulation, tool_river_pollution_event,
            tool_river_concentration_profile, tool_river_compare_scenarios, tool_river_export_data,
        )
        if name == "init_river_model": return tool_river_init(st.session_state.river_state, **args)
        elif name == "run_hydrodynamic_simulation":
            args = dict(args)
            args.setdefault("tidal_amplitude_m", 0.0)
            if args["tidal_amplitude_m"] != 0:
                return json.dumps({"error": "潮汐模型未通过质量守恒验收，Agent 仅支持恒定流模拟"}, ensure_ascii=False)
            return tool_river_hydro_simulation(st.session_state.river_state, **args)
        elif name == "simulate_pollution_event": return tool_river_pollution_event(st.session_state.river_state, monitoring_data=st.session_state.get('df'), **args)
        elif name == "get_concentration_profile": return tool_river_concentration_profile(st.session_state.river_state, **args)
        elif name == "compare_scenarios": return tool_river_compare_scenarios(st.session_state.river_state, **args)
        elif name == "export_river_model_data": return tool_river_export_data(st.session_state.river_state, **args)
    else: return f"未知工具: {name}"

def _ensure_charts(amap_key, cloud_token=None):
    if not os.path.exists("time_series.png") and st.session_state.df is not None:
        tool_generate_time_series()
    if not os.path.exists("trace_map.html") and st.session_state.df is not None:
        tool_generate_trace_map(amap_key, cloud_token)
    # 河道模型图表
    if st.session_state.river_state.has_hydro() and not os.path.exists("river_hydro_profile.png"):
        from river_model.visualization import plot_water_level_profile, plot_discharge_profile
        try:
            plot_water_level_profile(st.session_state.river_state.hydro_result,
                                     st.session_state.river_state.config, "river_hydro_profile.png")
            plot_discharge_profile(st.session_state.river_state.hydro_result, "river_discharge_profile.png")
        except Exception:
            pass

def _trim_tool_messages(msgs):
    """截断过长的工具返回消息，防止上下文爆炸"""
    trimmed = []
    for m in msgs:
        # 兼容 dict 和 ChatCompletionMessage 两种类型
        if isinstance(m, dict):
            role = m.get("role", "")
            content = m.get("content", "")
        else:
            role = getattr(m, "role", "")
            content = getattr(m, "content", "") or ""
        if role == "tool" and len(content) > 2000:
            if isinstance(m, dict):
                m = m.copy()
                m["content"] = content[:2000] + "\n...(已截断)"
            else:
                m = m.model_copy()
                m.content = content[:2000] + "\n...(已截断)"
        trimmed.append(m)
    return trimmed

def process_user_message(user_text, api_key, amap_key, cloud_token=None, system_prompt=None):
    if system_prompt is None:
        system_prompt = "你是水质监测与污染溯源专家。所有推断必须基于监测数据和GB3838标准。禁止编造坐标。"
    msgs = st.session_state.full_messages.copy()
    if len(msgs)==0 or msgs[0]["role"]!="system": msgs.insert(0,{"role":"system","content":system_prompt})
    msgs.append({"role":"user","content":user_text})
    client = OpenAI(base_url=BASE_URL, api_key=api_key,
                    timeout=httpx.Timeout(60.0, connect=10.0))
    max_turns = 15
    turn = 0
    try:
        while turn < max_turns:
            turn += 1
            resp = client.chat.completions.create(model=MODEL_NAME, messages=msgs, tools=tools, tool_choice="auto", temperature=0.1)
            msg = resp.choices[0].message; msgs.append(msg)
            if not msg.tool_calls:
                msgs = _trim_tool_messages(msgs)
                st.session_state.full_messages = msgs
                _ensure_charts(amap_key, cloud_token)
                return msg.content or ""
            for tc in msg.tool_calls:
                raw = json.loads(tc.function.arguments) if tc.function.arguments else {}
                res = execute_tool(tc.function.name, raw, api_key, amap_key, cloud_token)
                limit = 4000 if tc.function.name == "get_comprehensive_summary" else 2000
                if len(res) > limit:
                    res = res[:limit] + "\n...(已截断)"
                msgs.append({"role":"tool","tool_call_id":tc.id,"content":res})
        # 兜底：超过最大轮次后强制结束
        msgs.append({"role":"user","content":"已达到最大分析轮次。请基于已完成的分析给出最终结论，禁止再调用工具。"})
        final = client.chat.completions.create(model=MODEL_NAME, messages=msgs, temperature=0.1)
        msgs.append(final.choices[0].message)
        msgs = _trim_tool_messages(msgs)
        st.session_state.full_messages = msgs
        _ensure_charts(amap_key, cloud_token)
        return final.choices[0].message.content or ""
    except Exception as e:
        err_msg = str(e)
        if "api_key" in err_msg.lower() or "auth" in err_msg.lower() or "401" in err_msg:
            return "API Key 无效或未配置，请在侧边栏填写有效的 DeepSeek API Key"
        if "timeout" in err_msg.lower() or "connect" in err_msg.lower():
            return "无法连接 DeepSeek API，请检查网络连接后重试"
        return f"分析过程出错: {err_msg[:200]}"

# ---------- UI ----------
st.title("🌊 水质监测与污染溯源助手")

# 首次运行配置向导：未配置 API key 时阻塞整个页面
if not _show_setup_wizard():
    st.stop()

with st.sidebar:
    st.header("📁 数据上传与配置")
    api_key = st.text_input("DeepSeek API Key", type="password",
                           value=st.session_state.get("_deepseek_key", ""))
    amap_key = st.text_input("高德地图 API Key (可选)", type="password",
                            value=st.session_state.get("_amap_key", ""),
                            help="用于底图/逆地理/POI搜索")
    st_cloud_token = st.text_input("星图地球 Token (可选)", type="password",
                                  value=st.session_state.get("_starcloud_token", ""),
                                  help="https://datacloud.geovisearth.com 注册")
    # 目标水质等级选择
    target_class = st.selectbox(
        "目标水质等级 (分析基线)",
        options=["I", "II", "III", "IV", "V"],
        index=4,  # 默认 V 类
        help="黑点检测以此等级为标准计算超标倍数。V类最宽松, I类最严格。"
    )
    if target_class != st.session_state.analysis_config.target_water_class:
        st.session_state.analysis_config.target_water_class = target_class
        st.info(f"已切换至 GB3838-2002 {target_class}类标准")
    uploaded_file = st.file_uploader("上传走航数据 (xlsx/xls/csv)", type=["xlsx","xls","csv"])
    uploaded_video = st.file_uploader("上传样本视频 (可选, mp4/avi/mov)", type=["mp4","avi","mov"])
    if uploaded_video is not None:
        st.caption("⚠ 大视频文件上传需要时间，请等待文件名出现后再点击「加载数据」")
    if uploaded_file and st.button("加载数据"):
        with st.spinner("加载数据..."):
            msg, ok = load_data(uploaded_file)
        # 失败时用 st.error —— 曾把"未找到时间列"这类错误显示在绿色成功框里
        (st.success if ok else st.error)(msg)
        if uploaded_video:
            with st.spinner("提取视频帧..."):
                msg, ok = extract_video(uploaded_video, interval=5)
            (st.success if ok else st.error)(msg)
    if st.session_state.df is not None:
        st.write(f"✅ 数据：{len(st.session_state.df)} 条"); st.write(f"🎥 视频帧：{len(st.session_state.video_frames)} 帧")
        if len(st.session_state.video_frames) > 0:
            col_cv1, col_cv2 = st.columns(2)
            with col_cv1:
                time_offset = st.number_input("时间偏移 (秒)", value=0, step=10, help="视频第0秒相对第一条监测记录的偏移, 正=视频滞后")
            with col_cv2:
                if st.button("🔬 交叉印证", use_container_width=True):
                    with st.spinner("视频-传感器交叉印证中..."):
                        cv_result = tool_cross_validate_video(time_offset_sec=time_offset)
                        st.session_state.cross_validation_result = json.loads(cv_result)
                        from water_quality_mcp.src.water_quality_mcp.charts import generate_cross_validation_chart, generate_correlation_heatmap
                        from water_quality_mcp.src.water_quality_mcp.state import WaterQualityState
                        ws = WaterQualityState()
                        ws.df = st.session_state.df
                        ws.video_frames = st.session_state.video_frames
                        ws.black_spots = st.session_state.black_spots
                        generate_cross_validation_chart(ws, "cross_validation.png", time_offset)
                        generate_correlation_heatmap(st.session_state.cross_validation_result, "correlation_heatmap.png")
                        _build_cross_validation_caption()
                        _build_correlation_heatmap_caption()
                    st.success("交叉印证完成，查看「分析图表」标签")
                    st.rerun()

tab1, tab2, tab3, tab4 = st.tabs(["💬 智能对话", "📊 分析图表", "🌊 河道模型", "🔬 技术展示"])
with tab1:
    for m in st.session_state.messages:
        if m["role"]=="user": st.chat_message("user").markdown(m["content"])
        elif m["role"]=="assistant": st.chat_message("assistant").markdown(m["content"])
    if st.session_state.df is not None and not st.session_state.analysis_done:
        if st.button("🚀 开始自动分析", type="primary"):
            if not api_key: st.error("请输入API Key")
            else:
                system_prompt = (
                    "你是自动分析Agent。以下工具中有大量相互独立的调用，请在每一轮尽可能并行调用多个不依赖彼此结果的工具，以缩短总耗时。\n\n"
                    "第一轮（同时调用，全部独立）：\n"
                    "  1. get_data_summary\n"
                    "  2. find_black_spots\n"
                    "  4. get_national_station_data\n"
                    "  5. analyze_video\n"
                    "  7. get_satellite_image\n"
                    "  8. generate_time_series_chart\n"
                    "  9. generate_trace_map\n\n"
                    "等第一轮全部返回后，第二轮（同时调用）：\n"
                    "  3. reverse_geocode（用 find_black_spots 聚类中心坐标）\n"
                    "  5.5. cross_validate_video\n"
                    "  6. search_nearby_pollution_sources（用 reverse_geocode 结果也在此轮）\n\n"
                    "注意：如果第二轮中 3 的结果对 6 是必须的，则把 6 放到第三轮单独调用。\n\n"
                    "最后阶段（依次调用）：\n"
                    "  10. get_comprehensive_summary\n"
                    "  10.5. search_treatment_cases\n"
                    "  11. generate_final_report\n\n"
                    "输出要求：\n"
                    "- 为每张生成的图表撰写一句简要说明（时间序列图、溯源地图、交叉印证图），说明图表展示的核心发现。\n"
                    "- 只输出极简结论（不超过五句话），并提示用户查看「分析图表」标签。\n"
                    "- 严禁输出任何思考过程、自我检讨、道歉或工具调用日志。\n"
                    "- 严禁输出 DSML 标签。"
                )
                with st.spinner("Agent 分析中..."):
                    final = process_user_message("请开始自动分析。", api_key, amap_key, st_cloud_token, system_prompt)
                    st.session_state.messages.append({"role":"assistant","content":final})
                    st.session_state.analysis_done = True; st.rerun()
    user_input = st.chat_input("请输入问题...")
    if user_input:
        if not api_key: st.error("请输入API Key")
        elif st.session_state.df is None: st.error("请先上传并加载数据")
        else:
            st.session_state.messages.append({"role":"user","content":user_input}); st.chat_message("user").markdown(user_input)
            with st.spinner("思考中..."):
                answer = process_user_message(user_input, api_key, amap_key, st_cloud_token)
                st.session_state.messages.append({"role":"assistant","content":answer}); st.rerun()

with tab2:
    st.header("📈 分析图表")
    captions = st.session_state.chart_captions
    if os.path.exists("time_series.png"):
        st.subheader("时间序列趋势")
        st.image("time_series.png")
        if captions.get("time_series"):
            st.caption(captions["time_series"])
        with open("time_series.png", "rb") as f:
            st.download_button("下载时间序列图", f, file_name="time_series.png")
    else:
        st.info("尚未生成时间序列图")
    if os.path.exists("trace_map.html"):
        st.subheader("交互式溯源地图")
        if captions.get("trace_map"):
            st.caption(captions["trace_map"])
        col_btn1, col_btn2 = st.columns([1, 4])
        with col_btn1:
            if st.button("🔄 刷新地图", key="refresh_map"):
                if st.session_state.df is not None and st.session_state.black_spots is not None:
                    with st.spinner("重新生成地图..."):
                        tool_generate_trace_map(amap_key, st_cloud_token)
                    st.rerun()
        trace_path = os.path.join(os.path.dirname(__file__), "trace_map.html")
        with open(trace_path, "r", encoding="utf-8") as f:
            html = f.read()
        st.iframe(trace_path, height=600)
        st.download_button("下载交互式地图", html, file_name="trace_map.html", mime="text/html")
    else:
        st.info("尚未生成溯源地图")
    # 交叉印证图表
    st.subheader("视频-传感器交叉印证")
    if os.path.exists("cross_validation.png"):
        st.image("cross_validation.png")
        if captions.get("cross_validation"):
            st.caption(captions["cross_validation"])
        with open("cross_validation.png", "rb") as f:
            st.download_button("下载交叉印证图", f, file_name="cross_validation.png")
    elif len(st.session_state.video_frames) > 0:
        st.info("视频帧已提取，点击按钮运行交叉印证")
        cv_offset = st.number_input("时间偏移 (秒)", value=0, step=10, key="cv_offset_tab2")
        if st.button("🔬 运行交叉印证", key="cv_btn_tab2"):
            with st.spinner("交叉印证中..."):
                cv_result = tool_cross_validate_video(time_offset_sec=cv_offset)
                st.session_state.cross_validation_result = json.loads(cv_result)
                from water_quality_mcp.src.water_quality_mcp.charts import generate_cross_validation_chart as _cv_chart, generate_correlation_heatmap as _cv_heat
                from water_quality_mcp.src.water_quality_mcp.state import WaterQualityState
                ws = WaterQualityState()
                ws.df = st.session_state.df
                ws.video_frames = st.session_state.video_frames
                ws.black_spots = st.session_state.black_spots
                _cv_chart(ws, "cross_validation.png", cv_offset)
                _cv_heat(st.session_state.cross_validation_result, "correlation_heatmap.png")
                _build_cross_validation_caption()
                _build_correlation_heatmap_caption()
            st.success("交叉印证完成")
            st.rerun()
    else:
        st.info("尚未生成交叉印证图 (需先上传视频并运行交叉印证)")
    if os.path.exists("correlation_heatmap.png"):
        st.image("correlation_heatmap.png")
        if captions.get("correlation_heatmap"):
            st.caption(captions["correlation_heatmap"])
        with open("correlation_heatmap.png", "rb") as f:
            st.download_button("下载相关系数热力图", f, file_name="correlation_heatmap.png")

with tab3:
    st.header("🌊 水动力-水质模型")
    st.markdown("基于 Preissmann 隐式格式求解 Saint-Venant 方程 + ADR 水质模型。模拟潮汐影响下污染物输移扩散和生化反应。")

    # ---- 自动检测河道参数 ----
    auto_config = None
    has_monitoring_data = (
        st.session_state.get("df") is not None
        or os.path.exists("_uploaded_data.xlsx")
    )

    use_auto_detect = st.checkbox(
        "🔍 自动检测河道参数 (从走航GPS数据推断长度/宽度/糙率等)",
        value=has_monitoring_data,
        key="river_auto_detect",
        help="从已上传的走航监测数据自动提取河道长度、宽度、糙率、坡度等参数,代替雅瑶水道默认值。",
    )

    if use_auto_detect and has_monitoring_data:
        with st.spinner("正在从监测数据推断河道参数..."):
            try:
                import pandas as pd
                from river_model.parameter_estimator import estimate_config_from_data

                # 获取监测数据
                if st.session_state.get("df") is not None:
                    df = st.session_state.df
                else:
                    df = pd.read_excel("_uploaded_data.xlsx")

                amap_key = st.session_state.get("_amap_key", "")
                cloud_token = st.session_state.get("_cloud_token", "")
                if not cloud_token:
                    cloud_token = os.environ.get("CLOUD_TOKEN", "")

                auto_config = estimate_config_from_data(df, amap_key=amap_key or None, cloud_token=cloud_token or None)

                # 展示检测结果
                st.success(f"✅ 已自动检测: **{auto_config.river_name}** ({auto_config.location})")

                # 参数置信度颜色映射
                def _conf_color(c):
                    if c >= 0.8: return "🟢"
                    elif c >= 0.5: return "🟡"
                    elif c >= 0.3: return "🟠"
                    else: return "🔴"

                sources = auto_config.param_sources
                confs = auto_config.param_confidence
                src_labels = {
                    "gps": "GPS轨迹", "osm": "OSM地图", "satellite": "卫星影像",
                    "regional_defaults": "区域默认值", "known_river_database": "已知河道库",
                    "amap_address": "高德地址", "amap_reverse_geocode": "高德逆地理",
                    "estimated_from_width": "经验公式", "estimated": "经验估算",
                    "default": "默认值", "user": "用户指定",
                }
                with st.expander("📋 检测参数详情", expanded=False):
                    cols_detail = st.columns(3)
                    params_to_show = [
                        ("河道名称", auto_config.river_name, sources.get("river_name", ""), confs.get("river_name", 0)),
                        ("位置", auto_config.location, sources.get("location", ""), confs.get("location", 0)),
                        ("长度 (m)", f"{auto_config.length_m:.0f}", sources.get("length_m", ""), confs.get("length_m", 0)),
                        ("平均宽度 (m)", f"{auto_config.width_avg_m:.1f}", sources.get("width_avg_m", ""), confs.get("width_avg_m", 0)),
                        ("平均水深 (m)", f"{auto_config.depth_avg_m:.1f}", sources.get("depth_avg_m", ""), confs.get("depth_avg_m", 0)),
                        ("曼宁糙率", f"{auto_config.mannings_n:.3f}", sources.get("mannings_n", ""), confs.get("mannings_n", 0)),
                        ("纵向坡降", f"{auto_config.longitudinal_slope:.6f}", sources.get("longitudinal_slope", ""), confs.get("longitudinal_slope", 0)),
                        ("平均流量 (m³/s)", f"{auto_config.avg_discharge_m3s:.1f}", sources.get("avg_discharge_m3s", ""), confs.get("avg_discharge_m3s", 0)),
                        ("潮差 (m)", f"{auto_config.tidal_range_m:.1f}", sources.get("tidal_range_m", ""), confs.get("tidal_range_m", 0)),
                    ]
                    for i, (name, val, src, conf) in enumerate(params_to_show):
                        with cols_detail[i % 3]:
                            src_label = src_labels.get(src, src)
                            st.caption(f"{_conf_color(conf)} {name}: **{val}** ({src_label})")

            except Exception as e:
                st.warning(f"自动检测失败: {e}. 将使用默认参数。")
                auto_config = None

    # 使用自动检测值或默认值设定滑块初始值
    default_n = auto_config.mannings_n if auto_config else 0.028
    default_flow = auto_config.avg_discharge_m3s if auto_config else 6.9
    default_tide = (auto_config.tidal_range_m / 2.0) if auto_config else 0.8

    col_r1, col_r2 = st.columns(2)
    with col_r1:
        n_cs = st.slider("断面数", 20, 100, 50, 10, key="river_ncs")
        n_val = st.slider("曼宁糙率", 0.020, 0.040, default_n, 0.001, key="river_n")
    with col_r2:
        flow_val = st.slider("上游流量 (m³/s)", 1.0, 150.0, default_flow, 1.0, key="river_flow")
        tide_amp = st.slider("潮汐振幅 (m)", 0.0, 1.5, default_tide, 0.05, key="river_tide")

    if st.button("🚀 运行河道模型", type="primary", use_container_width=True):
        if not api_key:
            st.error("请先在侧边栏输入 DeepSeek API Key")
        else:
            with st.spinner("模型计算中..."):
                from river_model.tools import (
                    tool_river_init, tool_river_hydro_simulation, tool_river_pollution_event,
                )
                # 使用自动检测或传统模式初始化
                if use_auto_detect and has_monitoring_data:
                    import pandas as pd
                    if st.session_state.get("df") is not None:
                        ad_df = st.session_state.df
                    else:
                        ad_df = pd.read_excel("_uploaded_data.xlsx")
                    init_msg = tool_river_init(
                        st.session_state.river_state,
                        n_cross_sections=n_cs,
                        mannings_n=n_val,
                        auto_detect=True,
                        auto_detect_data=ad_df,
                        amap_key=st.session_state.get("_amap_key", ""),
                        cloud_token=st.session_state.get("_cloud_token", os.environ.get("CLOUD_TOKEN", "")),
                    )
                else:
                    init_msg = tool_river_init(
                        st.session_state.river_state, n_cross_sections=n_cs, mannings_n=n_val
                    )
                st.success("模型初始化完成")

                hydro_msg = tool_river_hydro_simulation(
                    st.session_state.river_state,
                    upstream_flow_m3s=flow_val,
                    tidal_amplitude_m=tide_amp,
                    duration_h=24.0,
                )
                st.success("水动力模拟完成")

                poll_msg = tool_river_pollution_event(
                    st.session_state.river_state,
                    chainage_m=1600.0,
                    pollutant_type="ammonia",
                    load_kg=50.0,
                    duration_min=30.0,
                    simulation_hours=24.0,
                    monitoring_data=st.session_state.get('df'),
                )
                st.success("污染事件模拟完成")

                st.markdown("### 模拟结果摘要")
                st.json(json.loads(hydro_msg))
                st.json(json.loads(poll_msg))

    # ---- 溯源动画生成 ----
    st.markdown("---")
    st.markdown("### 🔍 污染溯源动画")
    st.caption("基于实测数据定位污染源工厂 → 非恒定流模拟 → 生成扩散动画")

    col_anim1, col_anim2 = st.columns(2)
    with col_anim1:
        anim_source_chainage = st.number_input(
            "污染源桩号 (m, 0=自动)", value=0.0, step=100.0, key="anim_src_ch"
        )
    with col_anim2:
        anim_load = st.number_input(
            "排放量 (kg, 0=自动估算)", value=0.0, step=50.0, key="anim_load"
        )

    if st.button("🎬 生成污染溯源动画", type="secondary", use_container_width=True):
        if not os.path.exists("_uploaded_data.xlsx"):
            st.warning("请先在侧边栏上传并加载走航数据，再生成溯源动画")
        else:
            with st.spinner("正在生成溯源动画 (约需 30 秒)..."):
                from build_animation import build_trace_animation

                def progress(msg):
                    pass  # Streamlit 的 spinner 已提供视觉反馈

                try:
                    out_path = build_trace_animation(
                        excel_path="_uploaded_data.xlsx",
                        output_path=os.path.join(os.path.dirname(__file__), "river_pollution_animation.html"),
                        progress_callback=progress,
                    )
                    # 缓存到 session_state, 避免切换标签后丢失
                    if os.path.exists(out_path):
                        with open(out_path, "r", encoding="utf-8") as f:
                            st.session_state.anim_html = f.read()
                        st.session_state.anim_file_size = os.path.getsize(out_path)
                    _build_river_animation_caption(out_path)
                    st.success(f"动画已生成: {out_path}")
                except Exception as e:
                    st.error(f"生成失败: {e}")

    # 显示已生成的动画
    anim_file = os.path.join(os.path.dirname(__file__), "river_pollution_animation.html")
    if os.path.exists(anim_file):
        st.markdown("---")
        captions = st.session_state.chart_captions
        if captions.get("river_animation"):
            st.caption(captions["river_animation"])
        st.caption("💡 切换标签后动画会自动恢复到上次播放位置")
        # 优先使用缓存的 HTML, 文件变更时自动刷新
        current_size = os.path.getsize(anim_file)
        if st.session_state.anim_html is None or current_size != st.session_state.anim_file_size:
            with open(anim_file, "r", encoding="utf-8") as f:
                st.session_state.anim_html = f.read()
            st.session_state.anim_file_size = current_size
        st.iframe(anim_file, height=700)
        st.caption(f"文件: {anim_file} ({current_size/1024:.0f} KB)")
        with open(anim_file, "rb") as f:
            st.download_button("📥 下载动画", f, file_name=anim_file, mime="text/html")

    # 显示已生成的河道模型图表
    river_files = [
        ("river_hydro_profile.png", "沿程水位剖面"),
        ("river_discharge_profile.png", "沿程流量剖面"),
        ("river_wq_contour.png", "浓度时空分布"),
        ("river_wq_profile.png", "浓度沿程剖面"),
        ("river_wq_timeseries.png", "浓度过程线"),
        ("river_dashboard.png", "综合仪表盘"),
    ]
    existing = [(f, t) for f, t in river_files if os.path.exists(f)]
    if existing:
        st.markdown("---")
        st.markdown("### 已生成的河道模型图表")
        rs = st.session_state.river_state
        for fname, title in existing:
            st.markdown(f"**{title}**")
            st.image(fname)
            _render_river_chart_caption(fname, title, rs)

# ===== 技术展示 Tab =====

# ---------- 技术展示辅助函数 ----------

def _tech_data_status():
    """渲染数据状态横幅"""
    df = st.session_state.df
    video = st.session_state.video_frames
    black = st.session_state.black_spots
    parts = []
    if df is not None and len(df) > 0:
        dt_str = ""
        if 'datetime' in df.columns:
            t_min, t_max = df['datetime'].min(), df['datetime'].max()
            dt_str = f" ({t_min} ~ {t_max})"
        parts.append(f"✅ 走航数据已加载: {len(df)} 条记录{dt_str}")
    else:
        parts.append("⚠️ 走航数据未加载")
    if len(video) > 0:
        parts.append(f"✅ 视频已加载: {len(video)} 帧")
    else:
        parts.append("📹 视频未上传")
    if black is not None and len(black) > 0:
        parts.append(f"✅ 黑臭检测已完成: {len(black)} 个黑点")
    else:
        parts.append("🔍 黑臭检测未运行")
    target = st.session_state.analysis_config.target_water_class
    parts.append(f"🎯 目标水质: {target}类")
    cols = st.columns(len(parts))
    for i, p in enumerate(parts):
        with cols[i]:
            st.caption(p)


def _display_national_station_result(result_str):
    """解析并展示国控站点查询结果"""
    try:
        data = json.loads(result_str) if isinstance(result_str, str) else result_str
    except Exception:
        st.text(str(result_str)[:2000])
        return

    if not isinstance(data, dict):
        st.text(str(data)[:2000])
        return

    # 显示数据来源信息
    source = data.get("数据来源", "")
    freshness = data.get("数据时效", "")
    if source:
        st.caption(f"📡 {source}")
    if freshness:
        st.caption(f"🕐 {freshness}")

    stations = data.get("站点列表", [])
    has_live = data.get("含实时监测值", False)

    if not stations:
        st.info(data.get("状态", "未找到匹配站点，请尝试其他省份或关键词"))
        return

    st.metric("匹配站点数", len(stations))

    if has_live:
        # 实时模式 — 含监测值
        rows = []
        for s in stations[:20]:
            rows.append({
                "站名": s.get("站名", ""),
                "省份": s.get("省份", ""),
                "流域": s.get("流域", ""),
                "水质类别": s.get("水质类别", ""),
                "pH": s.get("pH", ""),
                "溶解氧": s.get("溶解氧", ""),
                "氨氮": s.get("氨氮", ""),
                "总磷": s.get("总磷", ""),
                "总氮": s.get("总氮", ""),
                "浊度": s.get("浊度", ""),
                "水温": s.get("水温", ""),
                "监测时间": s.get("时间", ""),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        # 离线模式 — 仅站点基本信息 + 坐标
        rows = []
        for s in stations[:20]:
            rows.append({
                "站名": s.get("站名", ""),
                "省份": s.get("省份", ""),
                "流域": s.get("流域", ""),
                "城市": s.get("城市", ""),
                "经度": s.get("lon", ""),
                "纬度": s.get("lat", ""),
                "水质目标": s.get("水质目标", ""),
                "距离(km)": s.get("距离_km", ""),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
        st.info("当前为离线模式。若需实时监测值（pH/DO/氨氮等），请使用 `live=True` 并确保网络可访问 szzdjc.cnemc.cn")

    if len(stations) > 20:
        st.caption(f"（仅显示前 20 个站点，共 {len(stations)} 个）")


def _display_black_spot_result(result_str):
    """解析并展示黑臭检测结果"""
    try:
        data = json.loads(result_str) if isinstance(result_str, str) else result_str
    except Exception:
        st.text(str(result_str)[:2000])
        return

    if isinstance(data, dict) and "error" in data:
        st.warning(data["error"])
        return

    black = st.session_state.black_spots

    if data.get("黑点总数", 0) == 0:
        # 无黑点 — 好结果
        target_class = data.get("判定标准", {}).get("污染指数", "")
        st.success(f"✅ 未检测到黑臭点位 — 该水体当前达标（{target_class}）")
        if data.get("提示"):
            st.caption(data["提示"])
        return

    # 从 JSON 提取统计信息
    total = data.get("黑点总数", 0)
    sev_dist = data.get("黑臭等级分布", {})
    n_clusters = data.get("聚类热点数", 0)
    n_extra = data.get("时空增强识别的额外黑点", 0)
    nh3_peak = data.get("氨氮峰值(mg/L)", "")
    do_min = data.get("溶解氧最低(mg/L)", "")
    max_score = data.get("增强评分最高", "")

    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("黑点总数", total)
    with c2: st.metric("聚类热点数", n_clusters)
    with c3: st.metric("氨氮峰值(mg/L)", nh3_peak)
    with c4: st.metric("溶解氧最低(mg/L)", do_min)

    c1a, c2a, c3a = st.columns(3)
    with c1a: st.metric("最高增强评分", max_score)
    with c2a: st.metric("时空增强额外发现", n_extra, help="传统判定为非黑臭但时空上下文显示异常的额外黑点数")

    # 黑臭等级分布
    if sev_dist:
        st.markdown("**黑臭等级分布**")
        st.bar_chart(sev_dist)

    # 判定标准说明
    standards = data.get("判定标准", {})
    if standards:
        pi_std = standards.get("污染指数", "")
        bo_std = standards.get("黑臭判定", "")
        st.caption(f"判定标准: {pi_std} | {bo_std}")

    # Top-5 最严重污染点位
    if black is not None and len(black) > 0:
        st.markdown("**Top-5 最严重污染点位**")
        avail_cols = []
        for c in ["datetime", "ammonia_nitrogen", "dissolved_oxygen", "turbidity", "ph",
                   "cod", "black_score", "severity_label", "cluster_id"]:
            if c in black.columns:
                avail_cols.append(c)
        sort_col = "black_score" if "black_score" in black.columns else None
        if sort_col:
            top5 = black.nlargest(5, sort_col)[avail_cols]
        else:
            top5 = black.head(5)[avail_cols]
        st.dataframe(top5, use_container_width=True, hide_index=True)

    # 聚类详情
    # 键名以 analysis.get_cluster_summary 的实际产出为准:
    # cluster_id / count / 内梅罗等级 (曾误写为 聚类ID / 点位数量 / 严重程度,
    # .get() 取不到时静默返回空串, 面板上三个值恒为空白)
    clusters_info = data.get("聚类详情", [])
    if clusters_info:
        with st.expander("聚类详情"):
            for ci in clusters_info:
                st.markdown(f"**聚类 {ci.get('cluster_id', '')}** — "
                           f"污染等级: {ci.get('内梅罗等级', '')}, "
                           f"黑臭等级: {ci.get('黑臭等级', '')}, "
                           f"沿航迹指数: {ci.get('沿航迹指数变化', ci.get('趋势', '未判定'))}")
                st.caption(f"点位: {ci.get('count', '')} 个 | "
                          f"空间跨度: {ci.get('空间跨度_m', '')}m | "
                          f"首末观测跨度: {ci.get('首末观测跨度_min', '')}min")


def _display_trace_result(result_str):
    """解析并展示多渠道溯源结果"""
    try:
        data = json.loads(result_str) if isinstance(result_str, str) else result_str
    except Exception:
        st.text(str(result_str)[:2000])
        return

    if not isinstance(data, dict):
        st.text(str(data)[:2000])
        return

    if "状态" in data:
        st.info(data.get("状态", ""))
        st.caption(data.get("建议", ""))
        return

    # 各数据源贡献
    source_contrib = data.get("各数据源贡献", {})
    if source_contrib:
        st.markdown("**各数据源贡献**")
        cols_src = st.columns(len(source_contrib))
        for i, (src_name, count) in enumerate(source_contrib.items()):
            with cols_src[i]:
                st.metric(src_name, count)

    # 综合污染源列表
    sources = data.get("综合污染源列表", [])
    search_center = data.get("搜索中心", "")
    radius = data.get("搜索半径_m", "")
    if search_center:
        st.caption(f"搜索中心: {search_center} | 半径: {radius}m")

    if not sources:
        st.info("未发现登记在册的污染源，建议扩大搜索范围或人工排查。")
        return

    st.metric("发现疑似污染源", len(sources))
    rows = []
    for src in sources[:20]:
        rows.append({
            "排名": src.get("排名", ""),
            "名称": src.get("名称", ""),
            "数据来源": src.get("数据源", ""),
            "权重得分": round(float(src.get("置信度权重", 0)), 3),
            "距离(m)": src.get("距离", src.get("distance", "")),
            "地址": src.get("地址", src.get("address", "")),
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    # 数据源说明
    desc = data.get("数据源说明", {})
    if desc:
        with st.expander("数据来源说明"):
            for src, explain in desc.items():
                st.caption(f"• **{src}**: {explain}")

    if len(sources) > 20:
        st.caption(f"（仅显示前 20 个，共 {len(sources)} 个污染源）")


def _display_rag_results(result_str):
    """解析并展示 RAG 检索结果"""
    try:
        data = json.loads(result_str) if isinstance(result_str, str) else result_str
    except Exception:
        st.text(str(result_str)[:3000])
        return

    # 处理错误/状态消息
    if isinstance(data, dict) and "状态" in data and "检索结果" not in data:
        status = data.get("状态", "")
        suggestion = data.get("建议", "")
        error = data.get("错误", "")
        if error:
            st.error(f"{status}\n\n{error}\n\n{suggestion}")
        else:
            st.warning(f"{status}\n\n{suggestion}")
        return

    # search_treatment_cases 返回格式: {"检索结果": [...], "共检索到": N, "查询": "..."}
    if isinstance(data, dict) and "检索结果" in data:
        cases = data["检索结果"]
        st.metric("检索到相似案例", len(cases))
        if data.get("查询"):
            st.caption(f"查询: {data['查询']}")
        if len(cases) == 0:
            st.info("知识库中暂无匹配案例，请尝试更宽泛的关键词或检查索引是否已构建。")
            return
        for i, case in enumerate(cases):
            title = case.get("标题", f"案例 {i+1}")
            location = case.get("地点", "")
            severity = case.get("污染程度", "")
            methods = case.get("治理方法", "")
            score = case.get("相似度", 0)
            summary = case.get("内容摘要", "")
            with st.container(border=True):
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.markdown(f"**{title}**")
                    if location:
                        st.caption(f"📍 {location}")
                    if severity:
                        st.caption(f"🏷️ {severity}")
                with c2:
                    pct = float(score) if isinstance(score, (int, float)) else 0
                    st.metric("相似度", f"{pct:.0%}" if pct <= 1 else f"{pct:.1f}")
                if methods:
                    st.caption(f"治理方法: {methods}")
                if summary:
                    with st.expander("查看摘要"):
                        st.markdown(str(summary))
        return

    # 兼容旧格式（直接列表）
    if isinstance(data, list):
        if len(data) == 0:
            st.info("知识库中暂无匹配案例，请尝试更宽泛的关键词或检查索引是否已构建。")
            return
        st.metric("检索到相似案例", len(data))
        for i, case in enumerate(data):
            title = case.get("title", case.get("案例标题", f"案例 {i+1}"))
            location = case.get("location", case.get("地点", ""))
            severity = case.get("severity", case.get("严重程度", ""))
            methods = case.get("treatment_methods", case.get("治理方法", []))
            score = case.get("score", case.get("相似度", 0))
            content = case.get("content", case.get("摘要", ""))
            with st.container(border=True):
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.markdown(f"**{title}**")
                    if location:
                        st.caption(f"📍 {location}")
                    if severity:
                        st.caption(f"🏷️ {severity}")
                with c2:
                    pct = float(score) if isinstance(score, (int, float)) else 0
                    st.metric("相似度", f"{pct:.0%}" if pct <= 1 else f"{pct:.1f}")
                if methods:
                    if isinstance(methods, list):
                        st.caption("治理方法: " + " · ".join(methods))
                    else:
                        st.caption(f"治理方法: {methods}")
                if content:
                    with st.expander("查看摘要"):
                        st.markdown(str(content)[:500])
        return

    if isinstance(data, dict):
        if "error" in data or "状态" in data:
            st.warning(data.get("error", data.get("状态", str(data))))
        else:
            st.json(data)
    else:
        st.text(str(data)[:3000])


def _display_river_params(auto_config):
    """渲染河道参数自动检测结果"""
    # 参数来源中文映射
    src_labels_map = {
        "gps": "GPS轨迹", "osm": "OSM地图", "satellite": "卫星影像",
        "regional_defaults": "区域默认值", "known_river_database": "已知河道库",
        "amap_address": "高德地址", "amap_reverse_geocode": "高德逆地理",
        "estimated_from_width": "经验公式", "estimated": "经验估算",
        "default": "默认值", "user": "用户指定",
    }
    def _conf_color(c):
        if c >= 0.8: return "🟢"
        elif c >= 0.5: return "🟡"
        elif c >= 0.3: return "🟠"
        else: return "🔴"

    sources = auto_config.param_sources
    confs = auto_config.param_confidence
    st.success(f"✅ 检测到河道: **{auto_config.river_name}** ({auto_config.location})")
    st.metric("河道全长", f"{auto_config.length_m:.0f} m")
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("平均宽度", f"{auto_config.width_avg_m:.1f} m")
    with c2: st.metric("平均水深", f"{auto_config.depth_avg_m:.1f} m")
    with c3: st.metric("曼宁糙率", f"{auto_config.mannings_n:.3f}")
    with c4: st.metric("平均流量", f"{auto_config.avg_discharge_m3s:.1f} m³/s")

    with st.expander("参数详情与置信度"):
        params_to_show = [
            ("河道名称", auto_config.river_name, sources.get("river_name", ""), confs.get("river_name", 0)),
            ("位置", auto_config.location, sources.get("location", ""), confs.get("location", 0)),
            ("长度 (m)", f"{auto_config.length_m:.0f}", sources.get("length_m", ""), confs.get("length_m", 0)),
            ("平均宽度 (m)", f"{auto_config.width_avg_m:.1f}", sources.get("width_avg_m", ""), confs.get("width_avg_m", 0)),
            ("平均水深 (m)", f"{auto_config.depth_avg_m:.1f}", sources.get("depth_avg_m", ""), confs.get("depth_avg_m", 0)),
            ("曼宁糙率", f"{auto_config.mannings_n:.3f}", sources.get("mannings_n", ""), confs.get("mannings_n", 0)),
            ("纵向坡降", f"{auto_config.longitudinal_slope:.6f}", sources.get("longitudinal_slope", ""), confs.get("longitudinal_slope", 0)),
            ("平均流量 (m³/s)", f"{auto_config.avg_discharge_m3s:.1f}", sources.get("avg_discharge_m3s", ""), confs.get("avg_discharge_m3s", 0)),
            ("潮差 (m)", f"{auto_config.tidal_range_m:.1f}", sources.get("tidal_range_m", ""), confs.get("tidal_range_m", 0)),
        ]
        cols_detail = st.columns(3)
        for i, (name, val, src, conf) in enumerate(params_to_show):
            with cols_detail[i % 3]:
                src_label = src_labels_map.get(src, src)
                st.caption(f"{_conf_color(conf)} {name}: **{val}** ({src_label})")

# ---------- Tab 4: 技术展示 ----------

with tab4:
    st.header("🔬 核心技术能力展示")
    st.markdown(
        "以下展示了本系统的 6 项核心技术能力。每项技术均可**一键运行**，无需通过对话提问。"
    )
    _tech_data_status()

    # ============ 第 1 区：国控水质站点实时查询 ============
    with st.expander("📡 国控水质站点实时查询", expanded=False):
        st.markdown("""
        **技术原理**：本系统接入国家地表水水质自动监测实时数据发布系统（生态环境部 szzdjc.cnemc.cn），
        覆盖全国 **1800+** 个自动监测断面，数据**每小时更新**。通过逆向工程 RealDatas.js 接口，
        直接 HTTP POST 获取 JSON 实时数据（pH / 溶解氧 / 氨氮 / 总磷 / 总氮 / 浊度等），
        无需浏览器或 Playwright。内置 95+ 站点离线坐标数据库作为备用。
        """)
        st.caption("💡 无需上传数据即可体验")
        col_ns1, col_ns2 = st.columns(2)
        with col_ns1:
            tech_province = st.selectbox("省份", [
                "广东省", "江苏省", "浙江省", "湖北省", "湖南省", "四川省",
                "山东省", "河南省", "河北省", "北京市", "上海市", "重庆市",
                "福建省", "安徽省", "江西省", "广西壮族自治区", "海南省",
                "云南省", "贵州省", "陕西省", "山西省", "甘肃省", "辽宁省",
                "吉林省", "黑龙江省", "内蒙古自治区", "西藏自治区",
                "宁夏回族自治区", "青海省", "新疆维吾尔自治区", "天津市",
            ], key="tech_ns_province")
        with col_ns2:
            tech_keyword = st.text_input("站名/流域关键词 (可选)", placeholder="如: 珠江、西江", key="tech_ns_keyword")
        if st.button("📡 查询实时数据", type="primary", key="tech_ns_btn"):
            with st.spinner("正在查询国家监测平台实时数据..."):
                result = tool_get_national_station_data(province=tech_province, keyword=tech_keyword, live=True)
                st.session_state.tech_ns_result = result
        if st.session_state.get("tech_ns_result"):
            _display_national_station_result(st.session_state.tech_ns_result)

    # ============ 第 2 区：多维黑臭点智能检测 ============
    with st.expander("🔍 多维黑臭点智能检测", expanded=False):
        st.markdown("""
        **技术原理**：采用 **4 维增强评分算法** 检测水污染热点：

        1. **内梅罗综合指数** — 综合评估多污染物超标情况
        2. **严重度因子** — 超标倍数越大，权重越高（>3× → 1.5×，>5× → 2.0×，>10× → 3.0×）
        3. **时间持续性因子** — 30 分钟滑动窗口，孤立单点超标被抑制，持续性超标被放大
        4. **空间连续性因子** — 基于 Haversine 距离的成片超标检测，漂移单点被降权

        然后用 **DBSCAN 空间聚类**（eps≈33m）识别热点区域，最终按《城市黑臭水体整治工作指南》(2015)
        和 **GB3838-2002** 分级为轻度/重度黑臭。

        核心创新：传统方法只看超标倍数，本系统同时考察**时-空-严重度**三维上下文，避免误报。
        """)
        tech_has_data = st.session_state.df is not None and len(st.session_state.df) > 0
        if not tech_has_data:
            st.warning("⚠️ 请先在侧边栏上传走航监测数据并点击「加载数据」")
        config = st.session_state.analysis_config
        st.caption(f"当前目标水质: **{config.target_water_class}类** "
                   f"(氨氮≤{config.get_target_limits()['nh3']}mg/L, "
                   f"DO≥{config.get_target_limits()['do']}mg/L, "
                   f"COD≤{config.get_target_limits()['cod']}mg/L) — 可在侧边栏切换")
        if st.button("🔍 运行黑臭检测", type="primary", key="tech_black_btn", disabled=not tech_has_data):
            with st.spinner("正在运行 4 维增强黑臭检测 + DBSCAN 聚类..."):
                result = tool_find_black_spots()
                st.session_state.tech_black_result = result
        if st.session_state.get("tech_black_result"):
            _display_black_spot_result(st.session_state.tech_black_result)

    # ============ 第 3 区：多渠道污染溯源 ============
    with st.expander("🎯 多渠道污染溯源", expanded=False):
        st.markdown("""
        **技术原理**：整合 **4 大数据源** 进行根因溯源，加权融合去重：

        | 数据源 | 权重 | 说明 |
        |--------|:----:|------|
        | 全国排污许可证管理信息平台 | 0.35 | 环保部官方数据库 (permit.mee.gov.cn) |
        | 高德地图 POI | 0.25 | 搜索工厂/养殖场/排污口/化工厂等 |
        | 国控水质站点 | 0.25 | 国家地表水自动监测网络 |
        | 重点排污单位名录 | 0.15 | 地方生态环境局年度公开数据 |

        各数据源独立查询、交叉验证，按置信度加权排序后去重输出 Top-20 疑似污染源。
        """)
        tech_has_black = (st.session_state.black_spots is not None and len(st.session_state.black_spots) > 0)
        if not tech_has_data:
            st.warning("⚠️ 请先上传并加载数据")
        elif not tech_has_black:
            st.info("💡 建议先运行「多维黑臭点智能检测」以定位搜索中心。也可手动输入坐标。")
        col_tr1, col_tr2 = st.columns(2)
        with col_tr1:
            # 尝试从黑点聚类推断坐标
            default_lat = 23.11; default_lon = 113.14
            black = st.session_state.black_spots
            if black is not None and len(black) > 0 and 'cluster_id' in black.columns:
                best_id = black['cluster_id'].value_counts().idxmax()
                best_cluster = black[black['cluster_id'] == best_id]
                default_lat = round(float(best_cluster['latitude'].mean()), 6)
                default_lon = round(float(best_cluster['longitude'].mean()), 6)
            tech_trace_lat = st.number_input("搜索中心纬度", value=default_lat, format="%.6f", key="tech_trace_lat")
        with col_tr2:
            tech_trace_lon = st.number_input("搜索中心经度", value=default_lon, format="%.6f", key="tech_trace_lon")
        tech_trace_radius = st.slider("搜索半径 (m)", 200, 3000, 500, 100, key="tech_trace_radius")
        if st.button("🎯 运行多渠道溯源", type="primary", key="tech_trace_btn", disabled=not tech_has_data):
            with st.spinner("正在整合 4 大数据源进行污染溯源..."):
                amap_key = st.session_state.get("_amap_key", "")
                config = st.session_state.analysis_config
                # 临时修改 search_radius
                orig_radius = config.search_radius_m
                config.search_radius_m = tech_trace_radius
                try:
                    from water_quality_mcp.src.water_quality_mcp.trace_engine import multi_source_trace
                    result = multi_source_trace(tech_trace_lat, tech_trace_lon, amap_key or None, config)
                    st.session_state.tech_trace_result = result
                finally:
                    config.search_radius_m = orig_radius
        if st.session_state.get("tech_trace_result"):
            _display_trace_result(st.session_state.tech_trace_result)

    # ============ 第 4 区：视频-传感器交叉印证 ============
    with st.expander("🎥 视频-传感器交叉印证", expanded=False):
        st.markdown("""
        **技术原理**：将走航视频的**计算机视觉特征**与传感器实测数据进行**多模态交叉印证**：

        1. **视频端**：逐帧提取 Laplacian 方差（水质清澈度）、HSV 色彩空间特征（黑臭颜色判断）
        2. **传感器端**：浊度、氨氮、溶解氧、COD 等实测指标
        3. **对齐**：通过时间偏移 + 滑动窗口将视频帧与监测记录对齐
        4. **分析**：计算 Pearson 相关系数矩阵 + 黑臭判定一致性矩阵（一致率/准确率/召回率）

        量化 "人眼看到的" 与 "仪器测到的" 之间的一致性，为视频辅助水质判断提供科学依据。
        """)
        tech_has_video = len(st.session_state.video_frames) > 0
        if not tech_has_data:
            st.warning("⚠️ 请先上传并加载数据")
        elif not tech_has_video:
            st.warning("📹 请先在侧边栏上传视频文件 (MP4/AVI/MOV) 并点击「加载数据」")
        col_cv1, col_cv2 = st.columns(2)
        with col_cv1:
            tech_cv_offset = st.number_input("时间偏移 (秒)", value=0, step=10, key="tech_cv_offset",
                                             help="视频第 0 秒相对第一条监测记录的时间偏移。正值=视频滞后。")
        with col_cv2:
            tech_cv_window = st.number_input("匹配窗口 (秒)", value=30, step=10, key="tech_cv_window",
                                             help="取窗口内监测数据均值与视频帧匹配。")
        if st.button("🔬 运行交叉印证", type="primary", key="tech_cv_btn",
                     disabled=not (tech_has_data and tech_has_video)):
            with st.spinner("正在运行视频-传感器多模态交叉印证..."):
                cv_result = tool_cross_validate_video(
                    time_offset_sec=tech_cv_offset, time_window_sec=tech_cv_window)
                st.session_state.tech_cv_result = cv_result
                # 生成图表
                from water_quality_mcp.src.water_quality_mcp.charts import (
                    generate_cross_validation_chart as _cv_chart,
                    generate_correlation_heatmap as _cv_heat,
                )
                from water_quality_mcp.src.water_quality_mcp.state import WaterQualityState
                ws = WaterQualityState()
                ws.df = st.session_state.df
                ws.video_frames = st.session_state.video_frames
                ws.black_spots = st.session_state.black_spots
                _cv_chart(ws, "cross_validation.png", tech_cv_offset)
                _cv_heat(st.session_state.cross_validation_result, "correlation_heatmap.png")
                _build_cross_validation_caption()
                _build_correlation_heatmap_caption()
        if st.session_state.get("tech_cv_result"):
            try:
                cv_data = json.loads(st.session_state.tech_cv_result) if isinstance(
                    st.session_state.tech_cv_result, str) else st.session_state.tech_cv_result
            except Exception:
                cv_data = {}
            # 显示关键相关系数
            # 键名以 cross_validate_video_with_monitoring 的实际产出为准。
            # 此处原读 cv_data["pearson"] —— 后端从未产出过该键,
            # 因此指标区一直不渲染。后端实际给出的是 "相关系数矩阵" (list),
            # 每项含 视频指标 / 传感器指标 / Pearson_r / 显著性 / 样本数。
            corr_list = cv_data.get("相关系数矩阵", [])
            if isinstance(corr_list, list) and corr_list:
                top = [c for c in corr_list if isinstance(c, dict)][:4]
                cols_cv = st.columns(min(len(top), 4))
                for i, c in enumerate(top):
                    with cols_cv[i % 4]:
                        label = f"{c.get('视频指标', '?')} × {c.get('传感器指标', '?')}"
                        r = c.get("Pearson_r")
                        delta = c.get("显著性") if c.get("显著性") != "不显著" else None
                        st.metric(label, f"{r:.3f}" if isinstance(r, (int, float)) else "—",
                                  delta=delta)
            if os.path.exists("cross_validation.png"):
                st.image("cross_validation.png")
                captions = st.session_state.chart_captions
                if captions.get("cross_validation"):
                    st.caption(captions["cross_validation"])
            if os.path.exists("correlation_heatmap.png"):
                st.image("correlation_heatmap.png")
                if captions.get("correlation_heatmap"):
                    st.caption(captions["correlation_heatmap"])

    # ============ 第 5 区：RAG 知识库智能检索 ============
    with st.expander("📚 RAG 知识库智能检索", expanded=False):
        st.markdown("""
        **技术原理**：基于 **RAG（检索增强生成）** 架构的水环境治理方案智能检索系统：

        - **知识库**：29 个真实治理案例，覆盖黑臭水体、氨氮治理、富营养化、综合治理 4 大类
        - **向量化**：使用 `shibing624/text2vec-base-chinese` 模型（768 维中文语义向量）
        - **检索引擎**：ChromaDB 向量数据库，语义相似度匹配
        - **检索策略**：从分析结果中自动提取污染物类型、严重程度、水体类型等特征，构建结构化查询

        在生成治理建议时，系统会自动检索最相似的历史案例，为 LLM 提供参考，避免"幻觉"。
        """)
        st.caption("💡 无需上传数据即可体验")

        # ---- 路径计算 ----
        _project_root = os.path.dirname(os.path.abspath(__file__))
        _rag_dir = os.path.join(_project_root, ".rag_index")
        _sqlite_path = os.path.join(_rag_dir, "chroma.sqlite3")
        _kb_dir = os.path.join(_project_root, "knowledge_base")

        # ---- RAG 可用性检测（文件系统优先 → ChromaDB 二次确认） ----
        tech_rag_available = os.path.exists(_sqlite_path) and os.path.getsize(_sqlite_path) > 0

        if not tech_rag_available:
            # ----- 索引文件不存在，提供一键构建 -----
            # 确认知识库目录是否有源文档
            _kb_has_docs = os.path.isdir(_kb_dir) and any(
                f.endswith(".md") and not os.path.basename(f).startswith("_")
                and os.path.basename(f) != "README.md"
                for f in [os.path.join(dp, fn) for dp, _, fns in os.walk(_kb_dir) for fn in fns]
            ) if os.path.isdir(_kb_dir) else False

            if _kb_has_docs:
                st.warning("⚠️ RAG 知识库索引尚未构建（源文档已就绪，可一键构建）")
                if st.button("🔧 一键构建索引", type="primary", key="tech_rag_build",
                             help=f"从 {_kb_dir} 加载所有案例文档 → 生成向量索引"):
                    with st.spinner("🔄 正在构建 RAG 知识库索引（首次需下载嵌入模型 ~400MB，请耐心等待）..."):
                        try:
                            from water_quality_mcp.src.water_quality_mcp.knowledge_loader import (
                                KnowledgeBaseManager,
                            )
                            manager = KnowledgeBaseManager(
                                knowledge_dir=_kb_dir,
                                persist_dir=_rag_dir,
                            )
                            docs = manager.load_documents()
                            if not docs:
                                st.error("知识库目录中未找到有效 Markdown 文档（需含 frontmatter `title` 字段）。")
                            else:
                                chunk_count = manager.build_index(reset=True)
                                # 清空 RAG 引擎单例缓存
                                try:
                                    from water_quality_mcp.src.water_quality_mcp.rag_engine import RAGEngine
                                    RAGEngine._instance = None
                                except Exception:
                                    pass
                                st.success(
                                    f"✅ 索引构建完成！\n\n"
                                    f"{len(docs)} 个案例文档 → {chunk_count} 个向量块已入库\n"
                                    f"索引位置: `{_rag_dir}`"
                                )
                                time.sleep(1)
                                st.rerun()
                        except ImportError as e:
                            st.error(
                                f"缺少依赖: `{e}`\n\n"
                                f"请手动安装: `pip install chromadb sentence-transformers`"
                            )
                        except Exception as e:
                            st.error(f"构建失败: {str(e)[:400]}")
                            with st.expander("查看详细错误"):
                                import traceback as _tb
                                st.code(_tb.format_exc())
            else:
                st.error(
                    "⚠️ RAG 知识库索引和源文档均未找到。\n\n"
                    f"预期位置:\n"
                    f"- 索引: `{_rag_dir}/`\n"
                    f"- 源文档: `{_kb_dir}/`\n\n"
                    f"请确保知识库文件存在，或运行:\n"
                    f"`python scripts/build_rag_index.py`"
                )
        else:
            # ----- 索引文件存在 -----
            # 尝试通过 ChromaDB 获取文档数（用于显示状态），失败也不影响使用
            _rag_count = 0
            _rag_chroma_ok = False
            try:
                from water_quality_mcp.src.water_quality_mcp.rag_engine import get_rag_engine
                engine = get_rag_engine()
                engine._collection = None
                engine._chroma_client = None
                _rag_count = engine.collection.count()
                _rag_chroma_ok = True
            except Exception:
                pass

            if _rag_chroma_ok:
                st.success(f"✅ 知识库就绪：{_rag_count} 个向量块可供检索 (索引: `{_rag_dir}`)")
            else:
                st.warning(
                    "⚠️ 检测到索引文件存在，但 ChromaDB 暂时无法连接"
                    "（可能是 SQLite 文件锁冲突）。检索时将自动重试。"
                )

            # 提供重建按钮（折叠在高级选项中）
            with st.expander("🔧 高级：重建索引"):
                st.caption("如果知识库源文档有更新，可在此重建向量索引。")
                if st.button("🔄 重建知识库索引", key="tech_rag_rebuild"):
                    with st.spinner("🔄 正在重建 RAG 知识库索引..."):
                        try:
                            from water_quality_mcp.src.water_quality_mcp.knowledge_loader import (
                                KnowledgeBaseManager,
                            )
                            manager = KnowledgeBaseManager(
                                knowledge_dir=_kb_dir,
                                persist_dir=_rag_dir,
                            )
                            docs = manager.load_documents()
                            if not docs:
                                st.error("知识库目录中未找到有效文档。")
                            else:
                                chunk_count = manager.build_index(reset=True)
                                try:
                                    from water_quality_mcp.src.water_quality_mcp.rag_engine import RAGEngine
                                    RAGEngine._instance = None
                                except Exception:
                                    pass
                                st.success(
                                    f"✅ 重建完成！{len(docs)} 个文档 → {chunk_count} 个向量块"
                                )
                                time.sleep(1)
                                st.rerun()
                        except Exception as e:
                            st.error(f"重建失败: {str(e)[:300]}")

        # ---- 检索界面（当可用时） ----
        col_rg1, col_rg2 = st.columns([3, 1])
        with col_rg1:
            tech_rag_query = st.text_input("检索查询",
                                           placeholder="例如：氨氮超标 黑臭水体 城市河道治理",
                                           key="tech_rag_query")
        with col_rg2:
            tech_rag_topk = st.number_input("返回案例数", 1, 10, 5, key="tech_rag_topk")
        # 快捷查询按钮
        quick_queries = ["黑臭水体治理", "氨氮工业废水处理", "富营养化湖泊修复", "城市河道综合治理"]
        cols_qq = st.columns(len(quick_queries))
        clicked_qq = None
        for i, qq in enumerate(quick_queries):
            with cols_qq[i]:
                if st.button(qq, key=f"tech_rag_qq_{i}"):
                    clicked_qq = qq
        if st.button("📚 检索相似案例", type="primary", key="tech_rag_btn"):
            query = tech_rag_query or "黑臭水体治理"
            with st.spinner(f"正在语义检索: {query}..."):
                result = tool_search_treatment_cases(query, top_k=tech_rag_topk)
                st.session_state.tech_rag_result = result
        # 快捷查询
        if clicked_qq:
            with st.spinner(f"正在语义检索: {clicked_qq}..."):
                result = tool_search_treatment_cases(clicked_qq, top_k=tech_rag_topk)
                st.session_state.tech_rag_result = result
        if st.session_state.get("tech_rag_result"):
            _display_rag_results(st.session_state.tech_rag_result)

    # ============ 第 6 区：河道模型自动参数检测 ============
    with st.expander("🌊 河道模型自动参数检测", expanded=False):
        st.markdown("""
        **技术原理**：无需人工测量河道，系统从走航船的 **GPS 轨迹** 自动推断全套水动力模型参数。
        采用 **6 层自动检测管道**（优先级递减）：

        | 层级 | 数据源 | 推断内容 | 置信度 |
        |:----:|--------|----------|:------:|
        | 0 | 已知河道 GPS 匹配库 | 9 条珠三角已知河道精准参数 | 🟢 0.95 |
        | 1 | OpenStreetMap 水道数据 | 名称/类型/宽度 (Overpass API) | 🟡 0.80-0.90 |
        | 2 | 高德逆地理编码 | 水体名称与位置 | 🟠 0.50-0.70 |
        | 3 | 星图地球卫星影像 | 河道宽度 (边缘检测) | 🔴 实验性 |
        | 4 | 区域默认值查表 | 曼宁糙率/坡降/水深经验公式 | 🟡 0.50-0.70 |
        | 5 | 监测数据统计推断 | 水质基线值/GPS 横向散布 | 🟠 0.30-0.50 |

        每一层可独立降级，即使 OSM 超时或卫星影像不可用，仍能完成参数估计。
        """)
        tech_has_mon_data = (st.session_state.df is not None) or os.path.exists("_uploaded_data.xlsx")
        if not tech_has_mon_data:
            st.warning("⚠️ 请先上传并加载走航数据")
        if st.button("🌊 自动检测河道参数", type="primary", key="tech_river_btn",
                     disabled=not tech_has_mon_data):
            with st.spinner("正在运行 6 层自动检测管道..."):
                try:
                    import pandas as pd
                    from river_model.parameter_estimator import estimate_config_from_data
                    if st.session_state.get("df") is not None:
                        df = st.session_state.df
                    else:
                        df = pd.read_excel("_uploaded_data.xlsx")
                    amap_key = st.session_state.get("_amap_key", "")
                    cloud_token = st.session_state.get("_cloud_token", "")
                    if not cloud_token:
                        cloud_token = os.environ.get("CLOUD_TOKEN", "")
                    auto_config = estimate_config_from_data(
                        df, amap_key=amap_key or None, cloud_token=cloud_token or None)
                    st.session_state.tech_river_config = auto_config
                except Exception as e:
                    st.error(f"自动检测失败: {e}")
                    st.info("将使用雅瑶水道默认参数（可前往「河道模型」标签页手动配置）")
        if st.session_state.get("tech_river_config"):
            _display_river_params(st.session_state.tech_river_config)
            st.caption("💡 检测到的参数可前往「河道模型」标签页直接用于水动力模拟")

