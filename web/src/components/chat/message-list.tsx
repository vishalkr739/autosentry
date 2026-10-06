import { useEffect, useRef } from 'react';

import type { ChatMessage as Message } from '@/lib/chat';

import { ChatMessage } from './chat-message';

/** The conversation, scrolled to the newest message as it streams in. */
export function MessageList({ messages, onCite }: { messages: Message[]; onCite: (id: string) => void }) {
  const end = useRef<HTMLDivElement>(null);
  const last = messages[messages.length - 1];

  useEffect(() => {
    end.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' });
  }, [messages.length, last?.content, last?.tools.length]);

  return (
    <div className="space-y-4" aria-live="polite">
      {messages.map((message) => (
        <ChatMessage key={message.id} message={message} onCite={onCite} />
      ))}
      <div ref={end} />
    </div>
  );
}
