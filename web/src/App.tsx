import { useCallback, useReducer, useRef, useState } from 'react';

import { EvidencePanel } from '@/components/chat/evidence-panel';
import { MessageInput } from '@/components/chat/message-input';
import { MessageList } from '@/components/chat/message-list';
import { Sidebar } from '@/components/layout/sidebar';
import { ThemeProvider } from '@/contexts/theme';
import { investigate } from '@/lib/api';
import { chatReducer, initialChat, isRunning } from '@/lib/chat';

/** The spec's reference questions (section 15), offered when a chat is empty. */
export const STARTERS = [
  'List mule accounts in the graph',
  'Investigate account-000127: is this part of a fraud ring? What type of fraud is it?',
  'Which devices or IPs are shared across high-risk accounts?',
  'Do I need to file a regulatory report on these mule accounts?',
];

let nextId = 0;

export function Investigation({ fetcher = fetch }: { fetcher?: typeof fetch }) {
  const [chat, dispatch] = useReducer(chatReducer, initialChat);
  const [selected, setSelected] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const running = isRunning(chat);

  const ask = useCallback(
    async (question: string) => {
      const controller = new AbortController();
      abort.current = controller;
      dispatch({ type: 'ask', question, id: String(++nextId) });
      try {
        for await (const event of investigate(question, chat.threadId, controller.signal, fetcher)) {
          dispatch({ type: 'event', event });
        }
      } catch (error) {
        if (controller.signal.aborted) {
          dispatch({ type: 'stopped' });
        } else {
          dispatch({
            type: 'event',
            event: { type: 'error', data: { detail: `Could not reach the agent: ${String(error)}`, code: 'network' } },
          });
        }
      } finally {
        abort.current = null;
      }
    },
    [chat.threadId, fetcher]
  );

  const stop = () => abort.current?.abort();
  const reset = () => {
    stop();
    dispatch({ type: 'reset' });
    setSelected(null);
  };

  return (
    <div className="flex h-screen text-text-primary">
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)} onNewInvestigation={reset} />
      <main className="flex min-w-0 flex-1 flex-col bg-surface">
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-4 py-6">
            {chat.messages.length === 0 ? (
              <div className="space-y-4 pt-16 text-center">
                <h1 className="text-xl font-semibold">Fraud investigation</h1>
                <p className="text-muted-foreground">
                  Ask about accounts, money flows, shared devices or regulatory obligations. Every answer cites the
                  graph evidence and guidance it rests on.
                </p>
                <div className="mx-auto grid max-w-2xl gap-2 pt-2 sm:grid-cols-2">
                  {STARTERS.map((question) => (
                    <button
                      key={question}
                      onClick={() => ask(question)}
                      className="rounded-lg border border-border p-3 text-left text-sm hover:bg-accent"
                    >
                      {question}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <MessageList messages={chat.messages} onCite={setSelected} />
            )}
          </div>
        </div>
        <div className="mx-auto w-full max-w-3xl px-4 pb-4">
          <MessageInput running={running} onSend={ask} onStop={stop} />
          <p className="pt-2 text-center text-xs text-muted-foreground">
            Read-only: Autosentry investigates and explains; it never blocks, reports or changes anything.
          </p>
        </div>
      </main>
      {selected && (
        <EvidencePanel
          selected={selected}
          messages={chat.messages}
          onSelect={setSelected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

function App() {
  return (
    <ThemeProvider>
      <Investigation />
    </ThemeProvider>
  );
}

export default App;
