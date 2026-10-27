"""N06 河道模型页面浏览器验收：无监测数据的手工恒定流模板、分步状态和错误就近提示。"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from playwright.sync_api import sync_playwright

def measure(page):
    return page.evaluate("""() => ({
      statuses:[...document.querySelectorAll('.step-status')].map(e=>e.textContent.trim()),
      changed:document.querySelector('.seq-hint')?.textContent||'',
      result:[...document.querySelectorAll('#river-hydrodynamics .result')].map(e=>e.innerText).join(' | '),
      failure:document.querySelector('#river-hydrodynamics [role=alert]')?.textContent||'',
      notice:document.querySelector('.seq-hint[role=status]')?.textContent||'',
      tabs:!!document.querySelector('.tabs')?.getClientRects().length
    })""")

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--url',default='http://127.0.0.1:5180/river'); p.add_argument('--out',default='docs/validation'); a=p.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    report={'url':a.url,'checks':{},'screenshots':[]}
    with sync_playwright() as browser:
        b=browser.chromium.launch(); page=b.new_page(viewport={'width':1280,'height':800})
        page.goto(a.url,wait_until='domcontentloaded'); page.wait_for_selector('.model-guide',timeout=30000); page.wait_for_function("() => !document.querySelector('.connection-panel')",timeout=30000); page.wait_for_timeout(300)
        initial=measure(page)
        initial['pass']=all(x in initial['statuses'] for x in ('可计算','未开始')) and initial['tabs']
        report['checks']['initial']=initial
        page.get_by_role('button',name='初始化模型').click()
        page.wait_for_selector('#river-hydrodynamics .result',timeout=30000)
        after_init=measure(page)
        after_init['pass']='已完成' in after_init['statuses'] and '断面数' in after_init['result']
        report['checks']['after_init']=after_init
        page.get_by_role('button',name='运行水动力').click()
        page.wait_for_timeout(8000)
        after_hydro=measure(page)
        after_hydro['pass']='已完成' in after_hydro['statuses'] and '工况' in after_hydro['result']
        report['checks']['after_hydro']=after_hydro
        page.screenshot(path=str(out/'river_guide_after_hydro_1280x800.png'),full_page=False); report['screenshots'].append(str(out/'river_guide_after_hydro_1280x800.png'))
        # 改变上游流量 -> 旧水动力结果失效、可重新计算
        flow=page.locator('label').filter(has_text='上游流量').locator('input').first
        flow.fill('7')
        page.wait_for_timeout(300)
        changed=measure(page)
        changed['pass']='上游流量' in changed['notice'] and '可计算' in changed['statuses']
        report['checks']['changed_upstream']=changed
        # 非法长度 -> 就近失败提示，不显示伪结果
        length=page.locator('label').filter(has_text='河段长度').locator('input').first
        length.fill('0')
        page.get_by_role('button',name='初始化模型').click()
        page.wait_for_function("() => (document.querySelector('#river-hydrodynamics [role=alert]')?.textContent||'').includes('初始化失败')",timeout=30000)
        invalid=measure(page)
        invalid['pass']='初始化失败' in invalid['failure'] and '失败' in invalid['statuses'] and '断面数' not in invalid['result']
        report['checks']['invalid_length']=invalid
        page.screenshot(path=str(out/'river_guide_invalid_1280x800.png'),full_page=False); report['screenshots'].append(str(out/'river_guide_invalid_1280x800.png'))
        b.close()
    report['passed']=all(v.get('pass') for v in report['checks'].values())
    (out/'browser_river_guide_20261027.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],**{k:v.get('pass') for k,v in report['checks'].items()}},ensure_ascii=False))
    return 0 if report['passed'] else 1

if __name__=='__main__': raise SystemExit(main())
