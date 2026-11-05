"""Regression of the complete playback UI with an unavailable-map API response."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:5182/tests/fixtures/simulationPlaybackPreview.html')
    parser.add_argument('--payload', help='Optional locally captured playback response; never edits a project')
    args = parser.parse_args()
    out = Path('output/simulation-fallback-acceptance'); out.mkdir(parents=True, exist_ok=True)
    payload = json.loads(Path(args.payload).read_text(encoding='utf-8')) if args.payload else {
        'constituent':'ammonia','label':'氨氮','river_name':'受控河段（非实测）',
        'times':[0,.1,.2,.3],'chainage':[0,100,200,300,400],
        'concentrations':[[2,2,2,2,2],[2,8,3,2,2],[2,2.4,5,3,2],[2,2,2,2,2]],
        'source':{'source_effective_chainage_m':100,'source_start_h':0,'duration_min':5,'load_kg':5,'pollutant_type':'ammonia'},
        'color_scale':{'min':2,'max':8,'inverse':False},'total_frames':4,'limit':2,
        'target_water_class':'V','limit_direction':'maximum','notice':'受控显示回归，非实测',
        'mapping':{'available':False,'positions':[],'reason':'当前走航轨迹未覆盖模拟桩号范围；请核对河段长度、起点与方向。'},
    }
    assert payload['mapping']['available'] is False
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width':1100,'height':1000})
        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/river/playback*', lambda route: route.fulfill(json=payload))
        page.goto(args.url)
        svg = page.locator('.schematic svg'); svg.wait_for()
        segments = page.locator('.schematic-trail')
        assert segments.get_attribute('data-segments') == '0'
        assert page.locator('.schematic-source').count() == 1
        assert page.get_by_text('卫星地图暂未定位，下面先播放河道示意图。').is_visible()
        assert page.get_by_role('link',name='配置河道参考线与方向 →').get_attribute('href') == '/river#river-alignment'
        page.screenshot(path=str(out/'initial.png'),full_page=True)
        page.get_by_role('button',name='跳到扩散峰值').click()
        page.wait_for_function("Number(document.querySelector('.schematic-trail').dataset.segments)>0")
        page.screenshot(path=str(out/'peak.png'),full_page=True)
        peak_index = int(page.locator('.time-slider').input_value())
        page.get_by_role('button',name='复位',exact=True).click()
        assert segments.get_attribute('data-segments') == '0'
        page.get_by_role('button',name='播放扩散',exact=True).click()
        page.wait_for_function("Number(document.querySelector('.schematic-trail').dataset.segments)>0")
        page.get_by_role('button',name='暂停',exact=True).click()
        page.get_by_role('button',name='复位',exact=True).click()
        page.locator('.time-slider').fill(str(len(payload['times'])-1))
        if not args.payload:
            assert segments.get_attribute('data-segments') == '0'
        page.get_by_role('button',name='跳到扩散峰值').click()
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(100)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(out/'mobile.png'),full_page=True)
        assert not errors, errors
        report={'source_only_initial':True,'fallback_visible':True,'peak_frame':peak_index,
                'play_and_reset':True,'mobile_no_overflow':True,'page_errors':errors,
                'data':'captured model output' if args.payload else 'controlled API response, not field measurements'}
        (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False));browser.close()


if __name__ == '__main__': main()
