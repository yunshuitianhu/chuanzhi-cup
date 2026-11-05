"""Read-only production-page checks: actual basemap plus current model trail."""
import argparse
import json
from pathlib import Path
import urllib.request

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:5174')
    parser.add_argument('--session-file', required=True, help='Local JSON containing the existing sid; never committed')
    parser.add_argument('--output', default='output/map-overlay-acceptance')
    args = parser.parse_args()
    saved = json.loads(Path(args.session_file).read_text(encoding='utf-8'))
    out = Path(args.output);out.mkdir(parents=True,exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(args.base_url+'/api/river/playback',headers={'X-Session-Id':saved['sid']})
    data = json.load(opener.open(request,timeout=30))
    assert data['mapping']['available']
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':1331,'height':1050})
        # This acceptance is scoped to the user-approved Starcloud destination.
        # Do not trigger the app's other external basemap fallbacks during the test.
        page.route('https://**/*',lambda route:route.abort())
        page.route('**/api/maps/amap-static*',lambda route:route.abort())
        page.add_init_script('localStorage.setItem("wq_session_id", '+json.dumps(saved['sid'])+')')
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(args.base_url+'/river')
        player=page.locator('.pollution-player');canvas=player.locator('canvas.simulation-trail')
        canvas.wait_for(timeout=45000)
        player.locator('.map-shell').scroll_into_view_if_needed()
        page.wait_for_function("Array.from(document.querySelectorAll('.pollution-player img.leaflet-tile-loaded')).some(i=>i.complete&&i.naturalWidth>0)",timeout=45000)
        def pixels():
            return canvas.evaluate('''canvas=>{const a=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
                let n=0;for(let i=3;i<a.length;i+=4)if(a[i])n++;return n;}''')
        assert pixels()==0
        assert player.locator('.simulation-source').count()==1
        page.screenshot(path=str(out/'initial.png'))
        player.get_by_role('button',name='跳到扩散峰值').click()
        page.wait_for_function("(()=>{const c=document.querySelector('.pollution-player canvas.simulation-trail');return c&&c.getContext('2d').getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0)})()")
        peak_pixels=pixels();peak_time=player.locator('.playback-controls strong').inner_text()
        player.locator('.map-shell').scroll_into_view_if_needed()
        page.screenshot(path=str(out/'peak.png'))
        player.locator('.leaflet-control-zoom-in').click();page.wait_for_timeout(400)
        assert pixels()>0
        player.get_by_role('button',name='复位',exact=True).click();page.wait_for_timeout(100)
        assert pixels()==0
        player.get_by_role('button',name='跳到扩散峰值').click();page.wait_for_timeout(100)
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(250)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        player.locator('.map-shell').scroll_into_view_if_needed();page.screenshot(path=str(out/'mobile.png'))
        assert not errors,errors
        report={'url':args.base_url+'/river','nodes':len(data['chainage']),
            'complete':data['mapping'].get('complete',True),'mapped_range_m':data['mapping'].get('mapped_range_m'),
            'initial_pixels':0,'peak_pixels':peak_pixels,'peak_time':peak_time,
            'basemap_tiles':player.locator('img.leaflet-tile-loaded').count(),
            'reset_and_zoom':True,'mobile_no_overflow':True,'page_errors':errors,
            'scope':'current model scenario over actual map tiles; not field plume validation'}
        (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False));browser.close()


if __name__=='__main__':main()
