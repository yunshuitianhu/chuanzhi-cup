"""Exercise the actual reference-selection UI against six synthetic model sessions."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

if __name__=='__main__':
    records=json.loads(Path('output/map-generalization-ui-sessions.json').read_text())
    out=Path('output/map-generalization-ui');out.mkdir(exist_ok=True)
    reports=[]
    with sync_playwright() as p:
        browser=p.chromium.launch()
        for record in records:
            page=browser.new_page(viewport={'width':1331,'height':1000})
            page.add_init_script('localStorage.setItem("wq_session_id",'+json.dumps(record['sid'])+')')
            # No coordinate requests leave localhost in this controlled UI check.
            tile='<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#19323e"/></svg>'
            page.route('**/api/maps/**',lambda route:route.fulfill(content_type='image/svg+xml',body=tile))
            page.route('https://**/*',lambda route:route.abort())
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:5184/river')
            button=page.get_by_role('button',name='从当前轨迹查找单向航段');button.wait_for(timeout=30000);button.click()
            page.get_by_role('checkbox',name='确认按所选方向作为本次情景的上游→下游；实际水流方向仍需核实').check()
            page.get_by_role('button',name='应用航段并显示地图扩散').click()
            canvas=page.locator('.pollution-player canvas.simulation-trail');canvas.wait_for()
            page.get_by_role('button',name='跳到扩散峰值').click()
            page.wait_for_function('''()=>{const c=document.querySelector('.pollution-player canvas.simulation-trail');return c&&c.getContext('2d').getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0)}''')
            page.locator('.pollution-player .map-shell').scroll_into_view_if_needed()
            page.screenshot(path=str(out/(record['sample']+'.png')))
            assert not errors,errors
            reports.append(dict(sample=record['sample'],map_visible=True,trail_visible=True,page_errors=errors))
            page.close()
        browser.close()
    (out/'result.json').write_text(json.dumps(reports,indent=2),encoding='utf-8')
    print(json.dumps(reports))
