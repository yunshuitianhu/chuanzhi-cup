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
/** @param {unknown} value */
function sourceIdentity(value) {
  if(!value||typeof value!=='object'||Array.isArray(value))return null
  const source=/** @type {Record<string,unknown>} */(value)
  if(source.kind==='message')return ['message',source.message_id||null,source.message_id?null:source.run_id||null,source.view||'raw']
  if(source.kind==='rag')return ['rag',source.retrieval_id,source.evidence_id]
  if(source.kind==='help')return ['help',source.help_id]
  return null
}
/** @param {SelectedText} quote */
export function quoteKey(quote) { return JSON.stringify([quote.project_id,quote.analysis_revision,quote.results_revision,sourceIdentity(quote.source),quote.text]) }
/** @param {SelectedText} selected @param {import('../types/contracts').QuestionContext} card */
export function matchesTextQuote(selected,card) {
  return card.kind==='text_quote'&&selected.text===card.selection.text
    &&selected.project_id===card.scope.project_id&&selected.analysis_revision===card.scope.analysis_revision
    &&selected.results_revision===card.scope.results_revision
    &&JSON.stringify(sourceIdentity(selected.source))===JSON.stringify(sourceIdentity(card.selection.source))
}
