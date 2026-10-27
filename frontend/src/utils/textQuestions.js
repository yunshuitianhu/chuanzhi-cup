/** @typedef {import('../types/contracts').SelectedText} SelectedText */
export const MAX_QUOTE = 3000
/** @returns {import('../types/contracts').TextQuestionState} */
export function textQuestionState() {
  return {pendingMain:null, pendingSide:null, open:false, threads:[], active:'', drafts:{},
    run:null, streaming:false, notice:'', error:'', request:0, sourceCard:null, sourceError:''}
}
/** Read the selected text only. Registered sources keep their strict provenance.
 * Otherwise an explicit application route enables a browser-provided page quotation.
 * @param {Selection|null} selection
 * @param {{path:string,title?:string}|null} [page]
 */
export function selectedPassage(selection,page=null) {
  if(!selection || selection.isCollapsed || selection.rangeCount!==1)return null
  /** @param {Node|null} node */
  const element=node=>node?.nodeType===1?/** @type {Element} */(node):node?.parentElement
  const first=element(selection.anchorNode),last=element(selection.focusNode)
  if(!first||!last||first.isConnected===false||last.isConnected===false)return null
  const excluded='input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[inert],[data-no-quote],.selection-menu'
  if(first.closest(excluded)||last.closest(excluded)||!first.getClientRects().length||!last.getClientRects().length)return null
  // A drag crossing a form must not silently include editing content either.
  const range=selection.getRangeAt?.(0)
  const common=range&&element(range.commonAncestorContainer)
  if(common&&typeof range.intersectsNode==='function'){
    for(const node of common.querySelectorAll(excluded)){
      try{if(range.intersectsNode(node))return null}catch{return null}
    }
  }
  const start=/** @type {HTMLElement|null} */(first.closest('[data-quote-region]'))
  const end=last.closest('[data-quote-region]')
  const text=selection.toString().trim()
  if(!text)return null
  if(start&&start===end){
    if(start.closest(excluded)||!start.getClientRects().length)return null
    const source=JSON.parse(start.dataset.quoteSource||'null')
    if(!source)return null
    return {text,source,title:start.dataset.quoteTitle||'所选文字'}
  }
  if(!page||!['/chat','/charts','/river','/tech'].includes(page.path))return null
  /** @param {Element} node */
  const inApp=node=>node.closest('#app,.side-questions,.question-context')
  if(!inApp(first)||!inApp(last))return null
  let region=first.closest('[id]')
  while(region&&!region.contains(last))region=region.parentElement?.closest('[id]')||null
  const anchor=region&&/^[A-Za-z][A-Za-z0-9_:-]{0,99}$/.test(region.id)?region.id:'main-content'
  return {text,source:{kind:'page',path:page.path,anchor},title:(page.title||'当前页面')+' · 页面选文'}
}
/** @param {unknown} value */
function sourceIdentity(value) {
  if(!value||typeof value!=='object'||Array.isArray(value))return null
  const source=/** @type {Record<string,unknown>} */(value)
  if(source.kind==='message')return ['message',source.message_id||null,source.message_id?null:source.run_id||null,source.view||'raw']
  if(source.kind==='rag')return ['rag',source.retrieval_id,source.evidence_id]
  if(source.kind==='help')return ['help',source.help_id]
  if(source.kind==='page')return ['page',source.path,source.anchor||'main-content']
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
