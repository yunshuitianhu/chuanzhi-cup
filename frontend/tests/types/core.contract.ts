// 编译期反例：删除约束或让类型退化成 any，会出现 unused @ts-expect-error 并使 CI 失败。
import { api } from '../../src/api/client.js'
import { useSessionStore } from '../../src/stores/session.js'
import { consumeAgentStream } from '../../src/utils/agentStream.js'
import type { AgentEventArgs, RiverForm } from '../../src/types/contracts'

async function verifyContracts() {
  const state = await api.projects()
  // @ts-expect-error current 允许 null，须先检查
  state.current.id
  state.current?.id.toUpperCase()
  // @ts-expect-error 后端使用 revision，不能拼成 version
  state.current?.version
  const exported: Blob = await api.exportProject('saved-id', null)
  void exported
  // @ts-expect-error 项目导出是 Blob，不能按 JSON 对象使用
  const invalidExport: { project: string } = await api.exportProject('id', null)
  void invalidExport
  // @ts-expect-error 数据条数是数字
  const rows: string = (await api.status()).rows
  void rows
  // @ts-expect-error 水质等级只接受 I~V
  api.setTargetClass('VI')
  // @ts-expect-error 新建项目必须有名称
  api.createProject({ from_current: false })
  // @ts-expect-error 当前Web模型仅支持恒定流
  api.riverHydro({tidal_amplitude_m: 1})
  // @ts-expect-error DO是耦合状态，不能作为当前排放类型
  api.riverPollution({pollutant_type:'do'})
  // @ts-expect-error 网格细分数只接受 1/2/4/8
  const draft: Partial<RiverForm> = { wq_refinement: 3 }
  void draft
  const store = useSessionStore()
  store.riverForm = { dissolved_oxygen_mgL: 0 }
  // @ts-expect-error 后端布尔字段不可赋字符串
  store.status.has_data = 'false'
  // @ts-expect-error 任务状态拼写错误不能混入状态存储
  store.adoptAgentRun({run_id:null,status:'done',busy:false,steps:[]})
  // @ts-expect-error 执行包装器必须保留返回值类型
  const result: string = await store._run(async () => 123)
  void result
}
void verifyContracts // 仅编译，不访问真实网络或浏览器。

// @ts-expect-error done 不接受 running
const invalidEnd: AgentEventArgs = ['done', {status:'running'}]
void invalidEnd
declare const body: ReadableStream<Uint8Array>
function verifyStreamTypes() {
  consumeAgentStream(body, (...[event,payload]) => {
    if (event === 'delta') {
      payload.text.toUpperCase()
      // @ts-expect-error delta 没有完成状态
      payload.status
    }
    if (event === 'done') {
      const end: 'succeeded'|'partial'|'failed'|'cancelled' = payload.status
      void end
    }
  })
}
void verifyStreamTypes
