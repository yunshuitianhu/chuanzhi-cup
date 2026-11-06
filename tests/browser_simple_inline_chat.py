"""隔离UI回归：真实表格和趋势API，共用聊天组件的受控SSE；不调用LLM。"""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

if __name__=='__main__':
 out=Path('output/simple-inline-browser');out.mkdir(exist_ok=True)
 with sync_playwright() as p:
  browser=p.chromium.launch();page=browser.new_page(viewport={'width':1280,'height':900})
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.route('https://**/*',lambda r:r.abort());page.route('**/api/maps/**',lambda r:r.abort())
  page.goto('http://127.0.0.1:5184/simple')
  page.locator('.simple-card select').first.select_option('S02')
  page.get_by_role('button',name='载入样例并检测').click()
  page.locator('.simple-table tbody tr').first.wait_for(timeout=45000)
  assert page.locator('.simple-table tbody tr').count()==50
  sid=page.evaluate('localStorage.getItem("wq_session_id")')
  preview=page.request.get('http://127.0.0.1:5184/api/data/preview',headers={'X-Session-Id':sid}).json()
  assert len(preview['rows'])==50 and preview['total']==1801
  page.get_by_label('选择趋势指标').select_option('dissolved_oxygen')
  page.get_by_role('heading',name='采样时间趋势').scroll_into_view_if_needed()
  page.locator('.simple-card .echart canvas').wait_for()
  page.screenshot(path=str(out/'table-and-chart.png'),full_page=True)
  page.locator('.simple-steps button').nth(2).click()
  composer=page.get_by_role('textbox',name='向水质分析助手提问')
  composer.fill('无Key测试，不应发出AI请求')
  sent=[];page.on('request',lambda r:sent.append(r) if '/api/agent/chat' in r.url else None)
  page.locator('.composer button[type=submit]').click()
  page.get_by_text('未配置 DeepSeek Key：',exact=False).wait_for()
  assert not sent and page.url.endswith('/simple')
  # Simulate configured state and deterministic SSE without claiming a live model answer.
  config=page.request.get('http://127.0.0.1:5184/api/config',headers={'X-Session-Id':sid}).json();config['keys']['deepseek']=True
  page.route('**/api/config',lambda r:r.fulfill(json=config))
  page.reload();page.get_by_role('heading',name='读懂本次检测').wait_for()
  page.locator('.simple-steps button').nth(2).click();composer=page.get_by_role('textbox',name='向水质分析助手提问')
  history=[];requests=[]
  page.route('**/api/agent/history',lambda r:r.fulfill(json={'messages':history}))
  def reply(route):
   body=route.request.post_data_json;requests.append((route.request.headers,body))
   history.extend([{'role':'user','content':body['message']},{'role':'assistant','content':'受控SSE测试回答：需要核对采样证据。'}])
   route.fulfill(content_type='text/event-stream',body='event: delta\ndata: {"text":"受控SSE测试回答：需要核对采样证据。"}\n\nevent: done\ndata: {"status":"succeeded"}\n\n')
  page.route('**/api/agent/chat',reply)
  composer.fill('解释当前检测依据')
  page.locator('.composer button[type=submit]').click()
  page.get_by_text('受控SSE测试回答：需要核对采样证据。',exact=True).wait_for()
  assert requests[0][0]['x-session-id']==sid
  assert requests[0][1]['auto'] is False and page.url.endswith('/simple')
  page.locator('.simple-steps button').nth(1).click();page.locator('.simple-steps button').nth(2).click()
  assert page.get_by_text('受控SSE测试回答：需要核对采样证据。',exact=True).is_visible()
  page.screenshot(path=str(out/'inline-chat.png'),full_page=True)
  # Failure must remain in the same simple page and display the actual error.
  page.unroute('**/api/agent/chat')
  page.route('**/api/agent/chat',lambda r:r.fulfill(status=503,json={'detail':'受控测试：模型服务暂不可用'}))
  composer.fill('失败测试')
  page.locator('.composer button[type=submit]').click()
  page.get_by_text('受控测试：模型服务暂不可用',exact=False).first.wait_for()
  assert page.url.endswith('/simple')
  page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(150)
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  assert not errors,errors
  result={'table_rows':50,'total':1801,'indicator_switch':True,'no_redirect':True,'sse_session_header':True,'no_key_no_request':True,'failure_visible':True,'step_history_preserved':True,'mobile_no_overflow':True,'page_errors':errors,'ai':'controlled SSE only, not live LLM'}
  (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False));browser.close()
