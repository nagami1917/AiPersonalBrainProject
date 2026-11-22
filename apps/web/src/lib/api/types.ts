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

export type NoteBacklink = {
  id: string;
  title: string;
  status: DocumentStatus;
  updated_at: string;
};

/** A link written in a note body. `resolved: false` means the target is unwritten. */
export type NoteOutgoingLink = {
  target_title: string;
  target_id: string | null;
  resolved: boolean;
  status: DocumentStatus | null;
};

export type NoteDetail = NoteSummary & {
  content: string | null;
  content_format: 'markdown' | 'plain';
  error_message: string | null;
  version: number;
  embedding_model: string | null;
  chunk_count: number;
  backlinks: NoteBacklink[];
  outgoing_links: NoteOutgoingLink[];
};

export type DocumentSummary = {
  id: string;
  kind: 'document';
  title: string;
  filename: string;
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
  file_path: string | null;
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

/** A node as returned by /graph and /graph/entities/{id}/neighbors. */
export type GraphNode = {
  id: string;
  name: string;
  entity_type: string;
  description: string | null;
  mention_count: number;
};

/**
 * An edge as returned by the neighbourhood endpoint. Note the field names are
 * `source`/`target` (not `source_id`), matching that endpoint's shape; the flat
 * `/graph` payload uses `source_id`/`target_id`.
 */
export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  relation: string;
  weight: number;
};

export type GraphSlice = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  seed_id: string;
  depth: number;
};

/** The full entity payload from /graph/entities/{id}. */
export type EntityDetail = Entity & {
  links: {
    id: string;
    relation: string;
    weight: number;
    direction: 'in' | 'out';
    entity: Pick<Entity, 'id' | 'name' | 'entity_type'>;
  }[];
  mentions: {
    chunk_id: string;
    confidence: number;
    source_kind: SourceKind;
    source_id: string;
    source_title: string;
  }[];
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
  type: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'dead' | 'superseded';
  attempts: number;
  max_attempts: number;
  error_message: string | null;
  completed_at: string | null;
};

/** The Welcome page's stat row. One request, so the numbers agree with each other. */
export type WorkspaceStats = {
  workspace_id: string;
  notes: number;
  notes_ready: number;
  documents: number;
  documents_ready: number;
  documents_processing: number;
  documents_failed: number;
  collections: number;
  tags: number;
  entities: number;
  links: number;
  chunks: number;
  conversations: number;
  note_links: number;
};

/** A pair of notes that are related but not yet linked in either direction. */
export type SuggestedConnection = {
  source: { id: string; title: string };
  target: { id: string; title: string };
  shared_tags: string[];
  reason: string;
  similarity: number | null;
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
