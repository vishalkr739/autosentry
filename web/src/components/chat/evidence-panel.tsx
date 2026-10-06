import { AlertTriangle, ExternalLink, FileText, Network, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { Button } from '@/components/ui/button';
import type { CitedDocument, Evidence } from '@/lib/api';
import type { ChatMessage } from '@/lib/chat';

interface Collected {
  evidence: Map<string, Evidence>;
  documents: Map<string, CitedDocument>;
  unbacked: Set<string>;
}

/** Everything the thread's answers cited, and everything its tools returned. */
export function collectEvidence(messages: ChatMessage[]): Collected {
  const collected: Collected = { evidence: new Map(), documents: new Map(), unbacked: new Set() };
  for (const message of messages) {
    const result = message.result;
    if (!result) continue;
    for (const item of [...result.evidence, ...result.cited_evidence]) collected.evidence.set(item.id, item);
    for (const doc of result.cited_documents) collected.documents.set(doc.doc_id, doc);
    for (const id of result.unbacked) collected.unbacked.add(id);
  }
  return collected;
}

interface Props {
  selected: string;
  messages: ChatMessage[];
  onSelect: (id: string) => void;
  onClose: () => void;
}

export function EvidencePanel({ selected, messages, onSelect, onClose }: Props) {
  const { evidence, documents, unbacked } = collectEvidence(messages);
  const cited = new Set(messages.flatMap((m) => m.result?.cited_evidence.map((e) => e.id) ?? []));

  return (
    <aside
      className="flex w-[360px] shrink-0 flex-col border-l border-border-tertiary bg-surface"
      aria-label="Evidence"
    >
      <div className="flex items-center justify-between border-b border-border-tertiary px-4 py-3">
        <h2 className="text-sm font-semibold">Evidence</h2>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close evidence">
          <X className="h-4 w-4" />
        </Button>
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        <Selected id={selected} evidence={evidence} documents={documents} unbacked={unbacked} />

        {documents.size > 0 && (
          <section>
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Guidance cited</h3>
            <ul className="space-y-1">
              {[...documents.values()].map((doc) => (
                <li key={doc.doc_id}>
                  <button
                    className="text-left text-xs text-link hover:underline"
                    onClick={() => onSelect(`doc:${doc.doc_id}`)}
                  >
                    {doc.title}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        {cited.size > 0 && (
          <section>
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Entities cited</h3>
            <div className="flex flex-wrap gap-1">
              {[...cited].map((id) => (
                <button
                  key={id}
                  onClick={() => onSelect(id)}
                  className="rounded-sm border border-border px-1.5 py-px font-mono text-[11px] text-link hover:bg-accent"
                >
                  {id}
                </button>
              ))}
            </div>
          </section>
        )}
      </div>
    </aside>
  );
}

function Selected({
  id,
  evidence,
  documents,
  unbacked,
}: {
  id: string;
  evidence: Map<string, Evidence>;
  documents: Map<string, CitedDocument>;
  unbacked: Set<string>;
}) {
  if (id.startsWith('doc:')) {
    const doc = documents.get(id.slice('doc:'.length));
    if (!doc) return <Missing id={id} />;
    return (
      <section className="space-y-2">
        <div className="flex items-start gap-2">
          <FileText className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          <div>
            <div className="font-medium">{doc.title}</div>
            {doc.url && (
              <a href={doc.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs">
                Source document <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </div>
        </div>
        {doc.passages.map((passage) => (
          <blockquote
            key={passage.chunk_id}
            className="space-y-1 border-l-2 border-border pl-3 text-xs text-muted-foreground [&_strong]:font-semibold"
          >
            {/* graphrag stores chunks as markdown; render it, but not raw HTML */}
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{passage.text}</ReactMarkdown>
          </blockquote>
        ))}
      </section>
    );
  }

  const item = evidence.get(id);
  if (!item || unbacked.has(id)) return <Missing id={id} />;
  return (
    <section className="flex items-start gap-2">
      <Network className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
      <div>
        <div className="text-xs text-muted-foreground">{item.type}</div>
        <div className="font-mono text-sm">{item.id}</div>
        <p className="mt-1 text-xs text-muted-foreground">
          Returned by the investigation&apos;s graph tools in this thread.
        </p>
      </div>
    </section>
  );
}

function Missing({ id }: { id: string }) {
  return (
    <p className="flex items-start gap-2 text-xs text-destructive">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      No tool in this investigation returned <code>{id}</code>, so the answer&apos;s claim about it is unsupported.
    </p>
  );
}
