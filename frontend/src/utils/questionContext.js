/** @typedef {import('../types/contracts').QuestionContext} QuestionContext */
/** @param {unknown} value @returns {value is QuestionContext} */
export function isQuestionContext(value) {
  if (!value || typeof value !== 'object') return false
  const item=/** @type {Record<string,any>} */(value), scope=item.scope
  return typeof item.id==='string' && /^[a-f0-9]{32}$/.test(item.id)
    && ['sample','time_range','video_frame','rag_excerpt','text_quote','image_asset'].includes(item.kind)
    && typeof item.title==='string' && item.title.length<=1000
    && typeof item.summary==='string' && item.summary.length<=5000
    && typeof item.notice==='string' && item.notice.length<=5000
    && typeof item.source_url==='string'
    && !!item.selection && typeof item.selection==='object' && !Array.isArray(item.selection)
    && !!scope && typeof scope==='object'
    && (scope.project_id===null || typeof scope.project_id==='string')
    && Number.isInteger(scope.analysis_revision) && scope.analysis_revision>=0
    && Number.isInteger(scope.results_revision) && scope.results_revision>=0
    && typeof scope.data_version==='string' && /^[a-f0-9]{64}$/.test(scope.data_version)
    && (item.excerpt===undefined || (typeof item.excerpt==='string' && item.excerpt.length<=10000))
}
/** @param {QuestionContext} item @param {Pick<import('../types/contracts').SessionState,'status'|'projectState'>} state */
export function questionContextMatches(item,state) {
  return item.scope.project_id===(state.projectState.current?.id??null)
    && item.scope.analysis_revision===state.status.analysis_revision
    && item.scope.results_revision===(state.status.results_revision??0)
}
/** Rebuild local navigation; imported message metadata never supplies a URL to execute.
 * @param {unknown} item */
export function questionContextLocation(item) {
  if (!isQuestionContext(item)) return null
  if(item.kind==='text_quote') {
    const source=item.selection.source
    if(!source||typeof source!=='object'||Array.isArray(source))return null
    const src=source
    if(src.kind==='page') {
      if(typeof src.path!=='string'||!['/chat','/charts','/river','/tech'].includes(src.path))return null
      const anchor=typeof src.anchor==='string'&&/^[A-Za-z][A-Za-z0-9_:-]{0,99}$/.test(src.anchor)?src.anchor:'main-content'
      return {path:src.path,query:{text_context:item.id},hash:'#'+anchor}
    }
    const mid=typeof src.message_id==='string'?src.message_id:''
    const hid=typeof src.help_id==='string'?src.help_id:''
    const anchor=src.kind==='message'&&/^[a-f0-9]{64}$/.test(mid)?'message-'+mid:src.kind==='help'&&['rag_help','screening_help','model_help','grid_help'].includes(hid)?hid:'text-source-return'
    return {path:src.kind==='message'?'/chat':'/tech',query:{text_context:item.id},hash:'#'+anchor}
  }
  if(item.kind==='image_asset')return {path:'/chat',query:{context_id:item.id},hash:'#image-asset'}
  const hashes={sample:'#trace-map',time_range:'#time-series',video_frame:'#video-quality',rag_excerpt:'#rag-search'}
  return {path:item.kind==='rag_excerpt'?'/tech':'/charts',query:{context_id:item.id},hash:hashes[item.kind]}
}
/** @param {import('../types/contracts').QuestionContextKind} kind */
export function contextQuestion(kind) {
  return {text_quote:'请解释所选文字的含义、依据和适用限制。',sample:'请分析这个采样点，说明观测依据、缺口和下一步复测建议。',
    time_range:'请分析所选时段的变化，区分真实观测、缺测和推测。',
    video_frame:'请先说明这帧画面实际能看到什么，再结合记录解释；看不清或不能判断的地方直接说明。',
    image_asset:'请先说明这张图片里实际能看到什么，再结合当前记录解释；看不清或不能判断的地方直接说明。',
    rag_excerpt:'请解释所选资料片段的含义、适用条件与证据限制。'}[kind]
}
