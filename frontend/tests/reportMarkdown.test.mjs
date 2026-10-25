import test from 'node:test'
import assert from 'node:assert/strict'
import { renderReportMarkdown } from '../src/utils/reportMarkdown.js'

test('报告按标题、列表、表格排版，零值和证据ID保留', () => {
  const html=renderReportMarkdown('## 筛查结论\n\n**仅供复核**\n\n- DO 为 0 mg/L\n\n|指标|值|\n|---|---|\n|DO|0|\n\n[证据ID:黑臭水体\\案例.md_1]')
  for(const pattern of [/<h2>筛查结论<\/h2>/,/<strong>仅供复核<\/strong>/,/<li>DO 为 0 mg\/L<\/li>/,/<td>0<\/td>/,/证据ID:黑臭水体\\案例.md_1/,/tabindex="0"/])assert.match(html,pattern)
})

test('HTML、SVG、事件属性与围栏代码只能作为文本', () => {
  const html=renderReportMarkdown('<script>alert(1)</script>\n\n<svg onload="alert(2)"></svg>\n\n<img src=x onerror="alert(3)">\n\n```html\n<iframe srcdoc="<script>bad</script>"></iframe>\n```')
  assert.doesNotMatch(html,/<(?:script|svg|img|iframe)\b/i)
  for(const tag of ['script','svg','img','iframe']) assert.match(html,new RegExp('&lt;'+tag))
})

for(const url of ['javascript:alert%281%29','JaVaScRiPt:alert%281%29','javascript&#58;alert%281%29','data:text/html,test','file:///C:/private.env','vbscript:msgbox%281%29','blob:https://example.com/test']) {
  test(`危险链接不会成为可点击地址：${url}`, () => assert.doesNotMatch(renderReportMarkdown(`[访问](${url})`),/<a\b/))
}

test('远程图像不加载，安全参考链接隔离新窗口', () => {
  const html=renderReportMarkdown('![资料图](https://example.com/track.png)\n\n[原文](https://example.com/source?q=1&b=2)')
  assert.doesNotMatch(html,/<img\b|<iframe\b/i)
  assert.match(html,/href="https:\/\/example.com\/source\?q=1&amp;b=2"/)
  assert.match(html,/target="_blank"/);assert.match(html,/rel="noopener noreferrer"/)
})

test('不完整流式Markdown可排版且超长输入保留全文为转义文本', () => {
  assert.match(renderReportMarkdown('## 未完成\n\n**当前值为0'),/当前值为0/)
  const text='x'.repeat(200_001)+'<img src=x onerror=alert(1)>末尾记录'
  const html=renderReportMarkdown(text)
  assert.match(html,/<pre class="report-plain">/);assert.match(html,/&lt;img/)
  assert.match(html,/末尾记录<\/pre>$/);assert.doesNotMatch(html,/<img\b/)
})
