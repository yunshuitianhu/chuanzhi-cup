import type {JsonObject} from './contracts'

/**
 * Client types for backend/analysis_cards.py.
 * GET  /api/agent/evidence/{bundle_id}/cards
 * POST /api/agent/evidence/{bundle_id}/cards/{card_id}/preview
 * POST /api/agent/evidence/{bundle_id}/cards/{card_id}/confirm
 *
 * ResolvedRef.excerpt / label / notice are plain untrusted display text.
 * source_url is server built (internal route) or null; never model supplied.
 */
export type AnalysisCardsStatus = 'ready' | 'unavailable' | 'stale' | 'invalid'
export type AnalysisCardRefKind = 'fact' | 'record' | 'rag' | 'tool' | 'video_frame'

export interface ResolvedRef {
  kind: AnalysisCardRefKind
  label: string
  fact_id?: string
  position?: number
  map_sample_id?: number
  evidence_id?: string
  tool_id?: string
  frame_id?: number
  video_revision?: string
  excerpt: string
  source_url: string | null
  notice: string
}

export interface AnalysisCardRetestSuggestion {
  location: string
  latitude: null
  longitude: null
  indicators: string[]
  plan: string
}

export interface AnalysisCardSuggestion {
  title: string
  hypothesis: string
  supporting_notes: string
  opposing_notes: string
  retest: AnalysisCardRetestSuggestion
}

export interface AnalysisCard {
  id: string
  confirmed_task_id?: string | null
  title: string
  finding: string
  support: ResolvedRef[]
  oppose: ResolvedRef[]
  gaps: string[]
  opposing_evidence_status: 'provided' | 'not_provided'
  suggestion: AnalysisCardSuggestion
}

export interface AnalysisCardsResponse {
  version: 'analysis-cards-v1'
  status: AnalysisCardsStatus
  notice: string
  cards: AnalysisCard[]
}

export type InvestigationStatus = 'open' | 'planned' | 'sampled' | 'reviewed' | 'closed'

export interface InvestigationRetest {
  location: string
  latitude: number | null
  longitude: number | null
  indicators: string[]
  plan: string
  observed_at: string
  result: string
}

export interface InvestigationReview {
  outcome: string
  reviewer: string
  notes: string
}

export interface InvestigationDraft {
  title: string
  hypothesis: string
  supporting_notes: string
  opposing_notes: string
  status: InvestigationStatus
  retest: InvestigationRetest
  review: InvestigationReview
}

export interface AnalysisCardPreviewResponse {
  preview_id: string
  bundle_id: string
  card_id: string
  scope_id: string
  expected_revision: number
  draft: InvestigationDraft
  notice: string
}

export interface AnalysisCardConfirmRequest {
  preview_id: string
  request_id: string
  draft: InvestigationDraft
}

export interface AnalysisCardConfirmResponse {
  task_id: string
  board?: JsonObject
  refresh_warning?: string
  bundle_id?: string
  card_id?: string
  preview_id?: string
  request_id?: string
  idempotent?: boolean
  persisted_project?: boolean
  notice?: string
}
