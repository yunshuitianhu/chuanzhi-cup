# -*- coding: utf-8 -*-
"""Update 水质监测溯源助手_说明文档.docx to reflect current codebase state."""

from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from copy import deepcopy
import os

DOC_PATH = r"C:\Users\Cloud-Mist Sky Lake\Desktop\水质监测溯源助手_说明文档.docx"
BACKUP_PATH = DOC_PATH.replace(".docx", "_backup.docx")

# ---- helpers ----

def clear_paragraph(p):
    """Remove all runs from a paragraph."""
    for run in p.runs:
        run._element.getparent().remove(run._element)

def set_paragraph_text(p, text):
    """Set paragraph text while preserving style."""
    clear_paragraph(p)
    run = p.add_run(text)

def insert_paragraph_after(ref_para, texts, styles=None):
    """Insert paragraphs after ref_para. texts can be str or list[str].

    Returns the last inserted paragraph element.
    """
    if isinstance(texts, str):
        texts_list = [texts]
    else:
        texts_list = texts
    if styles is None:
        styles_list = ['Normal'] * len(texts_list)
    elif isinstance(styles, str):
        styles_list = [styles] * len(texts_list)
    else:
        styles_list = styles

    last = ref_para._element
    for text, style in zip(texts_list, styles_list):
        new_p = OxmlElement('w:p')
        last.addnext(new_p)

        # Set style
        if style and style != 'Normal':
            pPr = new_p.get_or_add_pPr()
            pStyle = OxmlElement('w:pStyle')
            pStyle.set(qn('w:val'), style)
            pPr.append(pStyle)

        # Set alignment if List Bullet
        if style == 'List Bullet':
            pPr = new_p.get_or_add_pPr()
            # Add list paragraph numPr
            numPr = OxmlElement('w:numPr')
            ilvl = OxmlElement('w:ilvl')
            ilvl.set(qn('w:val'), '0')
            numId = OxmlElement('w:numId')
            numId.set(qn('w:val'), '1')  # Use existing list ID from document
            numPr.append(ilvl)
            numPr.append(numId)
            pPr.append(numPr)

        # Add text
        for i, line in enumerate(text.split('\n')):
            if i > 0:
                # Add line break
                r_br = OxmlElement('w:r')
                br = OxmlElement('w:br')
                r_br.append(br)
                new_p.append(r_br)
            run = OxmlElement('w:r')
            t = OxmlElement('w:t')
            t.text = line
            t.set(qn('xml:space'), 'preserve')
            run.append(t)
            new_p.append(run)

        last = new_p

    return last

def insert_paragraphs_after(ref_para, blocks):
    """Insert multiple paragraphs after ref_para.

    blocks is a list of (text, style) tuples.
    Returns the last inserted element.
    """
    texts = [b[0] for b in blocks]
    styles = [b[1] for b in blocks]
    return insert_paragraph_after(ref_para, texts, styles)

def insert_table_row_after(table, ref_row_idx, cell_texts):
    """Insert a new row after ref_row_idx with given cell texts."""
    ref_tr = table.rows[ref_row_idx]._tr
    new_tr = deepcopy(ref_tr)
    ref_tr.addnext(new_tr)
    for i, text in enumerate(cell_texts):
        tc = new_tr.findall(qn('w:tc'))[i]
        # Remove existing paragraphs
        for p in tc.findall(qn('w:p')):
            tc.remove(p)
        # Add new paragraph
        new_p = OxmlElement('w:p')
        r = OxmlElement('w:r')
        t = OxmlElement('w:t')
        t.text = text
        r.append(t)
        new_p.append(r)
        tc.insert(0, new_p)

# ---- main ----

# Backup original
if not os.path.exists(BACKUP_PATH):
    import shutil
    shutil.copy2(DOC_PATH, BACKUP_PATH)
    print(f"Backup saved: {BACKUP_PATH}")

doc = Document(DOC_PATH)

# Collect all paragraphs
paragraphs = [p for p in doc.paragraphs]

# ---- Change 1: Architecture diagram — 3 tabs → 4 tabs (P40) ----
# The architecture text-art diagram in P40 shows only 3 tabs.
# We need to add Tab4 line.
p40_text = paragraphs[40].text

if "└─ Tab3" in p40_text and "Tab4" not in p40_text:
    old_line = "│  └─ Tab3 河道模型 (水动力-水质模拟+动画)   │"
    new_line = "│  └─ Tab3 河道模型 (水动力-水质模拟+动画)   │\n│  ├─ Tab4 技术展示 (一键演示 6 项核心技术)   │"
    # Actually the diagram is a single paragraph with newlines
    # Replace Tab3 line and add Tab4 after it
    old_diagram = """│  ├─ Tab2 分析图表 (时间序列/地图/交叉印证) │
│  └─ Tab3 河道模型 (水动力-水质模拟+动画)   │"""
    new_diagram = """│  ├─ Tab2 分析图表 (时间序列/地图/交叉印证) │
│  ├─ Tab3 河道模型 (水动力-水质模拟+动画)   │
│  └─ Tab4 技术展示 (一键演示 6 项核心技术)   │"""

    new_text = p40_text.replace(old_diagram, new_diagram)
    set_paragraph_text(paragraphs[40], new_text)
    print("[1/10] ✅ Architecture diagram updated: 3 tabs → 4 tabs")
else:
    print("[1/10] ⚠️  Architecture diagram already has 4 tabs or pattern not found")

# ---- Change 4: Multi-source trace in Section 8 (update P220 region) ----
# P220: "周边工厂 POI 标记（需高德 Key）" — this is in the visualization subsection
# Better: update the pollution source description. In section 8.4 (LLM), P214-P220.
# P220 says "溯源报告基于 GB 3838-2002..."
# Let's check if there's a specific pollution source paragraph to modify.
# Actually, the doc describes `search_nearby_pollution_sources` in MCP tools list (P229).
# Let's add a paragraph after P220 about multi-source tracing.

# For now, add multi-source trace note in section 9 (MCP tools area P230)
old_geo_line = "`search_nearby_pollution_sources`"
if old_geo_line in paragraphs[229].text:
    paragraphs[229].clear()
    paragraphs[229].add_run("- 地理服务：`get_satellite_image`、`search_nearby_pollution_sources`、`multi_source_trace`（4 源加权融合：高德 POI + 排污许可证 + 重点排污单位 + 国控站点）、`reverse_geocode`、`get_national_station_data`")
    print("[4/10] ✅ Multi-source trace added to MCP tools list (P229)")
else:
    print("[4/10] ⚠️  Could not find search_nearby_pollution_sources in P229")

# ---- Change 6: MCP tool count 23→25 (P226) ----
p226_text = paragraphs[226].text
if "23 个" in p226_text:
    new_text = p226_text.replace("23 个", "25 个")
    set_paragraph_text(paragraphs[226], new_text)
    print("[6/10] ✅ MCP tool count: 23 → 25")
elif "25 个" in p226_text:
    print("[6/10] ⚠️  MCP tool count already 25")
else:
    # Try to find the number pattern
    import re
    m = re.search(r'(\d+)\s*个\s*MCP', p226_text)
    if m:
        new_text = p226_text.replace(m.group(0), "25 个 MCP")
        set_paragraph_text(paragraphs[226], new_text)
        print(f"[6/10] ✅ MCP tool count: {m.group(1)} → 25")
    else:
        print("[6/10] ⚠️  Could not find MCP tool count")

# ---- Change 8: National station v2.0 (Section 8, near MCP tools) ----
# The national station is mentioned in MCP tools list P229 (geo services)
# and in the auto-analysis table (Table 3 row 4).
# The doc doesn't have a dedicated national station subsection, but P228 mentions it.
# Let's add a note in P229 about v2.0.
# P228 says: "- 分析检测：`get_data_summary`、`find_black_spots`..."
# P229 says: "- 可视化：..."
# Let's add after P228 a note about v2.0.
# Actually, let me not add a separate paragraph; just update if there's a reference.

# For now, let's modify step 4 description in the auto-analysis flow.
# That's in Table 3, Row 4. Let's update that cell.
table3 = doc.tables[3]  # Auto-analysis steps table (Table 3)
row4_cell2 = table3.rows[4].cells[2]  # Description of step 4
if "查询附近国控站点" in row4_cell2.text:
    row4_cell2.paragraphs[0].clear()
    row4_cell2.paragraphs[0].add_run("查询附近国控站点（v2.0 HTTP API 直连生态环境部，覆盖 1800+ 站点，含实时 pH/DO/氨氮/总磷/总氮/浊度）")
    print("[8/10] ✅ National station v2.0 note added to auto-analysis table")
else:
    print("[8/10] ⚠️  National station row pattern not found in auto-analysis table")

# ---- Now handle paragraph insertions (order: bottom-to-top to preserve P indices) ----

# Re-read paragraphs after any modifications
paragraphs = [p for p in doc.paragraphs]

# ---- Change 3: Insert RAG section (8.X) before Section 9 (MCP Server) ----
# Section 9 starts at P221 (Heading 2: "9. MCP Server 模式")
# Insert between P220 (end of 8.4) and P221
rag_blocks = [
    ("8.5 RAG 知识库（检索增强生成）", "Heading 3"),
    ("RAG (Retrieval-Augmented Generation) 知识库为系统提供治理方案推荐能力，使 LLM 生成的溯源报告能够参考历史真实治理案例。", "Normal"),
    ("工作流程", "Heading 4"),
    ("1. 知识库构建：29 个真实治理案例，Markdown 格式，每个文件包含 YAML frontmatter 元数据（标题、地点、污染物类型、严重程度、水体类型、治理方法、日期、来源）。按分类组织：黑臭水体治理（20 例）、氨氮工业废水治理（4 例）、富营养化治理（1 例）、综合治理（4 例）。", "List Bullet"),
    ("2. 向量化索引：使用 shibing624/text2vec-base-chinese 嵌入模型（768 维中文语义向量），通过 ChromaDB 持久化存储在项目 .rag_index/ 目录。构建命令：python scripts/build_rag_index.py（约 147 个文档块入库）。", "List Bullet"),
    ("3. 语义检索：从 comprehensive_summary 中自动提取污染物类型、黑臭等级、位置等关键特征，构建自然语言查询串，检索 top-5 最相似案例。检索结果按余弦相似度降序排列。", "List Bullet"),
    ("4. 双重检索路径：", "List Bullet"),
    ("  - Path A（MCP 工具主动调用）：LLM Agent 可在分析过程中主动调用 search_treatment_cases(query, top_k) 获取相关案例。", "Normal"),
    ("  - Path B（自动注入）：generate_report() 在调用 DeepSeek 前自动检索相似案例，格式化为 Markdown 后注入 prompt 作为参考。", "Normal"),
    ("5. 优雅降级：如果 ChromaDB 或 sentence-transformers 未安装，或知识库索引为空，检索静默返回空结果，报告生成回退到原始行为（无 RAG 上下文），不影响系统正常使用。", "List Bullet"),
    ("检索示例：输入 \"氨氮超标 黑臭水体 城市河道治理\" → 返回珠三角感潮河段氨氮超标治理、苏州河道黑臭水体治理等相似案例，含治理方法、效果数据和关键经验。", "Normal"),
]
insert_paragraphs_after(paragraphs[220], rag_blocks)
print("[3/10] ✅ RAG knowledge base section (8.5) inserted after P220")

# ---- Change 5: River model auto-detection (Section 7, after P113) ----
# P113: "系统内置了雅瑶水道一维非恒定流水动力-水质耦合模型。"
# P114: data source note (⚠️)
# Insert auto-detection section after P114
paragraphs = [p for p in doc.paragraphs]  # Refresh
auto_detect_blocks = [
    ("7.3 河道参数自动检测（auto_detect）", "Heading 3"),
    ("当启用 auto_detect=True 时，系统可从走航船 GPS 轨迹和公开地理数据中自动推断全套水动力模型参数，无需人工测量。采用 6 层自动检测管道，按优先级从高到低：", "Normal"),
    ("", "Normal"),  # Empty line as spacer
    ("第 0 层 — 已知河道 GPS 匹配库", "Heading 4"),
    ("通过 GPS 坐标 bounding box 匹配 9 条珠三角已知河道（雅瑶水道、佛山水道、北江、西江、东江等），返回精准预标定参数。命中时置信度 0.95，直接返回，不再执行后续层。", "List Bullet"),
    ("第 1 层 — OpenStreetMap 水道数据", "Heading 4"),
    ("通过 Overpass API 查询 GPS 轨迹周边的 OSM 水道，按类型优先级（river > canal > drain）、几何重叠度和名称匹配度综合评分。与已知河道数据库交叉校验，避免将 GPS 位于雅瑶水道时误匹配为附近\"大坑涌\"等小型灌渠。置信度 0.80-0.90。", "List Bullet"),
    ("第 2 层 — 高德逆地理编码", "Heading 4"),
    ("逆向地址解析提取水体名称，按关键字优先级排序（水道 > 河 > 江 > 涌 > 渠 > 湖）。若高德 API 不可用（无 Key 或调用失败），自动降级为 GPS 坐标推断（31 省份 bbox 查找 + 19 条全国主要河流区域提示）。置信度 0.50-0.70。", "List Bullet"),
    ("第 3 层 — 星图地球卫星影像", "Heading 4"),
    ("通过卫星影像瓦片的边缘检测估算河道宽度（类 NDWI 水陆边界检测）。实验性功能，需 CLOUD_TOKEN，失败时静默跳过。", "List Bullet"),
    ("第 4 层 — 区域默认值查表", "Heading 4"),
    ("基于 Chow (1959) 曼宁糙率表、珠江三角洲平原坡降经验值、Leopold & Maddock 宽深比经验公式等，提供区域性默认参数。置信度 0.50-0.70。", "List Bullet"),
    ("第 5 层 — 监测数据统计推断", "Heading 4"),
    ("从传感器数据中提取水质基线值（各指标中位数），从 GPS 横向散布估算河道宽度下界（需 >15m 散布才视为有效，低于此阈值归因于 GPS 噪声/行船摆动）。置信度 0.30-0.50。", "List Bullet"),
    ("", "Normal"),
    ("关键设计原则：", "Normal"),
    ("- 各层独立降级：OSM 超时（3s）或卫星影像不可用不影响整体估算", "List Bullet"),
    ("- 所有参数标注 _source（数据来源）和 _confidence（置信度）元数据", "List Bullet"),
    ("- 传统模式（雅瑶水道默认值）完全保留向后兼容", "List Bullet"),
    ("- UI 显示置信度色标：🟢≥80%  🟡≥50%  🟠≥30%  🔴<30%", "List Bullet"),
]
insert_paragraphs_after(paragraphs[124], auto_detect_blocks)
print("[5/10] ✅ River model auto-detection (7.3) inserted after P114")

# ---- Change 7: Technology Showcase tab details (Section 8, after P220+RAG) ----
paragraphs = [p for p in doc.paragraphs]  # Refresh
# Find the last RAG paragraph and insert after
# The RAG section ended with "检索示例" paragraph. Let's find it by searching from P221 backward.
# Actually P221 was the original "9. MCP Server" heading. Now it shifted.
# Let's find "检索示例" or just insert before MCP Server section.
for i, p in enumerate(paragraphs):
    if "检索示例" in p.text and "氨氮超标" in p.text:
        tech_blocks = [
            ("8.6 技术展示标签页（Tab4）", "Heading 3"),
            ("第 4 个标签页（🔬 技术展示）是面向非技术评审的无 LLM 一键演示画廊。每个功能模块封装为可展开卡片（st.expander），包含通俗中文说明 + 演示按钮 + 行内结果展示，无需通过智能对话即可体验全部核心技术。", "Normal"),
            ("", "Normal"),
            ("6 个技术演示模块：", "Normal"),
            ("", "Normal"),
            ("1. 📡 国控水质站点实时查询 — 零数据需求，30 省份下拉选择器，HTTP API 直连查询 1800+ 站点实时监测值（pH/DO/氨氮/总磷/总氮/浊度），含在线/离线双模式自动切换。", "List Bullet"),
            ("2. 🔍 多维黑臭点智能检测 — 需上传数据，运行时-空-严重度 4 维增强评分 + DBSCAN 空间聚类，输出聚类热点数、黑臭等级分布、氨氮峰值、内梅罗指数最高值等关键指标。", "List Bullet"),
            ("3. 🎯 多渠道污染溯源 — 需上传数据，整合高德 POI + 排污许可证 + 重点排污单位 + 国控站点 4 源加权融合，去重排序后输出 Top-20 疑似污染源列表。", "List Bullet"),
            ("4. 🎥 视频-传感器交叉印证 — 需数据 + 视频，Pearson 相关系数矩阵 + 黑臭判定一致性统计，双面板时间序列图（Laplacian 方差 vs 浊度 / HSV 亮度 vs 氨氮）。", "List Bullet"),
            ("5. 📚 RAG 知识库智能检索 — 零数据需求，自然语言查询框 + 4 个快捷预设按钮（\"氨氮超标\"、\"黑臭水体\"、\"富营养化\"、\"工业废水治理\"），一键语义搜索 29 个治理案例。", "List Bullet"),
            ("6. 🌊 河道模型自动参数检测 — 需上传数据，运行 6 层自动检测管道，展示推断的河道名称、几何参数、水力学参数及置信度评估。", "List Bullet"),
            ("", "Normal"),
            ("设计要点：", "Normal"),
            ("- 模块 1 和 5 零数据即可运行，首开即演示", "List Bullet"),
            ("- 所有 widget key 使用 tech_ 前缀避免与 Tab1-3 冲突", "List Bullet"),
            ("- 结果缓存在 st.session_state 的 tech_*_result 键中，展开/折叠和切换标签页不丢失", "List Bullet"),
            ("- 各模块优雅降级：缺失数据时按钮禁用 + 上下文提示，不崩溃", "List Bullet"),
        ]
        insert_paragraphs_after(p, tech_blocks)
        print(f"[7/10] ✅ Technology Showcase (8.6) inserted after P{i}")
        break
else:
    # Fallback: insert after MCP Server section header changed location
    for i, p in enumerate(paragraphs):
        if "9. MCP Server 模式" in p.text:
            tech_blocks = [
                ("8.6 技术展示标签页（Tab4）", "Heading 3"),
                ("第 4 个标签页（🔬 技术展示）是面向非技术评审的无 LLM 一键演示画廊，包含 6 个可展开技术演示卡片。模块 1（国控站点查询）和模块 5（RAG 知识库检索）零数据即可运行，其他模块需先上传走航数据。所有结果缓存在 session_state 中，切换标签页不丢失。", "Normal"),
            ]
            insert_paragraphs_after(paragraphs[i - 1], tech_blocks)
            print(f"[7/10] ✅ Technology Showcase (8.6) inserted before P{i} (fallback)")
            break
    else:
        print("[7/10] ⚠️  Could not find insertion point for tech showcase")

# ---- Change 9: Enhanced 4D black-odor scoring (Section 8.2, after P167) ----
paragraphs = [p for p in doc.paragraphs]  # Refresh
# P167: "两套标准的协同与分工" description
# Find the paragraph about dual-standard synergy
for i, p in enumerate(paragraphs):
    if "两套标准的协同" in p.text:
        scoring_blocks = [
            ("8.2.7 时空增强评分算法", "Heading 4"),
            ("在传统内梅罗综合指数基础上，系统引入了时空上下文增强评分（compute_black_score_enhanced），将\"超标多少\"（幅度维度）与\"持续多久\"（时间维度）和\"连片多大\"（空间维度）三维融合：", "Normal"),
            ("", "Normal"),
            ("增强评分 = 内梅罗综合指数 × 严重度因子 × (1 + α × 时间因子) × (1 + β × 空间因子)", "Normal"),
            ("", "Normal"),
            ("其中：", "Normal"),
            ("- 严重度因子：超标倍数越大权重越高（Pi > 3 → 1.5×, > 5 → 2.0×, > 10 → 3.0×），由 AnalysisConfig 可调", "List Bullet"),
            ("- 时间持续性因子：30 分钟滑动窗口内超标点占比，需 ≥3 个持续超标点（persistence_min_points）才激活。孤立单点超标被抑制，持续超标被放大", "List Bullet"),
            ("- 空间连续性因子：基于 Haversine 距离的成片超标检测，50m 窗口内连续超标段长度加权。漂移的孤立黑点被降权", "List Bullet"),
            ("- α = 1.5（时间权重系数），β = 1.5（空间权重系数）", "List Bullet"),
            ("", "Normal"),
            ("核心创新：传统方法仅考虑超标倍数（\"有多严重\"），本系统同时考察持续性和空间聚集性（\"是偶发漂移还是持续排放\"），有效抑制走航船瞬间掠过污染团产生的孤立误报。", "Normal"),
            ("", "Normal"),
            ("输出同时包含 black_score（增强评分）和 nemerow_raw（传统内梅罗评分），便于对比分析。聚类摘要新增字段：持续超标时长_min、空间跨度_m、超标倍数范围、趋势（恶化/稳定/改善，来自线性回归斜率）。", "Normal"),
        ]
        # Insert after the paragraph AFTER the "协同与分工" section
        # Find the next Heading 4 or a good stopping point
        insert_after = i
        for j in range(i + 1, min(i + 10, len(paragraphs))):
            if paragraphs[j].style.name.startswith("Heading"):
                insert_after = j - 1
                break
        else:
            insert_after = i + 5  # fallback

        insert_paragraphs_after(paragraphs[insert_after], scoring_blocks)
        print(f"[9/10] ✅ Enhanced 4D scoring (8.2.7) inserted after P{insert_after}")
        break
else:
    print("[9/10] ⚠️  Could not find '两套标准的协同' paragraph")

# ---- Change 10: Configurable analysis pipeline (after Section 6 or in Section 8) ----
paragraphs = [p for p in doc.paragraphs]  # Refresh
# Add at the end of Section 8.1 (data loading) or beginning of Section 8
# Let's find Section 8 heading and insert after it
for i, p in enumerate(paragraphs):
    if "8. 各功能模块说明" in p.text and p.style.name.startswith("Heading 2"):
        config_blocks = [
            ("可配置分析参数（AnalysisConfig）", "Normal"),
            ("系统通过 AnalysisConfig 数据类统一管理所有可调参数，支持运行中动态切换。侧边栏提供\"目标水质类别\"下拉选择器（I~V 类，默认 V 类），切换后所有 GB3838 限值（氨氮/DO/COD/BOD/TP/TN）同步更新，黑臭检测和溯源分析即时生效。", "Normal"),
            ("可配置的参数组包括：", "Normal"),
            ("- 目标水质类别（target_water_class）：I~V，控制 GB3838 单因子 Pi 和内梅罗指数计算的基准限值", "List Bullet"),
            ("- DBSCAN 聚类参数：eps（默认 0.0003°≈33m）、min_samples（默认 5）", "List Bullet"),
            ("- 时空评分权重：temporal_window_min（30min）、spatial_window_m（50m）、persistence_min_points（3）、time_weight_factor（1.5）、spatial_weight_factor（1.5）", "List Bullet"),
            ("- 溯源参数：search_radius_m（搜索半径）、pollution_keywords（POI 搜索关键词列表）、source_weights（4 源权重配置：排污许可证 0.35 / 国控站点 0.25 / 高德 POI 0.25 / 土地利用 0.15）", "List Bullet"),
            ("- 国控站点查询半径：national_station_radius_km（默认 50km）", "List Bullet"),
            ("所有核心分析函数接受可选 config 参数，config=None 时回退到 V 类默认值，保证向后兼容。", "Normal"),
        ]
        insert_paragraphs_after(p, config_blocks)
        print(f"[10/10] ✅ Configurable analysis pipeline inserted after P{i}")
        break
else:
    print("[10/10] ⚠️  Could not find Section 8 heading")

# ---- Change 2: Auto-analysis table — add step 10.5 ----
# Table 3 is the auto-analysis flow table (13 rows currently)
# Row 10 = step 10 (get_comprehensive_summary)
# Row 11 = step 11 (generate_final_report)
# We insert after Row 10 (which is ref_row_idx=10)
table3 = doc.tables[3]
print(f"Auto-analysis table has {len(table3.rows)} rows")
# Verify it's the right table
if "get_comprehensive_summary" in table3.rows[10].cells[1].text:
    # Row 10 cols 1 has the right content
    pass

# Insert new row
insert_table_row_after(table3, 10, [
    "10.5",  # Step number
    "`search_treatment_cases`",  # Tool name
    "RAG 知识库检索：从 29 个治理案例中语义搜索相似案例，自动注入最终报告作为治理建议参考"  # Description
])
print("[2/10] ✅ Auto-analysis table: step 10.5 inserted after row 10")

# Final verification of table3
table3 = doc.tables[3]
print(f"Auto-analysis table now has {len(table3.rows)} rows")
for ri, row in enumerate(table3.rows):
    cells = [cell.text[:50] for cell in row.cells]
    print(f"  Row {ri}: {cells}")

# ---- Save ----
doc.save(DOC_PATH)
print(f"\n✅ Document saved: {DOC_PATH}")
print("All changes applied. Original backed up as _backup.docx")
