"""Q17 浏览器性能观察；记录本机生产构建的相对结果，不作为跨设备门槛。

运行示例：
  python tests/browser_performance_acceptance.py --url http://127.0.0.1:5193/chat \
    --out work/q17_performance --report q17_performance.json

长历史通过 Playwright 路由夹具提供明确标注的合成文本，不调用模型；示例/图表使用真实服务。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics

from playwright.sync_api import sync_playwright

LONG_TASK_SCRIPT = """
window.__q17LongTasks = [];
try {
  new PerformanceObserver(list => {
    for (const entry of list.getEntries()) window.__q17LongTasks.push({start: entry.startTime, duration: entry.duration});
  }).observe({entryTypes: ['longtask']});
} catch (_) {}
"""


def page_stats(page):
    return page.evaluate("""() => {
      const nav = performance.getEntriesByType('navigation')[0] || {};
      const resources = performance.getEntriesByType('resource');
      const stream = document.querySelector('.stream');
      return {
        dom_nodes: document.querySelectorAll('*').length,
        rows: document.querySelectorAll('.row').length,
        assistant_rows: document.querySelectorAll('.row.assistant').length,
        stream_scroll_height: stream ? stream.scrollHeight : 0,
        stream_client_height: stream ? stream.clientHeight : 0,
        heap_bytes: performance.memory ? performance.memory.usedJSHeapSize : null,
        nav: {duration: nav.duration, dom_content_loaded: nav.domContentLoadedEventEnd, load: nav.loadEventEnd},
        resources: {
          count: resources.length,
          transfer_bytes: resources.reduce((n, r) => n + (r.transferSize || 0), 0),
          decoded_bytes: resources.reduce((n, r) => n + (r.decodedBodySize || 0), 0),
        },
        long_tasks: window.__q17LongTasks || [],
      };
    }""")


def summarize(sample):
    tasks = sample.get('long_tasks') or []
    return {
        **sample,
        'long_task_count': len(tasks),
        'long_task_total_ms': round(sum(t['duration'] for t in tasks), 1),
        'long_task_max_ms': round(max((t['duration'] for t in tasks), default=0), 1),
    }


def make_history(count=24, chars=3200):
    block = ('## 证据摘要\n\n'
             '这是明确标注的合成历史文本，用于测量长对话渲染，不代表真实模型输出。\n\n'
             '- 监测数据为仿真数据，非现场实测；热点数量不等于污染源数量。\n'
             '- 需要核对来源、项目版本和分析版本。\n\n'
             '| 指标 | 值 | 说明 |\n|---|---|---|\n| 氨氮 | 0.35 mg/L | 仿真 |\n| 溶解氧 | 6.76 mg/L | 仿真 |\n\n'
             '```text\nwater-quality synthetic fixture\n```\n\n')
    text = (block * max(1, chars // len(block) + 1))[:chars]
    messages = []
    for i in range(count):
        messages.append({
            'role': 'user' if i % 2 == 0 else 'assistant',
            'content': ('合成问题 ' + str(i // 2 + 1) + '：请核对这段仿真数据的限制。') if i % 2 == 0 else text,
        })
    return messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5193/chat')
    parser.add_argument('--out', default='work/q17_performance')
    parser.add_argument('--report', default='q17_performance.json')
    args = parser.parse_args()
    base = args.url.rstrip('/')
    root = base[:-len('/chat')] if base.endswith('/chat') else base
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    result = {'url': base, 'scope': '本机 Chromium 生产构建观察；不是跨设备或现场性能门槛',
              'route_load': {}, 'long_history': {}, 'sample_charts': {}, 'limitations': []}

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # 冷上下文逐页走查；记录导航、资源、DOM 与 long task。
        for route in ('chat', 'charts', 'river', 'tech'):
            context = browser.new_context(viewport={'width': 1280, 'height': 900})
            context.add_init_script(LONG_TASK_SCRIPT)
            page = context.new_page()
            errors = []
            page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
            page.goto(f'{root}/{route}', wait_until='networkidle')
            page.wait_for_timeout(500)
            result['route_load'][route] = summarize({**page_stats(page), 'console_errors': errors})
            page.screenshot(path=str(out / f'load_{route}.png'), full_page=False)
            context.close()

        # 长历史：用明确合成内容替换 history，其他接口仍走真实服务。
        context = browser.new_context(viewport={'width': 1280, 'height': 900})
        context.add_init_script(LONG_TASK_SCRIPT)
        messages = make_history()
        body = json.dumps({'messages': messages, 'analysis_revision': 0}, ensure_ascii=False)
        context.route('**/api/agent/history', lambda route: route.fulfill(
            status=200, content_type='application/json', body=body))
        page = context.new_page()
        page.goto(f'{root}/chat', wait_until='networkidle')
        page.wait_for_function("() => document.querySelectorAll('.row.assistant').length >= 12", timeout=30000)
        page.wait_for_timeout(1200)
        sample = summarize({**page_stats(page), 'fixture_messages': len(messages),
                            'fixture_chars': sum(len(m['content']) for m in messages)})
        page.evaluate("document.querySelector('.stream').scrollTop = 0")
        page.wait_for_timeout(100)
        sample['scroll_top_after_up'] = page.evaluate("document.querySelector('.stream').scrollTop")
        result['long_history'] = sample
        page.screenshot(path=str(out / 'long_history.png'), full_page=False)
        context.close()

        # 真实示例载入 + 图表页；外部底图失败不影响趋势图测量。
        context = browser.new_context(viewport={'width': 1280, 'height': 900})
        context.add_init_script(LONG_TASK_SCRIPT)
        page = context.new_page()
        page.goto(f'{root}/chat', wait_until='networkidle')
        page.get_by_role('button', name='载入并检测').first.click()
        page.wait_for_timeout(2200)
        sample = summarize(page_stats(page))
        page.goto(f'{root}/charts', wait_until='networkidle')
        page.wait_for_timeout(1800)
        sample.update({f'charts_{k}': v for k, v in summarize(page_stats(page)).items()})
        heat = page.locator('label:has-text("严重程度热力图") input')
        start = page.evaluate('performance.now()')
        heat.click()
        page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        off_ms = page.evaluate('performance.now()') - start
        start = page.evaluate('performance.now()')
        heat.click()
        page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        on_ms = page.evaluate('performance.now()') - start
        sample['map_heat_toggle_ms'] = {'off': round(off_ms, 1), 'on': round(on_ms, 1)}
        result['sample_charts'] = sample
        page.screenshot(path=str(out / 'sample_charts.png'), full_page=False)
        context.close()

        browser.close()

    result['limitations'] = [
        '长历史是 Playwright 路由提供的合成夹具，不是真实模型输出或真实项目历史。',
        '示例/图表使用真实服务和 S01 V2；外部地图瓦片受网络影响。',
        '只记录同一台机器、同一 Chromium、同一生产构建的一次观察，不输出跨设备提速比例。',
        '未使用真实读屏器、真机触屏、Safari 或低端硬件。',
    ]
    report = out / args.report
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'report': str(report), 'routes': list(result['route_load']),
                      'long_history': {k: result['long_history'].get(k) for k in
                                       ('dom_nodes', 'rows', 'long_task_total_ms', 'heap_bytes')},
                      'sample_charts': {k: result['sample_charts'].get(k) for k in
                                        ('charts_dom_nodes', 'charts_long_task_total_ms')}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
