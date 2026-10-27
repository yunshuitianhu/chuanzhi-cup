"""N07 浏览器可读性验收：四页、320/390/768/1280 宽度及 200% 页面缩放。

使用本机服务与内置 S01 仿真示例；不调用模型、不修改用户项目。输出只用于
检查溢出、主内容可见和字号，不声称流畅度或现场准确率。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROUTES = [('chat', '/chat'), ('charts', '/charts'), ('river', '/river'), ('tech', '/tech')]
WIDTHS = [320, 390, 768, 1280]


def wait_page(page, url):
    page.goto(url, wait_until='domcontentloaded')
    page.wait_for_selector('.content', timeout=30000)
    page.wait_for_function("() => !document.querySelector('.connection-panel')", timeout=30000)
    page.wait_for_timeout(250)


def load_sample(page):
    result = page.evaluate("""async () => {
      const sid=localStorage.getItem('wq_session_id');
      const headers={'Content-Type':'application/json',...(sid?{'X-Session-Id':sid}:{})};
      const response=await fetch('/api/data/samples/S01/load',{method:'POST',headers,
        body:JSON.stringify({project_id:null,analysis_revision:0,results_revision:0,detect:true,river_form:null})});
      return {status:response.status,body:await response.json().catch(()=>({}))};
    }""")
    if result['status'] != 200:
        raise RuntimeError(f"示例载入失败: {result['status']} {result.get('body')}")
    page.reload(wait_until='domcontentloaded')
    page.wait_for_selector('.sample-section', timeout=30000)
    page.wait_for_function("() => !document.querySelector('.connection-panel')", timeout=30000)
    page.wait_for_timeout(300)


def measure(page):
    return page.evaluate("""() => {
      const body=getComputedStyle(document.body);
      const content=document.querySelector('.content');
      const buttons=[...document.querySelectorAll('button')].filter(b=>b.getClientRects().length && !b.disabled);
      const answer=document.querySelector('.report-markdown');
      const description=document.querySelector('.desc,.page-heading p,.report-description');
      return {
        viewport:[innerWidth,innerHeight],
        bodyFont:body.fontSize,
        overflowX:document.documentElement.scrollWidth-innerWidth,
        overflowY:document.documentElement.scrollHeight-innerHeight,
        contentVisible:!!content && !!content.getClientRects().length,
        visibleButtons:buttons.length,
        answerFont:answer?getComputedStyle(answer).fontSize:null,
        descriptionFont:description?getComputedStyle(description).fontSize:null,
        tabVisible:!!document.querySelector('.tabs')?.getClientRects().length,
        composerVisible:!!document.querySelector('.composer')?.getClientRects().length,
      };
    }""")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5180/chat')
    parser.add_argument('--out', default='docs/validation')
    args = parser.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    report = {'url': args.url, 'checks': [], 'screenshots': []}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        wait_page(page, args.url)
        load_sample(page)
        for width in WIDTHS:
            page.set_viewport_size({'width': width, 'height': 800})
            for name, path in ROUTES:
                wait_page(page, 'http://127.0.0.1:5180' + path)
                values = measure(page)
                checks = {
                    'width': width, 'route': path, **values,
                    'pass': values['overflowX'] <= 1 and values['contentVisible']
                            and values['bodyFont'].endswith('px')
                            and float(values['bodyFont'].replace('px', '')) >= 15
                            and values['tabVisible']
                            and (name != 'chat' or values['composerVisible']),
                }
                report['checks'].append(checks)
                if width in (320, 390, 1280):
                    target = out / f'readability_{width}_{name}.png'
                    page.screenshot(path=str(target), full_page=False)
                    report['screenshots'].append(str(target))
        # 200% 页面缩放：模拟浏览器放大后的有效宽度，检查主内容仍可见且无页面级横向溢出。
        page.set_viewport_size({'width': 1280, 'height': 800})
        wait_page(page, 'http://127.0.0.1:5180/chat')
        page.evaluate("() => {document.body.style.zoom='2'}")
        page.wait_for_timeout(300)
        zoomed = measure(page)
        zoomed.update({'width': 1280, 'route': '/chat', 'zoom': '200%',
                       'pass': zoomed['overflowX'] <= 1 and zoomed['contentVisible'] and zoomed['composerVisible']})
        report['checks'].append(zoomed)
        page.screenshot(path=str(out / 'readability_zoom200_chat.png'), full_page=False)
        report['screenshots'].append(str(out / 'readability_zoom200_chat.png'))
        browser.close()
    report['passed'] = all(item['pass'] for item in report['checks'])
    (out / 'browser_readability_20261027.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']),
                      'failed': [item for item in report['checks'] if not item['pass']],
                      'screenshots': report['screenshots']}, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
