"""实际生产构建的双模式共享会话与无Key端到端验收。"""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

if __name__=='__main__':
 out=Path('output/simple-mode-browser');out.mkdir(exist_ok=True)
 with sync_playwright() as p:
  browser=p.chromium.launch();page=browser.new_page(viewport={'width':1280,'height':800})
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.route('https://**/*',lambda r:r.abort())
  page.route('**/api/maps/**',lambda r:r.abort())
  page.goto('http://127.0.0.1:5184/')
  page.get_by_role('link',name='进入简易版').click()
  page.get_by_role('heading',name='从一张监测表开始').wait_for()
  assert not page.locator('#project-sidebar').count()
  assert not page.get_by_role('navigation',name='主要导航').is_visible()
  page.locator('.simple-card select').first.select_option('S01')
  page.get_by_role('button',name='载入样例并检测').click()
  page.get_by_role('heading',name='读懂本次检测').wait_for(timeout=45000)
  page.wait_for_function("document.querySelector('.simple-stats article:nth-child(2) strong').textContent!=='待检测'")
  sid=page.evaluate('localStorage.getItem("wq_session_id")')
  before=page.request.get('http://127.0.0.1:5184/api/data/status',headers={'X-Session-Id':sid}).json()
  assert before['has_detection']
  text=page.locator('.simple-stats article:nth-child(2) strong').inner_text()
  assert int(text)==before['black_spot_count']
  assert page.locator('.simple-identity').inner_text().startswith('仿真示例')
  page.get_by_role('heading',name='采样时间趋势').scroll_into_view_if_needed()
  page.locator('.simple-card canvas').last.wait_for()
  page.wait_for_timeout(200)
  page.screenshot(path=str(out/'results.png'),full_page=True)
  page.locator('.simple-steps button').nth(2).click()
  assert page.get_by_role('button',name='让 AI 解释本次检测').is_disabled()
  page.get_by_text('基础检测可用。AI 解释需要配置服务端模型密钥。').wait_for()
  page.get_by_role('link',name='专业版',exact=True).click()
  page.get_by_role('navigation',name='主要导航').wait_for()
  assert page.locator('.tab-links>a').count()==4
  page.locator('.tabs').get_by_role('link',name='简易版',exact=True).click()
  page.get_by_role('heading',name='读懂本次检测').wait_for()
  assert sid==page.evaluate('localStorage.getItem("wq_session_id")')
  writes=[]
  page.on('request',lambda r:writes.append(r.url) if '/api/investigations' in r.url and r.method!='GET' else None)
  page.locator('.simple-steps button').nth(3).click()
  page.get_by_role('button',name='编辑待确认的复测计划').click()
  page.locator('#investigation-board').wait_for()
  assert not writes,'Opening a plan must not create a task'
  page.locator('.simple-steps button').nth(1).click()
  after=page.request.get('http://127.0.0.1:5184/api/data/status',headers={'X-Session-Id':sid}).json()
  for key in ('rows','analysis_revision','results_revision'):assert before[key]==after[key]
  project_before=page.request.get('http://127.0.0.1:5184/api/projects',headers={'X-Session-Id':sid}).json()['current']['id']
  page.reload()
  page.get_by_role('heading',name='读懂本次检测').wait_for()
  assert sid==page.evaluate('localStorage.getItem("wq_session_id")')
  assert page.request.get('http://127.0.0.1:5184/api/projects',headers={'X-Session-Id':sid}).json()['current']['id']==project_before
  page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(200)
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.screenshot(path=str(out/'mobile.png'),full_page=True)
  config=page.request.get('http://127.0.0.1:5184/api/config',headers={'X-Session-Id':sid}).json()
  config['keys']['deepseek']=True
  page.route('**/api/config',lambda r:r.fulfill(json=config))
  page.goto('http://127.0.0.1:5184/chat')
  composer=page.get_by_role('textbox',name='向水质分析助手提问')
  composer.fill('模式切换草稿验收，不发送')
  page.locator('.tabs').get_by_role('link',name='简易版',exact=True).click()
  page.get_by_role('link',name='专业版',exact=True).click()
  assert composer.input_value()=='模式切换草稿验收，不发送'
  page.unroute('**/api/config')
  page.locator('.tabs').get_by_role('link',name='简易版',exact=True).click()
  for sample_id in ['S02','S03','S04','S05','S06']:
   page.locator('.simple-steps button').nth(0).click()
   page.locator('.simple-card select').first.select_option(sample_id)
   page.get_by_role('button',name='载入样例并检测').click()
   page.get_by_role('heading',name='读懂本次检测').wait_for(timeout=45000)
   page.wait_for_function("document.querySelector('.simple-stats article:nth-child(2) strong').textContent!=='待检测'")
   assert page.locator('.simple-identity').inner_text().startswith('仿真示例')
  for path in ('/','/simple','/chat','/charts','/river','/tech'):
   assert page.request.get('http://127.0.0.1:5184'+path).status==200
  assert not errors,errors
  result=dict(shared_session=True,mode_roundtrip=True,draft_preserved=True,no_task_created_on_entry=True,no_key_detection=True,samples=6,warning_points=int(text),mobile_no_overflow=True,page_errors=errors)
  (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result));browser.close()
