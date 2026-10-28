import test from 'node:test'
import assert from 'node:assert/strict'
import { ragStageText } from '../src/utils/ragStage.js'

test('RAG 阶段显示为依赖、索引、模型和检索，不把索引可读当成检索成功', () => {
  assert.equal(ragStageText('dependencies'), '依赖检查')
  assert.equal(ragStageText('index_read'), '索引读取')
  assert.equal(ragStageText('index_build'), '索引构建')
  assert.equal(ragStageText('model_load'), '模型加载')
  assert.equal(ragStageText('retrieval'), '实际检索')
})

test('未知或旧接口缺少阶段时显示待确认', () => {
  assert.equal(ragStageText(undefined), '状态待确认')
  assert.equal(ragStageText('future_stage'), '状态待确认')
})
