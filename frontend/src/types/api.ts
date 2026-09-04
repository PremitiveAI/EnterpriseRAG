/** Response contracts. Mirrors docs/api/overview.md. */

export interface ApiSuccess<T> {
  success: true;
  data: T;
  message?: string;
}

export interface ApiError {
  success: false;
  error_code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id?: string;
}

export type ApiResponse<T> = ApiSuccess<T> | ApiError;

export interface UploadLimits {
  max_document_size_mb: number;
  max_image_size_mb: number;
  max_files_per_batch: number;
  document_extensions: string[];
  image_extensions: string[];
  legacy_extensions: string[];
}

export type UploadOutcome = "QUEUED" | "DUPLICATE" | "REJECTED";

export interface FileResult {
  file_name: string;
  status: UploadOutcome;
  document_id?: string | null;
  task_id?: string | null;
  error_code?: string | null;
  message?: string | null;
  duplicate_of?: { document_id: string; file_name: string } | null;
}

export interface UploadSummary {
  accepted: number;
  duplicates: number;
  rejected: number;
  results: FileResult[];
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
}

// --- Document management (§28-§30) --------------------------------------- //

export interface CategoryRef {
  id: string;
  slug: string;
  name: string;
}

export interface DocumentSummary {
  id: string;
  file_name: string;
  title: string | null;
  file_type: string;
  file_size: number;
  document_type: string | null;
  category: CategoryRef | null;
  status: string;
  language: string | null;
  page_count: number | null;
  chunk_count: number;
  tags: string[];
  is_possible_duplicate: boolean;
  /**
   * Reachable by the organization's public chatbot. The backend has always
   * returned this; nothing rendered it, so an admin had no way to see - let
   * alone change - which documents strangers could ask about.
   */
  is_public: boolean;
  error_code: string | null;
  created_at: string;
  deleted_at: string | null;
}

export interface ProcessingInfo {
  current_stage: string | null;
  error_code: string | null;
  error_message: string | null;
  retry_count: number;
  duration_ms: number | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface DocumentDetail extends DocumentSummary {
  description: string | null;
  mime_type: string;
  original_file_name: string;
  duplicate_of: string | null;
  processing: ProcessingInfo | null;
  updated_at: string | null;
}

export interface DocumentListPage {
  items: DocumentSummary[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface FilterOptions {
  categories: CategoryRef[];
  statuses: string[];
  document_types: string[];
  languages: string[];
}

export interface DocumentStatusSnapshot {
  document_id: string;
  status: string;
  current_stage: string | null;
  progress_percent: number;
  retry_count: number;
  error_code: string | null;
  error_message: string | null;
  is_terminal: boolean;
  duplicate_of?: { document_id: string; file_name: string } | null;
}

// --- Chat (§31-§36) ------------------------------------------------------ //

export interface ConversationSummary {
  id: string;
  title: string;
  message_count: number;
  last_message_at: string | null;
  created_at: string;
}

export interface SourceRef {
  document_id: string;
  document_name: string;
  chunk_id: string | null;
  page: number | null;
  section?: string | null;
  score: number;
  rank: number;
  document_deleted: boolean;
}

export interface ChatMessage {
  id: string;
  role: string;
  content: string;
  is_grounded: boolean | null;
  error_code: string | null;
  latency_ms: number | null;
  created_at: string;
  sources: SourceRef[];
}

export interface ConversationDetail {
  id: string;
  title: string;
  message_count: number;
  last_message_at: string | null;
  created_at: string;
  messages: ChatMessage[];
}

export interface AnswerResponse {
  message_id: string;
  conversation_id: string;
  answer: string;
  is_grounded: boolean;
  sources: SourceRef[];
  latency_ms: number;
  error_code: string | null;
  degraded: boolean;
}

// --- Release 2: organizations -------------------------------------------- //

export interface OrganizationCounts {
  admins: number;
  documents: number;
  public_documents: number;
  conversations: number;
}

export interface OrganizationLimits {
  max_documents: number | null;
  max_document_size_mb: number | null;
  rate_limit_chat: string | null;
  rate_limit_upload: string | null;
  rate_limit_public_chat: string | null;
}

export interface OrganizationSummary {
  id: string;
  name: string;
  slug: string;
  status: "ACTIVE" | "SUSPENDED" | "DELETED";
  contact_email: string | null;
  public_chat_enabled: boolean;
  counts: OrganizationCounts;
  created_at: string;
}

export interface OrganizationDetail extends OrganizationSummary {
  limits: OrganizationLimits;
  /** The embed snippet keys on this, not the slug, so a rename never breaks a
   *  live widget. */
  public_chat_key: string;
  public_chat_greeting: string | null;
  updated_at: string | null;
}

export interface OrganizationListPage {
  items: OrganizationSummary[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface OrganizationAdmin {
  id: string;
  full_name: string;
  email: string;
  mobile: string | null;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

// --- Public chatbot ------------------------------------------------------- //

export interface PublicConfig {
  organization_name: string;
  greeting: string;
}

/**
 * Sources carry a name and a page, never a document_id. A visitor cannot be
 * given a handle they could use against an authenticated endpoint.
 */
export interface PublicSource {
  document_name: string;
  page: number | null;
}

export interface PublicAnswer {
  answer: string;
  is_grounded: boolean;
  session_id: string;
  sources: PublicSource[];
}

// --- Category master (Super Admin) ---------------------------------------- //

export interface Category {
  id: string;
  /** Immutable after creation: the classifier returns it and Qdrant stores it. */
  slug: string;
  name: string;
  description: string | null;
  sort_order: number;
  is_active: boolean;
  /** Across every organization. A number, never a title or an owner. */
  document_count: number;
  created_at: string;
  updated_at: string | null;
}

export interface CategoryListPage {
  items: Category[];
}

/** A delete can come back as a deactivation, so the caller is told which. */
export interface CategoryDeleteResult {
  deleted: boolean;
  deactivated: boolean;
  document_count: number;
}

// --- Agent rules (Super Admin) -------------------------------------------- //

export interface AgentRuleSummary {
  agent_key: string;
  name: string;
  role: string;
  /** False means the shipped prompt is running — not that something is missing. */
  is_custom: boolean;
}

export interface AgentRuleListPage {
  items: AgentRuleSummary[];
}

// --- LLM providers (Super Admin, ADR-010) --------------------------------- //

export type LLMProviderName = "gemini" | "openai" | "anthropic" | "azure";

/**
 * Note what is absent: there is no field for the API key, and there is no
 * optional one either. The backend's response model has nowhere to put a
 * credential, so no screen can accidentally render one.
 */
export interface LLMProvider {
  id: string;
  provider_name: LLMProviderName;
  /** Bare id — "gpt-4o", never "openai/gpt-4o". The prefix is composed once,
   *  server-side, at call time. */
  model_name: string;
  /** sha256 of the key, truncated. Enough to answer "is this the same
   *  credential as before?" without anything decrypting one. */
  key_fingerprint: string;
  encryption_key_id: number;
  base_url: string | null;
  config: Record<string, unknown>;
  is_active: boolean;
  /** Monotonic across all rows. A change here is what makes every process
   *  rebuild its client, with no restart. */
  config_version: number;
  last_tested_at: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface LLMProviderListPage {
  items: LLMProvider[];
}

export interface AgentRuleDetail extends AgentRuleSummary {
  /** "" when no rule is set. The editor opens empty and the default runs. */
  content: string;
  default_content: string;
  /**
   * Appended by the backend on every call and unreachable from this API. Shown
   * read-only: a Super Admin who cannot see the part they may not change will
   * write their own output contract and wonder why nothing takes effect.
   */
  locked_text: string;
}
