/** @typedef {import('../types/contracts').SelectedText} SelectedText */
export const MAX_QUOTE = 3000
/** @returns {import('../types/contracts').TextQuestionState} */
export function textQuestionState() {
  return {pendingMain:null, pendingSide:null, open:false, threads:[], active:'', drafts:{},
    run:null, streaming:false, notice:'', error:'', request:0, sourceCard:null, sourceError:''}
}
/** Only read a single opt-in visible text region. Never read inputs or the page body. */
/** @param {Selection|null} selection */
export function selectedPassage(selection) {
  if(!selection || selection.isCollapsed || selection.rangeCount!==1)return null
  /** @param {Node|null} node */
  const element=node=>node?.nodeType===1?/** @type {Element} */(node):node?.parentElement
  const start=/** @type {HTMLElement|null|undefined} */(element(selection.anchorNode)?.closest('[data-quote-region]'))
  const end=element(selection.focusNode)?.closest('[data-quote-region]')
  if(!start||start!==end||start.closest('[inert],input,textarea,[contenteditable="true"]')||!start.getClientRects().length)return null
  const text=selection.toString().trim()
  if(!text)return null
  const source=JSON.parse(start.dataset.quoteSource||'null')
  if(!source)return null
  return {text,source,title:start.dataset.quoteTitle||'所选文字'}
}
/** @param {SelectedText} quote */
export function quoteKey(quote) { return JSON.stringify([quote.project_id,quote.analysis_revision,quote.results_revision,quote.source,quote.text]) }
