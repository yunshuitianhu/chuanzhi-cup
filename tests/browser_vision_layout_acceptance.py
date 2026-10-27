"""浏览器验收：对话顶部默认收起、矮屏/手机高度、粘贴截图真实上传链路。

针对已运行的本机服务（默认 http://127.0.0.1:5180/chat）执行；不启动模型。
图片是程序生成的黑底白字测试图，不含用户私人数据。
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from playwright.sync_api import sync_playwright


def sample_png(label: str = 'PASTE TEST') -> str:
    image = np.zeros((180, 320, 3), np.uint8)
    image[:] = (40, 90, 140)
    cv2.putText(image, label, (12, 96), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        raise RuntimeError('测试图编码失败')
    return base64.b64encode(encoded.tobytes()).decode('ascii')


def measure(page):
    return page.evaluate("""() => {
      const stream = document.querySelector('.stream');
      const composer = document.querySelector('.composer');
      const summary = document.querySelector('.chat-summary');
      const info = document.querySelector('#chat-analysis-info');
      const input = document.querySelector('.composer textarea');
      const send = document.querySelector('.composer button[type=submit]');
      const workspace = document.querySelector('.chat-workspace');
      return {
        viewport: [innerWidth, innerHeight],
        stream: stream ? stream.clientHeight : null,
        composer: composer ? composer.getBoundingClientRect().height : null,
        summary: summary ? summary.getBoundingClientRect().height : null,
        info: info ? info.getBoundingClientRect().height : 0,
        overflowX: document.documentElement.scrollWidth - innerWidth,
        workspaceBottom: workspace ? workspace.getBoundingClientRect().bottom : null,
        composerBottom: composer ? composer.getBoundingClientRect().bottom : null,
        textareaVisible: !!input?.offsetParent,
        textareaDisabled: !!input?.disabled,
        sendVisible: !!send?.offsetParent,
        sendDisabled: !!send?.disabled,
        activeIsInput: document.activeElement === input,
      };
    }""")


def wait_ready(page, url):
    page.goto(url, wait_until='domcontentloaded')
    page.wait_for_selector('.composer textarea', timeout=30000)
    page.wait_for_function("() => !document.querySelector('.connection-panel')", timeout=30000)
    page.wait_for_timeout(300)


def paste_image(page, png_b64):
    image = base64.b64decode(png_b64)
    page.evaluate("""(bytes) => {
      const data = Uint8Array.from(bytes);
      const file = new File([data], 'pasted-test.png', {type:'image/png'});
      const transfer = new DataTransfer();
      transfer.items.add(file);
      const event = new ClipboardEvent('paste', {clipboardData: transfer, bubbles: true, cancelable: true});
      document.querySelector('.composer textarea').dispatchEvent(event);
    }""", list(image))
    page.wait_for_selector('.context-line', timeout=30000)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5180/chat')
    parser.add_argument('--out', default='docs/validation')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    png = sample_png()
    side_png = sample_png('SIDE TEST')
    side_paste_png = sample_png('SIDE PASTE')
    report = {'url': args.url, 'checks': {}, 'screenshots': []}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        wait_ready(page, args.url)

        desktop = measure(page)
        collapsed = page.evaluate("() => document.querySelector('button[aria-controls=chat-analysis-info]')?.getAttribute('aria-expanded')")
        report['checks']['desktop_collapsed'] = {**desktop, 'aria_expanded': collapsed,
            'pass': desktop['stream'] is not None and desktop['stream'] >= 400 and collapsed == 'false'
                    and desktop['workspaceBottom'] <= desktop['viewport'][1] + 2}
        page.screenshot(path=str(out / 'chat_collapsed_1280x800.png'), full_page=False)
        report['screenshots'].append(str(out / 'chat_collapsed_1280x800.png'))

        empty_input = {'ok': False}
        try:
            page.click('.composer textarea')
            page.fill('.composer textarea', '什么是溶解氧？')
            empty_input = page.evaluate("""() => ({value:document.querySelector('.composer textarea')?.value||'',
              focused:document.activeElement===document.querySelector('.composer textarea'),
              disabled:!!document.querySelector('.composer textarea')?.disabled,
              sendDisabled:!!document.querySelector('.composer button[type=submit]')?.disabled})""")
            page.fill('.composer textarea','')
        except Exception as exc:
            empty_input['error'] = str(exc)
        empty_input['pass'] = bool(not empty_input.get('disabled') and empty_input.get('focused')
                                   and empty_input.get('value') == '什么是溶解氧？'
                                   and not empty_input.get('sendDisabled'))
        report['checks']['empty_session_input'] = empty_input

        page.evaluate("""() => {const el=document.querySelector('.composer textarea');
          el.value='草稿保留检查';el.dispatchEvent(new Event('input',{bubbles:true}));}""")
        page.click('button[aria-controls=chat-analysis-info]')
        page.wait_for_selector('#chat-analysis-info .context-strip')
        page.wait_for_timeout(200)
        expanded = measure(page)
        draft = page.input_value('.composer textarea')
        report['checks']['desktop_expanded'] = {**expanded, 'draft': draft,
            'pass': expanded['stream'] is not None and expanded['stream'] >= 250 and draft == '草稿保留检查'}
        page.screenshot(path=str(out / 'chat_expanded_1280x800.png'), full_page=False)
        report['screenshots'].append(str(out / 'chat_expanded_1280x800.png'))

        page.click('button[aria-controls=chat-analysis-info]')
        page.wait_for_timeout(200)
        report['checks']['desktop_collapsed_again'] = {
            'aria_expanded': page.evaluate("() => document.querySelector('button[aria-controls=chat-analysis-info]')?.getAttribute('aria-expanded')"),
            'draft': page.input_value('.composer textarea')}
        report['checks']['desktop_collapsed_again']['pass'] = (
            report['checks']['desktop_collapsed_again']['aria_expanded'] == 'false'
            and report['checks']['desktop_collapsed_again']['draft'] == '草稿保留检查')

        pasted = {'ok': False}
        try:
            paste_image(page, png)
            pasted = page.evaluate("""() => ({
              ok: true,
              kind: document.querySelector('.context-line .context-kind')?.textContent || '',
              title: document.querySelector('.context-line .context-name')?.textContent || '',
              collapsed: !!document.querySelector('.context-line'),
              thumb: false
            })""")
            page.screenshot(path=str(out / 'chat_image_context_1280x800.png'), full_page=False)
            report['screenshots'].append(str(out / 'chat_image_context_1280x800.png'))
            image_desktop = measure(page)
            report['checks']['desktop_image_collapsed'] = {**image_desktop,
                'pass': image_desktop['stream'] is not None and image_desktop['stream'] >= 400
                        and image_desktop['workspaceBottom'] <= image_desktop['viewport'][1] + 2}

            page.set_viewport_size({'width': 390, 'height': 844})
            page.wait_for_timeout(350)
            image_mobile = measure(page)
            report['checks']['mobile_image_collapsed'] = {**image_mobile,
                'pass': image_mobile['stream'] is not None and image_mobile['stream'] >= 430
                        and image_mobile['overflowX'] <= 0 and image_mobile['sendVisible']}
            page.screenshot(path=str(out / 'chat_image_context_mobile_390x844.png'), full_page=False)
            report['screenshots'].append(str(out / 'chat_image_context_mobile_390x844.png'))
            page.set_viewport_size({'width': 1280, 'height': 800})
            page.wait_for_timeout(350)

            side = {'opened': False}
            side_button = page.locator('button.side-ask')
            if side_button.count():
                side_button.first.click()
                page.wait_for_selector('.side-questions', state='visible', timeout=15000)
                page.wait_for_selector('.side-questions .question-context', timeout=15000)
                side = page.evaluate("""() => ({
                  opened: !!document.querySelector('.side-questions'),
                  quote: document.querySelector('.side-questions .question-context .head span')?.textContent || '',
                  sendVisible: !!document.querySelector('.side-questions button[type=submit]')?.offsetParent,
                  canvas: !!document.querySelector('.side-questions textarea')
                })""")
                page.locator('.side-questions header button').first.click()
                page.wait_for_timeout(200)
            pasted['side_context'] = side

            page.click('button[aria-controls=chat-analysis-info]')
            page.wait_for_selector('#chat-analysis-info .question-context', timeout=15000)
            page.wait_for_selector('#chat-analysis-info .question-context img.thumb', timeout=15000)
            pasted['thumb'] = True
            page.screenshot(path=str(out / 'chat_image_context_expanded_1280x800.png'), full_page=False)
            report['screenshots'].append(str(out / 'chat_image_context_expanded_1280x800.png'))
            link = page.locator('#chat-analysis-info .question-context .meta a').first
            if link.count():
                link.click()
                page.wait_for_selector('.context-source', timeout=15000)
                page.wait_for_function("() => {const el=document.querySelector('.context-source'); return !!el && !el.innerText.includes('正在核对');}", timeout=15000)
                source = page.evaluate("() => ({text:document.querySelector('.context-source')?.innerText||'', error:!!document.querySelector('.context-source [role=alert]')})")
                pasted['source_lookup'] = source
        except Exception as exc:
            pasted = {'ok': False, 'error': str(exc)}
        lookup = pasted.get('source_lookup')
        side = pasted.get('side_context') or {}
        pasted['pass'] = bool(pasted.get('ok') and pasted.get('kind') == '图片'
                              and pasted.get('title') and pasted.get('collapsed') and pasted.get('thumb')
                              and not pasted.get('error')
                              and (not lookup or (lookup.get('text') and not lookup.get('error')))
                              and (not side.get('opened') or (side.get('quote') and side.get('canvas'))))
        report['checks']['paste_image'] = pasted

        side_upload = {'ok': False}
        try:
            page.click('.side-launch')
            page.wait_for_selector('.side-questions', state='visible', timeout=15000)
            page.set_input_files('.side-questions input[type=file]', files=[{
                'name': 'side-upload.png', 'mimeType': 'image/png',
                'buffer': base64.b64decode(side_png)}])
            page.wait_for_function("() => document.querySelector('.side-questions .question-context .head span')?.textContent.includes('side-upload.png')", timeout=20000)
            page.wait_for_selector('.side-questions .question-context img.thumb', timeout=20000)
            side_upload = page.evaluate("""() => ({
              quote: document.querySelector('.side-questions .question-context .head span')?.textContent || '',
              thumb: !!document.querySelector('.side-questions .question-context img.thumb'),
              error: document.querySelector('.side-questions .failure')?.textContent || '',
              sendVisible: !!document.querySelector('.side-questions button[type=submit]')?.offsetParent
            })""")
            side_upload['main_context'] = page.evaluate("() => document.querySelector('#chat-analysis-info .question-context .head span')?.textContent || document.querySelector('.context-name')?.textContent || ''")
            page.screenshot(path=str(out / 'chat_side_image_upload_1280x800.png'), full_page=False)
            report['screenshots'].append(str(out / 'chat_side_image_upload_1280x800.png'))
            page.evaluate("""(bytes) => {
              const data = Uint8Array.from(bytes);
              const file = new File([data], 'side-paste.png', {type:'image/png'});
              const transfer = new DataTransfer(); transfer.items.add(file);
              const area = document.querySelector('.side-questions textarea');
              area.focus();
              area.dispatchEvent(new ClipboardEvent('paste', {clipboardData: transfer, bubbles:true, cancelable:true}));
            }""", list(base64.b64decode(side_paste_png)))
            page.wait_for_function("() => document.querySelector('.side-questions .question-context .head span')?.textContent.includes('side-paste.png')", timeout=20000)
            page.wait_for_selector('.side-questions .question-context img.thumb', timeout=20000)
            side_upload['paste_title'] = page.evaluate("() => document.querySelector('.side-questions .question-context .head span')?.textContent || ''")
            side_upload['paste_thumb'] = page.evaluate("() => !!document.querySelector('.side-questions .question-context img.thumb')")
            page.locator('.side-questions header button').first.click()
            page.wait_for_timeout(200)
        except Exception as exc:
            side_upload['error_detail'] = str(exc)
        side_upload['pass'] = bool(side_upload.get('quote') and side_upload.get('thumb')
                                   and side_upload.get('paste_title') and side_upload.get('paste_thumb')
                                   and not side_upload.get('error') and side_upload.get('sendVisible')
                                   and side_upload.get('main_context'))
        report['checks']['side_image_upload'] = side_upload

        retry = {'ok': False}
        try:
            page.route('**/api/media', lambda route: route.fulfill(status=500, content_type='application/json',
                body='{"detail":"controlled upload failure"}') if route.request.method == 'POST' else route.continue_())
            page.evaluate("""(bytes) => {
              const data = Uint8Array.from(bytes);
              const file = new File([data], 'retry-test.png', {type:'image/png'});
              const transfer = new DataTransfer(); transfer.items.add(file);
              document.querySelector('.composer textarea').dispatchEvent(new ClipboardEvent('paste',
                {clipboardData: transfer, bubbles:true, cancelable:true}));
            }""", list(base64.b64decode(png)))
            page.wait_for_selector('.attach-error', timeout=15000)
            failed = page.locator('button.retry-attach')
            page.unroute('**/api/media')
            retry = {'error': page.locator('.attach-error').inner_text(), 'retry_visible': failed.count() > 0}
            if failed.count():
                failed.first.click()
                page.wait_for_selector('.question-context', timeout=15000)
                retry['ok'] = True
        except Exception as exc:
            retry['error_detail'] = str(exc)
        retry['pass'] = bool(retry.get('ok') and retry.get('retry_visible'))
        report['checks']['upload_failure_retry'] = retry

        page.set_viewport_size({'width': 390, 'height': 844})
        wait_ready(page, args.url)
        mobile = measure(page)
        report['checks']['mobile_390x844'] = {**mobile,
            'pass': mobile['stream'] is not None and mobile['stream'] >= 430
                    and mobile['overflowX'] <= 0 and mobile['sendVisible']}
        page.screenshot(path=str(out / 'chat_mobile_390x844.png'), full_page=False)
        report['screenshots'].append(str(out / 'chat_mobile_390x844.png'))

        page.set_viewport_size({'width': 844, 'height': 390})
        wait_ready(page, args.url)
        landscape = measure(page)
        page.click('button[aria-controls=chat-analysis-info]')
        page.wait_for_selector('#chat-analysis-info .analysis-info')
        scroll = page.evaluate("""() => {
          const info = document.querySelector('#chat-analysis-info .analysis-info');
          info.scrollTop = info.scrollHeight;
          return {scrollTop: info.scrollTop, scrollHeight: info.scrollHeight, clientHeight: info.clientHeight,
                  lastVisible: info.scrollHeight - info.scrollTop - info.clientHeight <= 2};
        }""")
        collapse_visible = page.evaluate("""() => {const b=document.querySelector('button[aria-controls=chat-analysis-info]');return b? b.getBoundingClientRect().bottom<=innerHeight+2 : false;}""")
        report['checks']['landscape_844x390'] = {**landscape, **scroll, 'collapse_visible': collapse_visible,
            'pass': landscape['stream'] is not None and landscape['stream'] >= 60
                    and landscape['overflowX'] <= 0 and landscape['sendVisible']
                    and landscape['workspaceBottom'] <= landscape['viewport'][1] + 2
                    and landscape['composerBottom'] <= landscape['viewport'][1] + 2
                    and scroll['lastVisible'] and collapse_visible}
        page.screenshot(path=str(out / 'chat_landscape_844x390.png'), full_page=False)
        report['screenshots'].append(str(out / 'chat_landscape_844x390.png'))

        browser.close()
    passed = all(item.get('pass') for item in report['checks'].values())
    report['passed'] = passed
    (out / 'browser_vision_layout_20261027.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
