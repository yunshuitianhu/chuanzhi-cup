/**
 * 本地 RAG 的阶段名称。
 *
 * 后端 stage 区分依赖、索引读取、索引构建、模型加载和实际检索；
 * 前端只负责把阶段说清楚，不把“索引可读”当成“检索已成功”。
 */
export const RAG_STAGE_TEXT = {
  dependencies: '依赖检查',
  index_read: '索引读取',
  index_build: '索引构建',
  model_load: '模型加载',
  retrieval: '实际检索',
}

/**
 * @param {string} [stage]
 */
export function ragStageText(stage) {
  return RAG_STAGE_TEXT[stage] || '状态待确认'
}
