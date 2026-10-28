/**
 * 外部服务密钥状态：区分未配置、已填写未验证、已验证。
 *
 * 配置存在只说明 .env 里填了值，不代表本进程成功调用过外部服务；
 * 实际成功/失败以每次请求的结果为准。notice 来自 /api/config 的 key_state。
 */
export const KEY_ROWS = [
  { name: 'DeepSeek', key: 'deepseek', required: true },
  { name: '高德地图', key: 'amap', required: false },
  { name: '星图地球', key: 'starcloud', required: false },
]

/**
 * @param {Record<string,boolean>} [keys]
 * @param {Record<string,{configured?:boolean,verified?:boolean,notice?:string}>} [keyState]
 */
export function keyRows(keys = {}, keyState = {}) {
  return KEY_ROWS.map((row) => {
    const state = keyState[row.key] || {}
    const ok = !!keys[row.key]
    return {
      ...row,
      ok,
      verified: ok && state.verified === true,
      notice: typeof state.notice === 'string' ? state.notice : '',
    }
  })
}

/**
 * @param {{ok:boolean,verified?:boolean,required?:boolean}} row
 */
export function keyTag(row) {
  if (!row.ok) return row.required ? '未配置（AI功能需要）' : '未配置（可选）'
  return row.verified ? '本次已验证' : '已填写（未验证）'
}
