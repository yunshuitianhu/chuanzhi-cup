import {computed} from 'vue'
export function toSimpleSummary(store){
  const s=store.status,detected=!!s.has_data&&!!s.has_detection&&!s.detection_outdated
  return {hasData:!!s.has_data,records:Number(s.rows||0),hasDetection:detected,
    warningPoints:detected?Number(s.black_spot_count||0):null,
    clusters:detected?(store.blackSpots?.result?.['聚类热点数']??store.blackSpots?.result?.['聚类详情']?.length??null):null,
    identity:s.data_identity?.kind==='synthetic'?'仿真示例':'来源待核验',
    detection:s.detection_outdated?'stale':store.loading?'loading':detected?'ready':'idle',
    ai:store.chatRunning?'loading':s.analysis_done?'ready':s.analysis_status==='partial'?'partial':s.analysis_status==='failed'?'failed':'idle'}
}
export const useSimpleWorkflow=store=>computed(()=>toSimpleSummary(store))
