import * as Collapsible from '@radix-ui/react-collapsible';
import { AlertTriangle, ChevronRight } from 'lucide-react';
import { useState } from 'react';

import type { ChatMessage as Message } from '@/lib/chat';
import { cn } from '@/lib/utils';

import { MarkdownRenderer } from './markdown-renderer';
import { ToolCall } from './tool-call';
import { TypingIndicator } from './typing-indicator';

const OUTCOME_NOTE: Record<string, string> = {
  out_of_steps: 'The investigation hit its step limit; parts of this answer may be unverified.',
  out_of_time: 'The investigation hit its time limit; parts of this answer may be unverified.',
};

/** One chat bubble: the analyst's question, or the agent's steps and answer (cloud-portal's ChatMessage). */
export function ChatMessage({ message, onCite }: { message: Message; onCite: (id: string) => void }) {
  const [stepsOpen, setStepsOpen] = useState(false);

  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="whitespace-pre-wrap rounded-lg bg-primary p-3 text-sm text-primary-foreground sm:max-w-[70%]">
          {message.content}
        </div>
      </div>
    );
  }

  const result = message.result;
  const steps = message.tools;
  return (
    <div className="flex justify-start">
      <div className="w-full space-y-3 rounded-lg bg-muted p-3 text-sm text-foreground sm:max-w-[85%]">
        {steps.length > 0 && (
          <Collapsible.Root open={message.pending || stepsOpen} onOpenChange={setStepsOpen}>
            <Collapsible.Trigger
              className="flex cursor-pointer items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              disabled={message.pending}
            >
              <ChevronRight
                className={cn('h-3.5 w-3.5 transition-transform', (message.pending || stepsOpen) && 'rotate-90')}
              />
              {message.pending ? 'Investigating' : `${steps.length} investigation step${steps.length === 1 ? '' : 's'}`}
              {result && <span className="opacity-70"> · {(totalMs(result.trace) / 1000).toFixed(1)}s</span>}
            </Collapsible.Trigger>
            <Collapsible.Content className="mt-2 space-y-1.5">
              {steps.map((activity, i) => (
                <ToolCall key={`${activity.tool}-${i}`} activity={activity} />
              ))}
            </Collapsible.Content>
          </Collapsible.Root>
        )}

        {message.pending && !message.content && <TypingIndicator />}
        {message.content && <MarkdownRenderer content={message.content} unbacked={result?.unbacked} onCite={onCite} />}

        {result && OUTCOME_NOTE[result.outcome] && (
          <p className="text-xs text-muted-foreground">{OUTCOME_NOTE[result.outcome]}</p>
        )}
        {result && result.unbacked.length > 0 && (
          <p className="flex items-center gap-1 text-xs text-destructive">
            <AlertTriangle className="h-3.5 w-3.5" />
            Cites ids no tool returned: {result.unbacked.join(', ')}
          </p>
        )}
        {message.error && (
          <p className="rounded-md border border-destructive p-2 text-xs text-destructive" role="alert">
            {message.error}
          </p>
        )}
      </div>
    </div>
  );
}

function totalMs(trace: { duration_ms: number }[]): number {
  return trace.reduce((sum, record) => sum + record.duration_ms, 0);
}
