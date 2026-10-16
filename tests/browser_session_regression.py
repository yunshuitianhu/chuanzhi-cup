import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tempfile
import urllib.request

from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
output_root = Path(os.environ.get('WQ_TEST_OUTPUT', root / 'work'))
output_root.mkdir(parents=True, exist_ok=True)
work = Path(tempfile.mkdtemp(prefix='browser-regression-', dir=output_root))
cache = Path.home() / 'AppData/Local/ms-playwright'
if cache.is_dir():
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(cache))
env = dict(os.environ, WQ_PROJECT_ROOT=str(root), BACKEND_PORT='18372', PYTHONUTF8='1')
processes = []
logs = []
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def launch(command, cwd, name):
    log = (work / f'{name}.log').open('w', encoding='utf-8')
    logs.append(log)
    processes.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                     stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NO_WINDOW))


def ready(url):
    for _ in range(80):
        if any(process.poll() is not None for process in processes):
            raise RuntimeError(f'本地测试服务启动失败，日志位于 {work}')
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f'启动超时: {url}')


try:
    launch([sys.executable, '-m', 'uvicorn', 'browser_stub_server:app', '--app-dir', str(root / 'tests'),
            '--host', '127.0.0.1', '--port', '18372'], root, 'qa_backend')
    launch([shutil.which('node'), str(root / 'frontend/node_modules/vite/bin/vite.js'),
            '--host', '127.0.0.1', '--port', '18373', '--strictPort'], root / 'frontend', 'qa_vite')
    ready('http://127.0.0.1:18372/api/health')
    ready('http://127.0.0.1:18373/')
    checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[f'--log-file={work / "chromium-debug.log"}'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:18373/')
        data = ('datetime,longitude,latitude,ammonia_nitrogen,dissolved_oxygen,turbidity,ph\n'
                '2025-08-01 10:00:00,113.14,23.12,3,4,10,7\n'
                '2025-08-01 10:00:10,113.141,23.121,4,3,12,7\n')
        page.locator('input[type=file]').first.set_input_files({
            'name': 'sample.csv', 'mimeType': 'text/csv', 'buffer': data.encode()})
        page.get_by_role('button', name='加载数据', exact=True).click()
        expect(page.locator('.composer input')).to_be_enabled()
        page.locator('.composer input').fill('测试流式响应')
        page.get_by_role('button', name='发送', exact=True).click()
        expect(page.locator('.stream')).to_contain_text('这是第一段流式内容。')
        checks.append('第一段 SSE 内容立即渲染')
        page.get_by_role('link', name='分析图表').click()
        page.get_by_role('link', name='智能对话').click()
        expect(page.locator('.stream')).to_contain_text('这是第二段完整结论。')
        checks.append('流式进行中切换路由保留对话并继续接收')
        page.reload()
        expect(page.locator('.stream')).to_contain_text('这是第二段完整结论。')
        checks.append('刷新页面从后端恢复历史')
        page.get_by_role('button', name='III', exact=True).click()
        expect(page.locator('.stream')).not_to_contain_text('这是第二段完整结论。')
        checks.append('切换水质等级清除旧对话')
        page.get_by_role('link', name='分析图表').click()
        expect(page.locator('canvas').first).to_be_visible()
        chart = page.evaluate("fetch('/api/charts/time-series', {headers: {'X-Session-Id': localStorage.getItem('wq_session_id')}}).then(r=>r.json())")
        assert chart['target_water_class'] == 'III'
        assert chart['panels'][0]['limit'] == 1.0
        checks.append('III 类图表成功绘制且限值为 1.0')
        page.wait_for_timeout(1200)  # 等待 ECharts 入场动画完成后保存供人工检查的图片
        page.screenshot(path=str(work / 'browser_charts.png'), full_page=True)
        page.get_by_role('link', name='智能对话').click()
        page.locator('.composer input').fill('第二轮测试')
        page.get_by_role('button', name='发送', exact=True).click()
        expect(page.locator('.stream')).to_contain_text('这是第二段完整结论。')
        expect(page.get_by_role('button', name='发送', exact=True)).to_be_disabled()
        page.locator('input[type=file]').first.set_input_files({
            'name': 'same-row-count.csv', 'mimeType': 'text/csv', 'buffer': data.encode()})
        load = page.get_by_role('button', name='重新加载数据', exact=True)
        expect(load).to_be_enabled()
        load.click()
        expect(page.locator('.stream')).not_to_contain_text('这是第二段完整结论。')
        checks.append('相同行数的新文件也清除旧对话')
        assert not errors, errors
        checks.append('浏览器无未捕获 JavaScript 异常')
        browser.close()
    print(json.dumps({'passed': checks, 'artifacts': str(work)}, ensure_ascii=False, indent=2))
finally:
    for process in reversed(processes):
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    for log in logs:
        log.close()
