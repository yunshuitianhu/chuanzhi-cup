/** 与后端实际响应核对的核心边界；开放式分析结果逐步收紧，不冒充全站类型化。 */
export type JsonValue = null | boolean | number | string | JsonValue[] | JsonObject
export interface JsonObject { [key: string]: JsonValue }
export type WaterClass = 'I' | 'II' | 'III' | 'IV' | 'V'
export type TerminalStatus = 'succeeded' | 'partial' | 'failed' | 'cancelled'
export type ToolStatus = 'running' | 'succeeded' | 'failed' | 'skipped' | 'cancelled'
export interface AgentStep { id: string; name: string; status: ToolStatus; elapsed_s: number }
export interface ActiveRun {
  run_id: string
  analysis_revision: number
  status: 'running' | 'stopping' | TerminalStatus
  busy: boolean
  auto: boolean
  stage: string
  turn: number
  tool_count: number
  started_at: string
  elapsed_s: number
  stop_requested: boolean
  steps: AgentStep[]
  notice: string
  stop_accepted?: boolean
}
export interface IdleRun { run_id: null; status: 'idle'; busy: false; steps: AgentStep[]; analysis_revision?: never }
export type AgentRun = ActiveRun | IdleRun
interface EventContext { run_id?: string; progress?: ActiveRun }
/** 元组联合使消费者按事件名缩窄 payload，结束状态不能混入 running。 */
export type AgentEventArgs =
  | [event: 'run', payload: EventContext]
  | [event: 'turn', payload: EventContext & { turn: number }]
  | [event: 'delta', payload: EventContext & { text: string }]
  | [event: 'tool', payload: EventContext & { name: string; step_id?: string; args?: string[] }]
  | [event: 'tool_done', payload: EventContext & { name: string; ok: boolean; status: 'succeeded'|'failed'|'skipped'; step_id?: string; chars?: number }]
  | [event: 'done', payload: EventContext & { status: TerminalStatus; turns?: number; tool_errors?: number; message?: string }]
  | [event: 'error', payload: EventContext & { message: string; status?: TerminalStatus }]

export type QuestionContextKind = 'sample' | 'time_range' | 'video_frame' | 'rag_excerpt' | 'text_quote'
export interface QuestionContextScope {
  project_id:string|null; analysis_revision:number; results_revision:number; data_version:string
}
export interface TextSource {kind:'message'|'rag'|'help';message_id?:string;run_id?:string;view?:'markdown'|'raw';retrieval_id?:string;evidence_id?:string;help_id?:string}
export interface SelectedText {text:string;source:TextSource;title:string;project_id:string|null;analysis_revision:number;results_revision:number}
export interface SideRun {run_id:string|null;status:string;busy:boolean;thread_id?:string|null;save_notice?:string;[key:string]:unknown}
export interface SideThread {id:string;quote:QuestionContext;messages:ChatMessage[];requests:string[];updated_at:string;historical?:boolean}
export interface TextQuestionState {pendingMain:SelectedText|null;pendingSide:SelectedText|null;open:boolean;threads:SideThread[];active:string;drafts:Record<string,string>;run:SideRun|null;streaming:boolean;notice:string;error:string;request:number;sourceCard:QuestionContext|null;sourceError:string}
export type QuestionContextSelector =
  | {kind:'text_quote';text:string;source:TextSource}
  | {kind:'sample';sample_id:number}
  | {kind:'time_range';start:string;end:string;indicator:string}
  | {kind:'video_frame';frame_id:number;video_revision:string}
  | {kind:'rag_excerpt';retrieval_id:string;evidence_id:string}
export type QuestionContextRequest = QuestionContextSelector & {
  project_id:string|null;analysis_revision:number;results_revision:number
}
export interface QuestionContext {
  id:string;kind:QuestionContextKind;title:string;summary:string;notice:string
  scope:QuestionContextScope;selection:JsonObject;source_url:string;excerpt?:string
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'tool' | 'error' | 'system'
  content: string
  text_source_id?:string
  run_id?:string
  uiRequest?: string
  incomplete?: boolean
  evidence?: { id: string }
  question_context?: QuestionContext
}
export interface ChatHistory { messages: ChatMessage[]; analysis_revision: number }
export interface Config {
  keys: { deepseek?: boolean; amap?: boolean; starcloud?: boolean }
  target_water_class: WaterClass
  valid_classes: WaterClass[]
}
export interface DataStatus {
  has_data: boolean; rows: number; video_frames: number
  has_black_spots: boolean; black_spot_count: number
  has_cross_validation: boolean; analysis_done: boolean
  // 首次 bootstrap 前本地尚无 revision；请求返回后由后端提供。
  analysis_revision?: number; results_revision?: number
  agent_run?: AgentRun | null
  data_filename?: string; video_filename?: string; data_identity?: JsonObject
  analysis_status?: string; analysis_steps?: Record<string, ToolStatus>
  has_detection?: boolean; target_water_class?: WaterClass; messages?: number
  time_range?: string[]; detection_algorithm_version?: string | null; detection_outdated?: boolean
}
export interface Preview { columns: string[]; rows: JsonObject[]; total: number }
export interface BlackSpots { result: JsonObject; cluster_fields: string[] }
export interface AnalysisResults {
  analysis_revision: number; results_revision: number
  black_spots: BlackSpots | null; cross_validation: JsonObject | null
  comprehensive: JsonObject | null; warnings: string[]
}
export interface RiverForm {
  river_name: string
  n_cross_sections: number
  length_m: number; width_avg_m: number; depth_avg_m: number
  mannings_n: number; longitudinal_slope: number; bed_elevation_avg_m: number
  high_water_mean_m: number; ammonia_nh3_mgL: number; dissolved_oxygen_mgL: number
  bod_cbod_mgL: number; water_temp_C: number; k_nitrification: number; k_cbod_decay: number
  k_sediment_oxygen_demand: number; do_saturation_mgL: number
  upstream_flow_m3s: number; tidal_amplitude_m: number; duration_h: number
  load_kg: number; source_start_h: number; duration_min: number; chainage_m: number
  pollutant_type: 'ammonia' | 'cbod'
  wq_refinement: 1 | 2 | 4 | 8
}
export type GeometryKey = 'river_name' | 'length_m' | 'width_avg_m' | 'depth_avg_m' |
  'mannings_n' | 'longitudinal_slope' | 'bed_elevation_avg_m' | 'ammonia_nh3_mgL' |
  'dissolved_oxygen_mgL' | 'bod_cbod_mgL' | 'water_temp_C' | 'k_nitrification' |
  'k_cbod_decay' | 'k_sediment_oxygen_demand' | 'do_saturation_mgL'
export type RiverConfig = Pick<RiverForm, GeometryKey | 'high_water_mean_m'> & { avg_discharge_m3s: number }
export interface RiverInitRequest { n_cross_sections?: number; mannings_n?: number; auto_detect?: boolean; parameters?: Partial<RiverConfig> }
export interface HydroRequest {
  upstream_flow_m3s?: number; downstream_stage_m?: number; tidal_amplitude_m?: 0
  tidal_period_h?: number; duration_h?: number; dt_s?: number
}
export interface PollutionRequest {
  chainage_m?: number; pollutant_type?: RiverForm['pollutant_type']; load_kg?: number
  duration_min?: number; simulation_hours?: number; source_start_h?: number
  output_interval_s?: number; wq_refinement?: RiverForm['wq_refinement']
}
export interface ModelState {
  config: RiverConfig; cross_section_count: number; initialized: boolean; has_hydro: boolean; has_wq: boolean
  hydro_params: Partial<{ upstream_flow_m3s: number; downstream_stage_m: number; duration_h: number; tidal_amplitude_m: number }>
  wq_params: Partial<Pick<RiverForm, 'load_kg' | 'source_start_h' | 'duration_min' | 'chainage_m' | 'pollutant_type' | 'wq_refinement'>> & { simulation_hours?: number }
}
export interface RiverUI {
  form?: Partial<RiverForm>
  init?: JsonObject; hydro?: JsonObject; pollution?: JsonObject
}
export interface ProjectInfo { id: string; name: string; revision: number; created_at: string; updated_at: string }
export interface ProjectState {
  projects: ProjectInfo[]; current: ProjectInfo | null; river_ui: RiverUI
  model: ModelState | null; restore_error: string
}
export interface SaveProjectRequest { name?: string; river_form?: Partial<RiverForm> | null }
export interface CreateProjectRequest extends SaveProjectRequest { name: string; from_current?: boolean }
export interface CrossValidationRequest { time_offset_sec?: number; time_window_sec?: number }
export interface VideoSettings {
  roi: number[]; roi_confirmed: boolean; roi_note: string; reviewer: string
  sync_confirmed: boolean; sync_note: string; time_offset_sec: number; time_window_sec: number
}
export interface FrameReview { decision:'pending'|'include'|'exclude'; reviewer:string; notes:string; roi:number[]|null; reviewed_at?:string }
export interface VideoFrameQuality {
  id:number; time_sec:number|null; roi:number[]; roi_confirmed:boolean; review:FrameReview
  eligible:boolean; quality_status:'invalid'|'needs_review'|'usable'; quality_flags:string[]; quality_messages:string[]
  features:{laplacian_var:number;mean_saturation:number;mean_brightness:number;contrast_std:number;dark_fraction:number;glare_fraction:number}|null
  dark_color_cue:boolean|null; width:number|null; height:number|null
}
export interface VideoQualityResponse {
  version:string;revision:string;settings:VideoSettings;notice:string;rows:VideoFrameQuality[]
  summary:{frames:number;eligible:number;invalid:number;needs_review:number;manual_excluded:number}
  total:number;offset:number;limit:number
}
export interface VideoFrameResponse { revision:string;frame:VideoFrameQuality;image_base64:string;media_type:'image/jpeg' }
export interface VideoSettingsChange { expected_revision:string;settings:VideoSettings;frame_id?:number;review?:FrameReview }
export interface QualityConfig {
  max_gap_s: number; flat_duration_s: number; flat_min_points: number
  spike_thresholds: { ammonia_nitrogen: number; dissolved_oxygen: number }
  flat_tolerances: { ammonia_nitrogen: number; dissolved_oxygen: number }
}
export interface InvestigationResult { task_id: string; board: JsonObject; refresh_warning?: string }
export interface SessionState {
  ready: boolean; connecting: boolean; bootstrapStage: string; loading: boolean; error: string
  chatMessages: ChatMessage[]; chatRunning: boolean; chatStreaming: boolean
  sideRunning:boolean; textQuestions:TextQuestionState
  chatDraft:string; questionContext:QuestionContext|null; questionContextNotice:string; questionContextRequest:number
  agentRun: AgentRun | null; agentRunAt: number; agentRunRequest: number; agentActiveId: string | null
  agentConnectionError: string; workspaceRevision: number; switchingProject: boolean
  projectState: ProjectState; riverForm: Partial<RiverForm> | null
  config: Config; status: DataStatus; preview: Preview
  blackSpots: BlackSpots | null; crossValidation: JsonObject | null; comprehensive: JsonObject | null
  resultsWarnings: string[]; statusRequest: number; resultsRequest: number; projectRequest: number; previewRequest: number
  investigationRevision: number; selectedInvestigationId: string
}
