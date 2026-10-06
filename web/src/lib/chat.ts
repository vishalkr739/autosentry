/** Chat state, built from the analyst's questions and the agent's stream events. */

import type { InvestigationResult, StreamEvent } from './api';

export interface ToolActivity {
  tool: string;
  status: 'started' | 'done' | 'failed';
  summary?: string;
  error?: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  pending: boolean;
  tools: ToolActivity[];
  result?: InvestigationResult;
  error?: string;
}

export interface ChatState {
  threadId: string | null;
  messages: ChatMessage[];
}

export type ChatAction =
  | { type: 'ask'; question: string; id: string }
  | { type: 'event'; event: StreamEvent }
  | { type: 'stopped' }
  | { type: 'reset' };

export const initialChat: ChatState = { threadId: null, messages: [] };

function updateLast(state: ChatState, update: (m: ChatMessage) => ChatMessage): ChatState {
  const index = state.messages.length - 1;
  const last = state.messages[index];
  if (!last || last.role !== 'assistant') return state;
  const messages = [...state.messages];
  messages[index] = update(last);
  return { ...state, messages };
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'reset':
      return initialChat;
    case 'ask':
      return {
        ...state,
        messages: [
          ...state.messages,
          { id: `${action.id}-q`, role: 'user', content: action.question, pending: false, tools: [] },
          { id: `${action.id}-a`, role: 'assistant', content: '', pending: true, tools: [] },
        ],
      };
    case 'stopped':
      return updateLast(state, (m) => (m.pending ? { ...m, pending: false, error: 'Stopped.' } : m));
    case 'event':
      return applyEvent(state, action.event);
  }
}

function applyEvent(state: ChatState, event: StreamEvent): ChatState {
  switch (event.type) {
    case 'turn_start':
      return { ...state, threadId: event.data.thread_id };
    case 'activity':
      return updateLast(state, (m) => {
        const { tool, status, summary } = event.data;
        const error = event.data.error ?? undefined;
        if (status === 'started') return { ...m, tools: [...m.tools, { tool, status }] };
        // Complete the most recent running call of this tool.
        const tools = [...m.tools];
        for (let i = tools.length - 1; i >= 0; i--) {
          if (tools[i].tool === tool && tools[i].status === 'started') {
            tools[i] = { tool, status, summary, ...(error ? { error } : {}) };
            return { ...m, tools };
          }
        }
        return { ...m, tools: [...tools, { tool, status, summary, ...(error ? { error } : {}) }] };
      });
    case 'message':
      return updateLast(state, (m) => ({ ...m, content: event.data.text }));
    case 'final':
      return {
        ...updateLast(state, (m) => ({ ...m, content: event.data.answer, result: event.data, pending: false })),
        threadId: event.data.thread_id,
      };
    case 'error':
      return updateLast(state, (m) => ({ ...m, pending: false, error: event.data.detail }));
  }
}

export function isRunning(state: ChatState): boolean {
  return state.messages.some((m) => m.pending);
}
