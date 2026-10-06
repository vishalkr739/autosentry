import { AlertTriangle, FileText } from 'lucide-react';
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { CITE_SCHEME, citedId, linkCitations } from '@/lib/citations';
import { cn } from '@/lib/utils';

interface Props {
  content: string;
  unbacked?: string[];
  onCite?: (id: string) => void;
}

/** The agent's markdown answer, with each inline citation as a clickable chip. */
export function MarkdownRenderer({ content, unbacked = [], onCite }: Props) {
  return (
    <div className="space-y-2 text-sm leading-relaxed [&_li]:ml-5 [&_ol]:list-decimal [&_ul]:list-disc [&_h1]:font-semibold [&_h2]:font-semibold [&_h3]:font-semibold [&_strong]:font-semibold">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => (url.startsWith(CITE_SCHEME) ? url : defaultUrlTransform(url))}
        components={{
          a: ({ href, children }) => {
            const id = citedId(href);
            if (id === null) {
              return (
                <a href={href} target="_blank" rel="noreferrer">
                  {children}
                </a>
              );
            }
            const isDoc = id.startsWith('doc:');
            const isUnbacked = unbacked.includes(id);
            return (
              <button
                type="button"
                onClick={() => onCite?.(id)}
                title={isUnbacked ? 'No tool returned this id' : `Show evidence for ${id}`}
                className={cn(
                  'mx-0.5 inline-flex items-center gap-1 rounded-sm border px-1.5 py-px align-baseline font-mono text-[11px] leading-4 hover:bg-accent',
                  isUnbacked ? 'border-destructive text-destructive' : 'border-border text-link'
                )}
              >
                {isDoc && <FileText className="h-3 w-3" />}
                {isUnbacked && <AlertTriangle className="h-3 w-3" />}
                {isDoc ? id.slice('doc:'.length) : id}
              </button>
            );
          },
        }}
      >
        {linkCitations(content)}
      </ReactMarkdown>
    </div>
  );
}
