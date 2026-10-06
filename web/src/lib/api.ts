/** The agent's /investigate/stream API: NDJSON events, one per line. */

export interface Evidence {
  type: string;
  id: string;
}

export interface CitedDocument {
  doc_id: string;
  title: string;
  url: string;
  passages: { chunk_id: string; text: string }[];
}

export interface TraceRecord {
  seq: number;
  kind: 'llm' | 'tool';
  name: string;
  ok: boolean;
  duration_ms: number;
  summary?: string;
  error?: string | null;
}

export interface InvestigationResult {
  thread_id: string;
  turn_id: string;
  answer: string;
  outcome: 'answered' | 'out_of_steps' | 'out_of_time';
  cited_evidence: Evidence[];
  cited_documents: CitedDocument[];
  evidence: Evidence[];
  unbacked: string[];
  steps: number;
  trace: TraceRecord[];
}

export type StreamEvent =
  | { type: 'turn_start'; data: { thread_id: string; turn_id: string } }
  | {
      type: 'activity';
      data: {
        tool: string;
        status: 'started' | 'done' | 'failed';
        summary?: string;
        error?: string | null;
        args?: unknown;
      };
    }
  | { type: 'message'; data: { text: string } }
  | { type: 'final'; data: InvestigationResult }
  | { type: 'error'; data: { detail: string; code: string; request_id?: string } };

/** Parse an NDJSON byte stream into events; a line may arrive split across chunks. */
export async function* readNdjson(body: ReadableStream<Uint8Array>): AsyncGenerator<StreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffered = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffered += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      const lines = buffered.split('\n');
      buffered = lines.pop() ?? '';
      for (const line of lines) {
        if (line.trim()) yield JSON.parse(line) as StreamEvent;
      }
      if (done) break;
    }
    if (buffered.trim()) yield JSON.parse(buffered) as StreamEvent;
  } finally {
    reader.releaseLock();
  }
}

/** Ask a question; events stream back until `final` or `error`. */
export async function* investigate(
  question: string,
  threadId: string | null,
  signal?: AbortSignal,
  fetcher: typeof fetch = fetch
): AsyncGenerator<StreamEvent> {
  const response = await fetcher('/investigate/stream', {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/x-ndjson' },
    body: JSON.stringify(threadId ? { question, thread_id: threadId } : { question }),
    signal,
  });
  if (!response.ok || !response.body) {
    let detail = `the agent answered HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // not a JSON error body; keep the status line
    }
    yield { type: 'error', data: { detail, code: `http_${response.status}` } };
    return;
  }
  yield* readNdjson(response.body);
}
