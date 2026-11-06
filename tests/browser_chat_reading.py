"""受控长回答阅读验证；不发起真实模型调用。"""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

if __name__=='__main__':
 out=Path('output/chat-reading');out.mkdir(exist_ok=True)
 paragraph='受控阅读测试：本段仅用于验证长文本排版与阅读区域，不能作为真实水质结论。'
 body='\n\n'.join('## '+h+'\n\n'+('\n\n'.join([paragraph]*18)) for h in ['河道污染情况','主要污染类型','最可能的污染源头','建议治理方案'])
 with sync_playwright() as p:
  browser=p.chromium.launch();page=browser.new_page(viewport={'width':1280,'height':900})
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.route('https://**/*',lambda r:r.abort())
  page.route('**/api/agent/history',lambda r:r.fulfill(json={'messages':[{'role':'user','content':'受控阅读测试'},{'role':'assistant','content':body}]}))
  page.goto('http://127.0.0.1:5184/simple')
  page.locator('.simple-steps button').nth(2).click()
  page.get_by_role('heading',name='建议治理方案',exact=True).wait_for()
  panel=page.locator('.compact-chat');stream=panel.locator('.stream')
  assert panel.bounding_box()['height']>900
  assert stream.evaluate('(e)=>e.scrollHeight<=e.clientHeight+2')
  page.get_by_role('button',name='全屏阅读',exact=True).click()
  dialog=page.get_by_role('dialog',name='AI 研判全屏阅读');dialog.wait_for()
  assert 870<=dialog.bounding_box()['height']<=880
  assert stream.evaluate('(e)=>e.scrollHeight>e.clientHeight')
  assert page.evaluate('document.body.style.overflow')=='hidden'
  page.screenshot(path=str(out/'fullscreen.png'))
  page.keyboard.press('Escape')
  assert page.get_by_role('dialog').count()==0
  assert page.evaluate('document.activeElement.textContent.trim()')=='全屏阅读'
  assert page.evaluate('document.body.style.overflow')!='hidden'
  page.set_viewport_size({'width':390,'height':844})
  page.get_by_role('button',name='全屏阅读',exact=True).click()
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.screenshot(path=str(out/'mobile.png'))
  page.keyboard.press('Escape');assert not errors,errors
  report=dict(document_expands=True,fullscreen_fits=True,escape_restores_focus=True,mobile_no_overflow=True,page_errors=errors,data='controlled long text, not a live answer')
  (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report));browser.close()
