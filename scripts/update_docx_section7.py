# -*- coding: utf-8 -*-
"""Update Section 7 (river model) to reflect auto-detection capabilities."""

from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from copy import deepcopy
import os, sys

# Fix encoding
sys.stdout.reconfigure(encoding='utf-8')

DOC_PATH = r"C:\Users\Cloud-Mist Sky Lake\Desktop\水质监测溯源助手_说明文档.docx"

doc = Document(DOC_PATH)
paragraphs = [p for p in doc.paragraphs]

# Helper functions
def clear_paragraph(p):
    for run in p.runs:
        run._element.getparent().remove(run._element)

def set_paragraph_text(p, text):
    clear_paragraph(p)
    run = p.add_run(text)

def set_paragraph_style(p, style_name):
    """Set paragraph style via OOXML."""
    p.style = doc.styles[style_name]

# ---- Step 1: Update P113 (intro paragraph) ----
# Old: "系统内置了雅瑶水道一维非恒定流水动力-水质耦合模型。"
# New: mention dual modes
new_intro = (
    "系统内置了一维非恒定流水动力-水质耦合模型，支持两种运行模式："
    "（1）传统模式——使用雅瑶水道预标定默认参数，直接运行；"
    "（2）自动检测模式——从走航船 GPS 轨迹和公开地理数据中自动推断全套河道参数（名称、几何、水力学、水质基线），无需人工测量，支持全国任意河道。"
)
set_paragraph_text(paragraphs[113], new_intro)
print("[1/6] P113 intro updated to describe dual modes")

# ---- Step 2: Update P114 (data source note) ----
# Mention auto-detection as data source for non-Yayao rivers
new_note = (
    "⚠️ 数据来源说明：传统模式下，河道几何参数（断面形态、河长 3.2 km、平均河宽 55 m、平均水深 2.0 m）、水力学参数（曼宁糙率 0.028、设计流量 138.26 m³/s、平均流量 6.9 m³/s）、潮汐边界条件（潮差 1.6 m、M2 分潮周期 12.42 h、平均高潮位 0.83 m / 低潮位 -0.52 m）均来自雅瑶水道实地勘察和水文年鉴。自动检测模式下，参数源自 6 层数据管道：已知河道 GPS 匹配库（9 条珠三角河道）→ OpenStreetMap Overpass API → 高德逆地理编码 → 星图地球卫星影像 → 区域默认值查表（Chow 1959 糙率表等）→ 监测数据统计推断。所有参数均标注数据来源和置信度元数据。"
)
set_paragraph_text(paragraphs[114], new_note)
print("[2/6] P114 data source note updated")

# ---- Step 3: Fix stray P146-P147 (ADR simulation + charts) ----
# These are leftovers from the original 7.1 that got separated when 7.3 was inserted.
# P146 says "- 污染事件模拟...", P147 says "生成的图表包括..."
# These should be part of the 7.1 run flow, placed after P124
# P124 is the last step before 7.3 starts: "模型将依次执行："
# Actually P124 says "模型将依次执行：" followed by:
# P124 [List Bullet] "- 水动力模拟（Preissmann 隐式格式求解 Saint-Venant 方程）"
# The stray P146 has: "- 污染事件模拟（ADR 对流-扩散-反应模型，默认氨氮泄漏 50 kg）"
# So P146 should follow P124 as the second bullet point

# Convert the stray P146-P147 to be following P124
# P146 content: "- 污染事件模拟（ADR 对流-扩散-反应模型，默认氨氮泄漏 50 kg）"
# P147 content: "生成的图表包括：沿程水位剖面、流量剖面、浓度时空分布、浓度沿程剖面、浓度过程线、综合仪表盘。"

# Instead of moving, let's rewrite them as clean paragraphs after P124
# First, clear P146 and P147 (they'll be rewritten as part of the flow)
# Actually, let's just update their positions using XML

# Get the content
p146_text = paragraphs[146].text
p147_text = paragraphs[147].text

# Clean P146 and P147 (we'll move them)
# The simplest approach: Insert new paragraphs after P124 with the right content,
# then delete P146-P147

# Insert after P124
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

def create_paragraph(text, style_name='List Bullet'):
    """Create a new paragraph OOXML element."""
    new_p = OxmlElement('w:p')

    # Set style
    if style_name and style_name != 'Normal':
        pPr = OxmlElement('w:pPr')
        pStyle = OxmlElement('w:pStyle')
        pStyle.set(qn('w:val'), style_name)
        pPr.append(pStyle)
        new_p.append(pPr)

    # Add text
    run = OxmlElement('w:r')
    t = OxmlElement('w:t')
    t.text = text
    t.set(qn('xml:space'), 'preserve')
    run.append(t)
    new_p.append(run)

    return new_p

# Insert pollution simulation bullet and chart list after P124
ref = paragraphs[124]._element
for text, style in [
    (p146_text, 'List Bullet'),   # "- 污染事件模拟..."
    (p147_text, 'Normal'),        # "生成的图表包括：..."
]:
    new_p = create_paragraph(text, style)
    ref.addnext(new_p)
    ref = new_p

# Now delete the original stray paragraphs (P146 and P147)
# These have shifted due to our insertions
paragraphs = [p for p in doc.paragraphs]  # Refresh
# Find the stray paragraphs - they now have shifted indices
# Original P146 had "- 污染事件模拟"
for i, p in enumerate(paragraphs):
    if "污染事件模拟（ADR" in p.text and i > 130:
        # Remove this paragraph
        p._element.getparent().remove(p._element)
        break

paragraphs = [p for p in doc.paragraphs]
for i, p in enumerate(paragraphs):
    if "生成的图表包括：沿程水位剖面" in p.text and i > 130:
        p._element.getparent().remove(p._element)
        break

print("[3/6] Stray paragraphs P146-P147 moved into 7.1 flow")

# ---- Step 4: Fix section numbering ----
# Current: 7.1 → 7.3 (auto-detect) → 7.2 (animation)
# Target: 7.1 → 7.2 (auto-detect) → 7.3 (animation)
paragraphs = [p for p in doc.paragraphs]

# Find and rename
for i, p in enumerate(paragraphs):
    text = p.text.strip()
    style = p.style.name if p.style else ''

    if text == "7.3 河道参数自动检测（auto_detect）" and style.startswith('Heading 3'):
        set_paragraph_text(p, "7.2 河道参数自动检测（auto_detect）")
        print(f"  Renamed P{i}: 7.3 → 7.2 (auto-detection)")

    if text == "7.2 生成污染溯源动画" and style.startswith('Heading 3'):
        set_paragraph_text(p, "7.3 生成污染溯源动画")
        print(f"  Renamed P{i}: 7.2 → 7.3 (animation)")

print("[4/6] Section numbering fixed: 7.1 → 7.2 → 7.3")

# ---- Step 5: Update 7.1 description to mention the auto-detect checkbox ----
paragraphs = [p for p in doc.paragraphs]

# P116 is "切换到 '🌊 河道模型' 标签页：" section
# Let's update to mention the auto-detect option
for i, p in enumerate(paragraphs):
    if "切换到" in p.text and "河道模型" in p.text and "标签页" in p.text:
        # Add mention of auto-detect checkbox
        set_paragraph_text(p, "切换到 \"🌊 河道模型\" 标签页，可选择两种运行方式：")

        # Insert auto-detect description paragraphs after
        ref_el = p._element
        for text, style in [
            ("", "Normal"),
            ("传统模式：勾选\"使用雅瑶水道默认参数\"（默认），使用预置的雅瑶水道几何与水力学参数直接运行，适合快速演示。", "List Bullet"),
            ("自动检测模式：取消勾选\"使用默认参数\"（即启用 auto_detect），系统从已上传的走航数据中自动推断河道参数。6 层检测管道依次运行：已知河道库 GPS 匹配 → OSM 水道查询 → 高德逆地理 → 卫星影像宽度估计 → 区域默认值查表 → 监测数据推断。每层失败时自动降级，确保始终返回可用参数。", "List Bullet"),
            ("", "Normal"),
            ("传统模式参数设置（使用默认值时）：", "Normal"),
        ]:
            new_p = create_paragraph(text, style)
            ref_el.addnext(new_p)
            ref_el = new_p
        break

# Renumber: the old P117 "1. 调整模型参数（使用默认值也可）：" should become part of traditional mode
# It's fine as-is, just update its context

print("[5/6] 7.1 updated to describe both modes with auto-detect option")

# ---- Step 6: Expand auto-detection section with geographic coverage and Known River DB details ----
paragraphs = [p for p in doc.paragraphs]

# After the existing "第 0 层 — 已知河道 GPS 匹配库" paragraph, add details about the 9 rivers
for i, p in enumerate(paragraphs):
    if "第 0 层 — 已知河道 GPS 匹配库" in p.text and p.style.name.startswith('Heading 4'):
        # The next paragraph (P129) describes this layer. Update it with more detail.
        next_p = paragraphs[i + 1]
        # Don't change it, add a new paragraph after it with the river list
        known_river_text = (
            "已知河道数据库覆盖珠三角 9 条主要水道：雅瑶水道（佛山市南海区，河宽 55m，糙率 0.028）、"
            "北村水道、佛山水道（河宽 80m）、西南涌、珠江广州段（河宽 400m）、北江（河宽 300m）、"
            "西江（河宽 500m）、东江（河宽 200m）、潭江（河宽 150m）。每条河道均预标定了完整的水力参数"
            "（曼宁糙率、纵向坡降、平均流量、潮汐振幅）和 GPS 边界框。"
            "GPS 轨迹中心点落入某河道 bbox 时，直接返回该河道全部参数（置信度 0.95），跳过后续所有层。"
            "bbox 大小从 0.001°×0.001°（雅瑶水道，约 100m×100m）到 2°×1.5°（西江）不等，"
            "小 bbox 优先匹配以确保精度。"
        )
        new_p = create_paragraph(known_river_text, 'Normal')
        next_p._element.addnext(new_p)
        print(f"  Added known river DB details after P{i+1}")
        break

# Also update the OSM layer description to mention the cross-check mechanism more clearly
paragraphs = [p for p in doc.paragraphs]
for i, p in enumerate(paragraphs):
    if "第 1 层 — OpenStreetMap 水道数据" in p.text and p.style.name.startswith('Heading 4'):
        next_p = paragraphs[i + 1]
        osm_detail = (
            "OSM 在中国覆盖有限——雅瑶水道等小型水道通常未在 OSM 中命名，"
            "附近可能出现\"大坑涌\"等小型灌渠名称。为此，系统采用 GPS 轨迹几何重叠度评分"
            "（type_priority × overlap_score × name_score × length_score）选择最佳匹配水道，"
            "并与已知河道库交叉校验：若 OSM 返回小型涌名但 GPS 坐标落入已知大河道的 bbox，"
            "则以已知河道库名称为准（如雅瑶水道）。OSM 查询通过 Overpass API（免费、无需 Key），"
            "结果缓存 1 小时，超时 3 秒自动降级。"
        )
        new_p = create_paragraph(osm_detail, 'Normal')
        next_p._element.addnext(new_p)
        print(f"  Added OSM cross-check details after P{i+1}")
        break

print("[6/6] Auto-detection details expanded")

# ---- Save ----
doc.save(DOC_PATH)
print(f"\nDocument saved: {DOC_PATH}")
print("All Section 7 updates applied.")
