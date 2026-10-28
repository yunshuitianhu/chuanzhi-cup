import test from 'node:test'
import assert from 'node:assert/strict'
import { keyRows, keyTag } from '../src/utils/keyState.js'

test('未配置、已填写未验证和已验证分开显示', () => {
  const rows = keyRows(
    { deepseek: true, amap: false, starcloud: true },
    { deepseek: { configured: true, verified: false }, starcloud: { configured: true, verified: true } },
  )
  assert.deepEqual(rows.map((row) => row.key), ['deepseek', 'amap', 'starcloud'])
  assert.equal(keyTag(rows[0]), '已填写（未验证）')
  assert.equal(keyTag(rows[1]), '未配置（可选）')
  assert.equal(keyTag(rows[2]), '本次已验证')
  assert.equal(rows[0].verified, false)
  assert.equal(rows[2].verified, true)
})

test('AI 功能所需密钥缺失时说明需要配置', () => {
  const rows = keyRows({}, {})
  assert.equal(keyTag(rows[0]), '未配置（AI功能需要）')
  assert.equal(rows[0].required, true)
})

test('notice 只透传字符串，缺失时为空', () => {
  const rows = keyRows({ deepseek: true }, {
    deepseek: { configured: true, verified: false, notice: '已填写，尚未验证' },
    amap: { notice: null },
  })
  assert.equal(rows[0].notice, '已填写，尚未验证')
  assert.equal(rows[1].notice, '')
})
