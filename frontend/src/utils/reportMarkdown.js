import MarkdownIt from 'markdown-it'

// The parser owns all generated markup. No raw HTML, image loads or HTML plugins.
const markdown = new MarkdownIt({ html: false, linkify: false, breaks: true, maxNesting: 20 })
markdown.disable('image')
const defaultValidateLink = markdown.validateLink.bind(markdown)
/** @param {string} url */
markdown.validateLink = (url) => defaultValidateLink(url)
  && (!/^[a-z][a-z\d+.-]*:/i.test(url) || /^(https?|mailto):/i.test(url))
/** @type {import('markdown-it').RendererRule} */
const renderLink = (tokens, index, options, _env, renderer) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return renderer.renderToken(tokens, index, options)
}
markdown.renderer.rules.link_open = renderLink
markdown.renderer.rules.table_open = () => '<div class="report-table" role="region" aria-label="报告表格，可横向滚动" tabindex="0"><table>\n'
markdown.renderer.rules.table_close = () => '</table></div>\n'

export function renderReportMarkdown(content = '') {
  const text = String(content)
  // Keep the full source visible for unusually long inputs without an expensive parse.
  if (text.length > 200_000) return `<pre class="report-plain">${markdown.utils.escapeHtml(text)}</pre>`
  return markdown.render(text)
}
