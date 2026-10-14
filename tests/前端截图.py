# -*- coding: utf-8 -*-
"""用 Playwright 走一遍完整流程并逐页截图。

比手工截图可靠：能真正上传文件、点按钮、等状态更新，产出的是
「用户实际会看到什么」，可用于自查渲染问题，也可作为演示视频的素材。

用法：
    python tests/前端截图.py [输出目录]
默认输出到 %TEMP%/wq_shot/
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"
SAMPLE = r"C:\Users\Cloud-Mist Sky Lake\Desktop\苗圃杯\无人船智能水质监测分析和因果溯源样本数据\河道巡航走航样本数据.xlsx"
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "wq_shot")
os.makedirs(OUT, exist_ok=True)


def shot(page, name, full=True):
    path = os.path.join(OUT, f"{name}.png")
    page.screenshot(path=path, full_page=full)
    size = os.path.getsize(path)
    print(f"    ✓ {name:22s} {size:>8,} 字节")
    return path


def safe(fn, page, label):
    """执行一步；失败时打印页面可见文本再抛出，便于定位是选择器问题还是渲染问题。"""
    try:
        return fn()
    except Exception:
        print(f"    ✗ {label} 失败，页面当前可见文本（前 600 字）：")
        try:
            print("      " + page.locator('body').inner_text()[:600].replace(chr(10), ' | '))
        except Exception:
            pass
        raise


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1680, "height": 1050},
                              device_scale_factor=1.5, locale="zh-CN")
    page = ctx.new_page()
    page.on("console", lambda m: print(f"    [console.{m.type}] {m.text[:120]}")
            if m.type in ("error", "warning") else None)

    print("=" * 70)
    print("前端截图流程")
    print("=" * 70)

    # ---------- 1. 打开 ----------
    print("\n1. 打开应用")
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(1200)
    print(f"    标题: {page.title()}")
    print(f"    顶部状态: {page.locator('.pill').first.inner_text()}")
    shot(page, "01_初始状态")

    # ---------- 2. 上传数据 ----------
    print("\n2. 上传走航数据")
    page.set_input_files('input[type="file"][accept*="xlsx"]', SAMPLE)
    page.wait_for_timeout(500)
    page.click('button:has-text("加载数据")')
    # 等侧栏出现记录数，最多 30 秒
    page.wait_for_selector('.stat', timeout=30000)
    page.wait_for_timeout(800)
    print(f"    顶部状态: {page.locator('.pill').first.inner_text()}")
    shot(page, "02_数据已加载", full=False)

    # ---------- 3. 技术展示：黑臭检测 ----------
    print("\n3. 技术展示页 —— 运行黑臭检测")
    page.click('a:has-text("技术展示")')
    page.wait_for_timeout(600)
    shot(page, "03_技术展示_初始")

    page.click('button:has-text("运行黑臭检测")')
    print("    检测中（约 5 秒）…")
    page.wait_for_selector('text=黑点总数', timeout=60000)
    page.wait_for_timeout(1500)
    shot(page, "04_黑臭检测结果")

    # ---------- 4. 国控站点 ----------
    print("\n4. 国控站点实时查询")
    page.click('button:has-text("查询")')
    print("    查询中（约 10 秒）…")
    page.wait_for_selector('.table-wrap table', timeout=90000)
    page.wait_for_timeout(1000)
    shot(page, "05_国控站点")

    # ---------- 5. 多渠道溯源 ----------
    print("\n5. 多渠道溯源")
    page.click('button:has-text("运行多渠道溯源")')
    print("    溯源中（约 20 秒）…")
    # ⚠️ 必须限定在这个区块内。页面上「国控站点」也有 .table-wrap table，
    # 用全局选择器会立刻匹配到它，导致截到的是「溯源中…」而非结果。
    page.wait_for_selector('.block:has-text("多渠道污染溯源") .table-wrap table',
                           timeout=120000)
    page.wait_for_timeout(1500)
    shot(page, "06_多渠道溯源")

    # ---------- 6. RAG ----------
    print("\n6. RAG 知识库检索")
    page.click('button:has-text("黑臭水体治理")')
    print("    检索中（约 10 秒）…")
    page.wait_for_selector('.case', timeout=90000)
    page.wait_for_timeout(800)
    shot(page, "07_RAG检索")

    # ---------- 7. 分析图表 ----------
    print("\n7. 分析图表页")
    page.click('a:has-text("分析图表")')
    page.wait_for_timeout(3500)          # 等 ECharts 渲染
    shot(page, "08_分析图表")

    # ---------- 8. 河道模型 ----------
    print("\n8. 河道模型页")
    page.click('a:has-text("河道模型")')
    page.wait_for_timeout(1200)
    shot(page, "09_河道模型_初始")

    page.click('button:has-text("重新检测")')
    print("    参数检测中…")
    page.wait_for_selector('.prow', timeout=60000)
    page.wait_for_timeout(800)
    shot(page, "10_河道参数检测")

    # ---------- 9. 控制台错误汇总 ----------
    print("\n9. 截图完成")
    print(f"    输出目录: {OUT}")

    browser.close()
