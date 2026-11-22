/** API response shapes, mirroring apps/api/app/schemas. */

export type DocumentStatus = 'pending' | 'processing' | 'ready' | 'failed' | 'archived';
export type ProcessingStage = 'extracting' | 'chunking' | 'embedding' | 'entities' | null;
export type SourceKind = 'note' | 'document';
export type WorkspaceRole = 'owner' | 'admin' | 'member' | 'viewer';

export type Workspace = {
  id: string;
  name: string;
  slug: string;
  role: WorkspaceRole;
  owner_id: string;
  personal: boolean;
  member_count: number;
};

export type WorkspaceMember = {
  user_id: string;
  display_name: string | null;
  email: string | null;
  role: WorkspaceRole;
  joined_at: string;
};

export type NoteSummary = {
  id: string;
  kind: 'note';
  title: string;
  status: DocumentStatus;
  processing_stage: ProcessingStage;
  collection_id: string | null;
  summary: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
};

export type NoteDetail = NoteSummary & {
  body: string | null;
  error_message: string | null;
  version: number;
  embedding_model: string | null;
  chunk_count: number;
};

export type DocumentSummary = {
  id: string;
  kind: 'document';
  title: string;
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
  storage_path: string | null;
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
  note_count: number;
  document_count: number;
  item_count: number;
};

export type Tag = {
  id: string;
  name: string;
  note_count: number;
  document_count: number;
  usage_count: number;
};

/** A search hit, always tagged with the table its source id belongs to. */
export type SearchResult = {
  source_kind: SourceKind;
  source_id: string;
  source_title: string;
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

export type KnowledgeLink = {
  id: string;
  relation: string;
  weight: number;
  source: Pick<Entity, 'id' | 'name' | 'entity_type'>;
  target: Pick<Entity, 'id' | 'name' | 'entity_type'>;
};

export type Job = {
  id: string;
  note_id: string | null;
  document_id: string | null;
  target_kind: SourceKind | null;
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

export const ROLE_LABEL: Record<WorkspaceRole, string> = {
  owner: 'Owner',
  admin: 'Admin',
  member: 'Member',
  viewer: 'Viewer'
};

/** Role rank, mirrored from apps/api/app/core/tenancy.py. */
export const ROLE_RANK: Record<WorkspaceRole, number> = {
  viewer: 1,
  member: 2,
  admin: 3,
  owner: 4
};

export function canWrite(role: WorkspaceRole | null | undefined): boolean {
  return role != null && ROLE_RANK[role] >= ROLE_RANK.member;
}

export function canManage(role: WorkspaceRole | null | undefined): boolean {
  return role != null && ROLE_RANK[role] >= ROLE_RANK.admin;
}
