"""Controlled model-frame browser checks; run against an isolated Vite server."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:5182/tests/fixtures/simulationTrailPreview.html')
    args = parser.parse_args()
    out = Path('output/simulation-trail-acceptance'); out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1200, 'height': 950})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        # External tiles intentionally isolated; tests do not certify a satellite registration.
        page.route('https://**/*', lambda route: route.fulfill(status=200, content_type='image/svg+xml',
            body='<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#182f37"/></svg>'))
        page.goto(args.url)
        page.wait_for_selector('canvas.simulation-trail')
        def pixels():
            return page.locator('canvas.simulation-trail').evaluate('''canvas => {
              const a=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
              let count=0;for(let i=3;i<a.length;i+=4)if(a[i])count++;return count;
            }''')
        def frame(index):
            page.locator('#frame').fill(str(index))
            page.wait_for_timeout(120)  # Allow Vue's watcher and coalesced canvas redraw.
        initial = pixels()
        assert initial == 0, 'Initial map must contain no concentration band'
        page.screenshot(path=str(out/'initial.png'))
        frame(1); early = pixels(); assert early > 0
        page.screenshot(path=str(out/'early.png'))
        frame(2); later = pixels(); assert later > 0
        page.screenshot(path=str(out/'later.png'))
        frame(1); assert pixels() == early, 'Seek must not retain future-frame paint'
        frame(3); assert pixels() == 0, 'A returned-to-baseline frame must clear the trail'
        frame(0); assert pixels() == 0, 'Reset must clear the trail'
        frame(2)
        page.locator('.leaflet-control-zoom-in').click(); page.wait_for_timeout(500)
        assert pixels() > 0, 'Map zoom must redraw the overlay'
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(200)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Page must fit mobile width'
        assert not errors, errors
        result={'initial_pixels':initial,'early_pixels':early,'later_pixels':later,
                'reset_and_backward_seek':True,'zoom_redraw':True,'mobile_no_overflow':True,
                'page_errors':errors,'data':'controlled frames and coordinates, not field measurements'}
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
        browser.close()


if __name__ == '__main__': main()
