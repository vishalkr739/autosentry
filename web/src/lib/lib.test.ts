import { describe, expect, it } from 'vitest';

import { investigate, readNdjson, type StreamEvent } from './api';
import { chatReducer, initialChat, isRunning } from './chat';
import { citationGroup, citedId, linkCitations } from './citations';

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });
}

async function collect<T>(iterable: AsyncIterable<T>): Promise<T[]> {
  const out: T[] = [];
  for await (const item of iterable) out.push(item);
  return out;
}

describe('readNdjson', () => {
  it('reads one event per line, including a line split across chunks and a last line with no newline', async () => {
    const events = await collect(
      readNdjson(
        streamOf('{"type":"turn_start","data":{"thread_id":"t"', '}}\n\n{"type":"message",', '"data":{"text":"hi"}}')
      )
    );
    expect(events.map((e) => e.type)).toEqual(['turn_start', 'message']);
  });
});

describe('investigate', () => {
  it('posts the question with its thread and streams the events', async () => {
    const calls: { url: string; body: unknown }[] = [];
    const fetcher = (async (url: string, init: RequestInit) => {
      calls.push({ url, body: JSON.parse(String(init.body)) });
      return new Response(streamOf('{"type":"final","data":{"answer":"a"}}\n'));
    }) as unknown as typeof fetch;

    const events = await collect(investigate('who?', 't1', undefined, fetcher));
    expect(calls).toEqual([{ url: '/investigate/stream', body: { question: 'who?', thread_id: 't1' } }]);
    expect(events[0].type).toBe('final');
  });

  it("turns an HTTP error into an error event carrying the agent's detail", async () => {
    const fetcher = (async () =>
      new Response(JSON.stringify({ detail: 'no LLM_API_KEY configured', code: 'investigator_unavailable' }), {
        status: 503,
      })) as unknown as typeof fetch;
    const [event] = await collect(investigate('who?', null, undefined, fetcher));
    expect(event).toEqual({ type: 'error', data: { detail: 'no LLM_API_KEY configured', code: 'http_503' } });
  });
});

describe('citations', () => {
  it('links id groups and leaves asides as text', () => {
    expect(linkCitations('See [account-1, device-ring-0] and [1] and [doc:fincen_sar_faqs_2025].')).toBe(
      'See [account-1](cite:account-1) [device-ring-0](cite:device-ring-0) and [1] and ' +
        '[doc:fincen_sar_faqs_2025](cite:doc%3Afincen_sar_faqs_2025).'
    );
  });

  it('recognises IPs, keeps real markdown links, and reads cite hrefs back', () => {
    expect(citationGroup('198.51.100.1')).toEqual(['198.51.100.1']);
    expect(citationGroup('as above')).toBeNull();
    expect(linkCitations('[FinCEN](https://www.fincen.gov)')).toBe('[FinCEN](https://www.fincen.gov)');
    expect(citedId('cite:doc%3Ax')).toBe('doc:x');
    expect(citedId('https://x')).toBeNull();
  });
});

describe('chatReducer', () => {
  const final: StreamEvent = {
    type: 'final',
    data: {
      thread_id: 't9',
      turn_id: 'u1',
      answer: 'Mule [account-1].',
      outcome: 'answered',
      cited_evidence: [{ type: 'Account', id: 'account-1' }],
      cited_documents: [],
      evidence: [],
      unbacked: [],
      steps: 2,
      trace: [],
    },
  };

  it('builds a turn from a question and its events', () => {
    let state = chatReducer(initialChat, { type: 'ask', question: 'List mules', id: '1' });
    expect(isRunning(state)).toBe(true);
    state = chatReducer(state, {
      type: 'event',
      event: { type: 'turn_start', data: { thread_id: 't9', turn_id: 'u1' } },
    });
    state = chatReducer(state, {
      type: 'event',
      event: { type: 'activity', data: { tool: 'find_mule_candidates', status: 'started' } },
    });
    state = chatReducer(state, {
      type: 'event',
      event: { type: 'activity', data: { tool: 'find_mule_candidates', status: 'done', summary: '24 accounts' } },
    });
    state = chatReducer(state, { type: 'event', event: final });

    const answer = state.messages[1];
    expect(state.threadId).toBe('t9');
    expect(answer.tools).toEqual([{ tool: 'find_mule_candidates', status: 'done', summary: '24 accounts' }]);
    expect(answer.content).toBe('Mule [account-1].');
    expect(isRunning(state)).toBe(false);
  });

  it("keeps a failed tool's error on its step", () => {
    let state = chatReducer(initialChat, { type: 'ask', question: 'q', id: '1' });
    state = chatReducer(state, { type: 'event', event: { type: 'activity', data: { tool: 't', status: 'started' } } });
    state = chatReducer(state, {
      type: 'event',
      event: {
        type: 'activity',
        data: { tool: 't', status: 'failed', summary: 't could not run', error: 'workspace suspended' },
      },
    });
    expect(state.messages[1].tools).toEqual([
      { tool: 't', status: 'failed', summary: 't could not run', error: 'workspace suspended' },
    ]);
  });

  it('records errors and stops, and resets to an empty chat', () => {
    let state = chatReducer(initialChat, { type: 'ask', question: 'q', id: '1' });
    state = chatReducer(state, { type: 'event', event: { type: 'error', data: { detail: 'model down', code: 'x' } } });
    expect(state.messages[1]).toMatchObject({ pending: false, error: 'model down' });

    state = chatReducer(state, { type: 'ask', question: 'q2', id: '2' });
    state = chatReducer(state, { type: 'stopped' });
    expect(state.messages[3]).toMatchObject({ pending: false, error: 'Stopped.' });

    expect(chatReducer(state, { type: 'reset' })).toEqual(initialChat);
  });
});
