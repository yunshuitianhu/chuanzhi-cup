"""浏览器验收：空数据载入内置仿真示例、基础筛查、项目切换与页面显示。

只操作程序生成的示例 CSV；不触发真实模型、外部地图或用户项目。运行前启动
本机服务（例如 `python scripts/serve_web.py --port 5180`）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def wait_ready(page, url):
    page.goto(url, wait_until='domcontentloaded')
    page.wait_for_selector('.composer textarea', timeout=30000)
    page.wait_for_function("() => !document.querySelector('.connection-panel')", timeout=30000)
    page.wait_for_selector('.sample-section', timeout=30000)
    page.wait_for_function("() => document.querySelectorAll('.sample-item').length === 6", timeout=30000)


def sample_item(page, ident):
    return page.locator('.sample-item').filter(has_text=ident).first


def load_sample(page, ident, detect=False):
    item = sample_item(page, ident)
    item.locator('.sample-detect' if detect else '.sample-load').click()
    page.wait_for_function(
        "(ident) => (document.querySelector('.sample-report')?.innerText || '').includes(ident)",
        arg=ident, timeout=30000)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5180/chat')
    parser.add_argument('--out', default='docs/validation')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {'url': args.url, 'checks': {}, 'screenshots': []}
    external = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        page.on('request', lambda request: external.append(request.url)
                if not request.url.startswith(('http://127.0.0.1', 'http://localhost', 'data:', 'blob:')) else None)
        wait_ready(page, args.url)

        listing = page.evaluate("""() => ({
          count: document.querySelectorAll('.sample-item').length,
          titles: [...document.querySelectorAll('.sample-item strong')].map(e=>e.textContent),
          labels: [...document.querySelectorAll('.sample-item .sample-meta')].map(e=>e.textContent),
          intro: document.querySelector('.sample-intro')?.textContent || '',
          uploadVisible: !!document.querySelector('.upload-title'),
          videoPills: [...document.querySelectorAll('.sample-item .sample-meta')].filter(e=>/视频|帧/.test(e.textContent)).length
        })""")
        listing['pass'] = (listing['count'] == 6 and all('仿真' in text for text in listing['labels'])
                           and listing['uploadVisible'] and listing['videoPills'] == 0
                           and '仅监测表' in ' '.join(listing['labels']))
        report['checks']['empty_sample_list'] = listing
        page.screenshot(path=str(out / 'samples_empty_1280x800.png'), full_page=False)
        report['screenshots'].append(str(out / 'samples_empty_1280x800.png'))

        load_sample(page, 'S01')
        loaded = page.evaluate("""() => ({
          report: document.querySelector('.sample-report')?.textContent || '',
          rows: [...document.querySelectorAll('.stat-row')].map(e=>e.textContent).join(' | '),
          identity: document.querySelector('.stat > .hint')?.textContent || '',
          video: [...document.querySelectorAll('.stat-row')].map(e=>e.textContent).find(x=>x.includes('视频帧')) || '',
          project: document.querySelector('.project-tag')?.textContent || document.querySelector('.chat-summary .project-tag')?.textContent || '',
          sampleButtons: document.querySelectorAll('.sample-load').length
        })""")
        loaded['pass'] = ('1801' in loaded['rows'] and '仿真' in loaded['identity']
                          and '0 帧' in loaded['video'] and 'S01' in loaded['report'])
        report['checks']['load_s01'] = loaded
        page.screenshot(path=str(out / 'samples_loaded_s01_1280x800.png'), full_page=False)
        report['screenshots'].append(str(out / 'samples_loaded_s01_1280x800.png'))

        load_sample(page, 'S02', detect=True)
        detected = page.evaluate("""() => ({
          report: document.querySelector('.sample-report')?.textContent || '',
          detectionError: document.querySelector('.sample-error')?.textContent || '',
          rows: [...document.querySelectorAll('.stat-row')].map(e=>e.textContent).join(' | '),
          identity: document.querySelector('.stat > .hint')?.textContent || ''
        })""")
        detected['pass'] = ('S02' in detected['report'] and '筛查完成' in detected['report']
                            and '1801' in detected['rows'] and '仿真' in detected['identity']
                            and not detected['detectionError'])
        report['checks']['load_s02_with_detection'] = detected
        page.screenshot(path=str(out / 'samples_detected_s02_1280x800.png'), full_page=False)
        report['screenshots'].append(str(out / 'samples_detected_s02_1280x800.png'))

        # 项目列表和切换走真实 API；不是用页面文字模拟。
        projects = page.evaluate("""async () => {
          const sid=localStorage.getItem('wq_session_id');
          const reply=await fetch('/api/projects',{headers: sid?{'X-Session-Id':sid}:{}});
          return reply.json();
        }""")
        ids = [item['id'] for item in projects.get('projects', [])]
        names = [item['name'] for item in projects.get('projects', [])]
        switch = {'project_count': len(ids), 'names': names}
        if len(ids) >= 2:
            first = next((item['id'] for item in projects['projects'] if 'S01' in item['name']), ids[-1])
            page.evaluate("""async (id) => {
              const sid=localStorage.getItem('wq_session_id');
              await fetch('/api/projects/'+id+'/open',{method:'POST',
                headers:{'Content-Type':'application/json',...(sid?{'X-Session-Id':sid}:{})},body:'{}'});
            }""", first)
            page.reload(wait_until='domcontentloaded')
            page.wait_for_selector('.sample-section', timeout=30000)
            page.wait_for_function("() => !document.querySelector('.connection-panel')", timeout=30000)
            reopened = page.evaluate("""() => ({
              rows: [...document.querySelectorAll('.stat-row')].map(e=>e.textContent).join(' | '),
              file: document.querySelector('.txt')?.textContent || ''
            })""")
            switch['reopened'] = reopened
            switch['pass'] = ('1801' in reopened['rows'] and 'S01' in reopened['file'])
        else:
            switch['pass'] = False
        report['checks']['project_switch'] = switch

        report['external_requests'] = external
        report['checks']['no_external_requests'] = {'urls': external, 'pass': not external}
        browser.close()
    report['passed'] = all(item.get('pass') for item in report['checks'].values())
    (out / 'browser_sample_acceptance_20261027.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
