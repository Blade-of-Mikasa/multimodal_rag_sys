export type StreamEventName =
  | "accepted"
  | "planning"
  | "retrieving"
  | "sources"
  | "delta"
  | "heartbeat"
  | "done"
  | "error";

export interface StreamEvent {
  event: StreamEventName;
  request_id: string;
  sequence: number;
  data: Record<string, unknown>;
}

export interface Citation {
  citation_id: number;
  evidence_id: string;
  title: string;
  source: string;
  url: string;
  modality: string;
  metadata: Record<string, string>;
}

export interface Conflict {
  evidence_ids: string[];
  type: string;
  reason: string;
}

export type RetrievalScope = "auto" | "local" | "web" | "hybrid";
export type Modality = "document" | "image" | "video";

export interface QueryInput {
  query: string;
  conversation_id?: string;
  retrieval_scope: RetrievalScope;
  modalities: Modality[];
}
