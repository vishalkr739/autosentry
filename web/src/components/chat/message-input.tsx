import { ArrowUp, Square } from 'lucide-react';
import { type FormEvent, type KeyboardEvent, useState } from 'react';

import { Button } from '@/components/ui/button';

interface Props {
  running: boolean;
  onSend: (question: string) => void;
  onStop: () => void;
}

/** The question box: Enter sends, Shift+Enter breaks a line, Stop cancels a running investigation. */
export function MessageInput({ running, onSend, onStop }: Props) {
  const [value, setValue] = useState('');

  const send = (event?: FormEvent) => {
    event?.preventDefault();
    const question = value.trim();
    if (!question || running) return;
    onSend(question);
    setValue('');
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  };

  return (
    <form onSubmit={send} className="flex items-end gap-2 rounded-lg border border-input bg-background p-2 shadow-sm">
      <textarea
        aria-label="Ask about accounts, transactions or regulations"
        className="max-h-48 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground"
        placeholder="Ask about accounts, transactions or regulations, e.g. “List mule accounts in the graph”"
        rows={1}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
      />
      {running ? (
        <Button type="button" size="icon" variant="outline" onClick={onStop} aria-label="Stop">
          <Square className="h-3.5 w-3.5" />
        </Button>
      ) : (
        <Button type="submit" size="icon" disabled={!value.trim()} aria-label="Send">
          <ArrowUp className="h-4 w-4" />
        </Button>
      )}
    </form>
  );
}
