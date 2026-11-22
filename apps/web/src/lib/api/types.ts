/** API response shapes, mirroring apps/api/app/schemas. */

export type DocumentStatus = 'pending' | 'processing' | 'ready' | 'failed' | 'archived';
export type ProcessingStage = 'extracting' | 'chunking' | 'embedding' | 'entities' | null;

export type DocumentSummary = {
  id: string;
  title: string;
  source_type: 'upload' | 'note' | 'conversation';
  status: DocumentStatus;
  processing_stage: ProcessingStage;
  collection_id: string | null;
  mime_type: string | null;
  file_size: number | null;
  page_count: number | null;
  summary: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
};

export type DocumentDetail = DocumentSummary & {
  raw_text: string | null;
  error_message: string | null;
  version: number;
  embedding_model: string | null;
  chunk_count: number;
};

export type Collection = {
  id: string;
  name: string;
  parent_id: string | null;
  description: string | null;
  color: string | null;
  document_count: number;
};

export type Tag = { id: string; name: string; document_count: number };

export type SearchResult = {
  document_id: string;
  document_title: string;
  chunk_id: string;
  snippet: string;
  heading_path: string | null;
  page: number | null;
  score: number;
  matched_arm: 'vector' | 'keyword' | 'both';
};

export type Entity = {
  id: string;
  name: string;
  entity_type: string;
  description: string | null;
  mention_count: number;
  last_seen_at: string | null;
};

export type Job = {
  id: string;
  document_id: string | null;
  job_type: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'dead' | 'superseded';
  attempts: number;
  max_attempts: number;
  last_error: string | null;
};

export type Meta = {
  version: string;
  supported_mime_types: string[];
  max_upload_bytes: number;
  embedding_model: string;
  embedding_dim: number;
  llm_model: string;
  retrieval_k: number;
};

export const STAGE_LABEL: Record<Exclude<ProcessingStage, null>, string> = {
  extracting: 'Reading text',
  chunking: 'Splitting into passages',
  embedding: 'Making searchable',
  entities: 'Finding connections'
};

export const STATUS_LABEL: Record<DocumentStatus, string> = {
  pending: 'Queued',
  processing: 'Processing',
  ready: 'Ready',
  failed: 'Failed',
  archived: 'Archived'
};
