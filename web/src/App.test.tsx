import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import App, { Investigation, STARTERS } from './App';
import { ThemeProvider } from './contexts/theme';

const encoder = new TextEncoder();

/** A fetch that answers /investigate/stream with these NDJSON events, and records each request body. */
function agent(events: object[][], bodies: unknown[] = []): typeof fetch {
  let call = 0;
  return (async (_url: string, init: RequestInit) => {
    bodies.push(JSON.parse(String(init.body)));
    const turn = events[call++] ?? [];
    return new Response(
      new ReadableStream({
        start(controller) {
          turn.forEach((event) => controller.enqueue(encoder.encode(JSON.stringify(event) + '\n')));
          controller.close();
        },
      })
    );
  }) as unknown as typeof fetch;
}

function finalEvent(answer: string, extra: object = {}) {
  return {
    type: 'final',
    data: {
      thread_id: 't1',
      turn_id: 'u1',
      answer,
      outcome: 'answered',
      steps: 2,
      unbacked: [],
      trace: [{ seq: 1, kind: 'llm', name: 'fraud_investigator', ok: true, duration_ms: 1200 }],
      cited_evidence: [{ type: 'Account', id: 'account-000127' }],
      evidence: [{ type: 'Account', id: 'account-000127' }],
      cited_documents: [
        {
          doc_id: 'fincen_sar_faqs_2025',
          title: 'FAQs Regarding Suspicious Activity Reporting',
          url: 'https://www.fincen.gov/sar-faqs.pdf',
          passages: [{ chunk_id: 'c0', text: '**A SAR** is required when...' }],
        },
      ],
      ...extra,
    },
  };
}

const TURN = [
  { type: 'turn_start', data: { thread_id: 't1', turn_id: 'u1' } },
  { type: 'activity', data: { tool: 'find_mule_candidates', status: 'started' } },
  { type: 'activity', data: { tool: 'find_mule_candidates', status: 'done', summary: '24 account(s) scored 3+' } },
  finalEvent('The mule is [account-000127], per [doc:fincen_sar_faqs_2025].'),
];

function renderPage(fetcher: typeof fetch) {
  return render(
    <ThemeProvider>
      <Investigation fetcher={fetcher} />
    </ThemeProvider>
  );
}

beforeEach(() => localStorage.clear());

describe('Investigation page', () => {
  it('shows the reference questions and the read-only promise on an empty chat', () => {
    renderPage(agent([]));
    for (const question of STARTERS) expect(screen.getByRole('button', { name: question })).toBeInTheDocument();
    expect(screen.getByText(/never blocks, reports or changes anything/)).toBeInTheDocument();
  });

  it('streams a question into steps and a cited answer, and opens evidence from a citation', async () => {
    renderPage(agent([TURN]));
    fireEvent.click(screen.getByRole('button', { name: STARTERS[0] }));

    const chip = await screen.findByRole('button', { name: 'account-000127' });
    expect(screen.getByText('List mule accounts in the graph')).toBeInTheDocument();
    expect(screen.getByText('1 investigation step')).toBeInTheDocument();

    fireEvent.click(chip);
    const panel = screen.getByRole('complementary', { name: 'Evidence' });
    expect(within(panel).getByText('Account')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'fincen_sar_faqs_2025' }));
    // the passage's markdown renders: bold, not raw asterisks
    expect(within(panel).getByText('A SAR', { selector: 'strong' })).toBeInTheDocument();
    expect(within(panel).queryByText(/\*\*/)).toBeNull();
    expect(within(panel).getByRole('link', { name: /Source document/ })).toHaveAttribute(
      'href',
      'https://www.fincen.gov/sar-faqs.pdf'
    );
  });

  it('expands the steps to show each tool and its result', async () => {
    renderPage(agent([TURN]));
    fireEvent.click(screen.getByRole('button', { name: STARTERS[0] }));
    fireEvent.click(await screen.findByText('1 investigation step'));
    expect(screen.getByText('Scoring accounts for mule signals')).toBeInTheDocument();
    expect(screen.getByText('24 account(s) scored 3+')).toBeInTheDocument();
  });

  it('sends a follow-up in the same thread', async () => {
    const bodies: unknown[] = [];
    renderPage(agent([TURN, [finalEvent('Still [account-000127].')]], bodies));
    fireEvent.click(screen.getByRole('button', { name: STARTERS[0] }));
    await screen.findByRole('button', { name: 'account-000127' });

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Is it still a mule?' } });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });

    await screen.findByText('Is it still a mule?');
    await waitFor(() =>
      expect(bodies).toEqual([{ question: STARTERS[0] }, { question: 'Is it still a mule?', thread_id: 't1' }])
    );
  });

  it('flags citations no tool returned', async () => {
    renderPage(agent([[finalEvent('It is [account-999999].', { unbacked: ['account-999999'], cited_evidence: [] })]]));
    fireEvent.click(screen.getByRole('button', { name: STARTERS[0] }));
    expect(await screen.findByText(/Cites ids no tool returned: account-999999/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'account-999999' }));
    expect(screen.getByText(/claim about it is unsupported/)).toBeInTheDocument();
  });

  it("shows the agent's error", async () => {
    renderPage(
      agent([
        [{ type: 'error', data: { detail: 'the investigation failed: model down', code: 'investigation_failed' } }],
      ])
    );
    fireEvent.click(screen.getByRole('button', { name: STARTERS[0] }));
    expect(await screen.findByRole('alert')).toHaveTextContent('model down');
  });

  it('stops a running investigation', async () => {
    const hanging = ((_url: string, init: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      })) as unknown as typeof fetch;
    renderPage(hanging);
    fireEvent.click(screen.getByRole('button', { name: STARTERS[0] }));

    fireEvent.click(await screen.findByRole('button', { name: 'Stop' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Stopped.');
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument();
  });

  it('starts a new investigation from the nav', async () => {
    renderPage(agent([TURN]));
    fireEvent.click(screen.getByRole('button', { name: STARTERS[0] }));
    await screen.findByRole('button', { name: 'account-000127' });
    fireEvent.click(screen.getByRole('button', { name: 'New investigation' }));
    expect(screen.getByRole('button', { name: STARTERS[0] })).toBeInTheDocument();
  });
});

describe('App shell', () => {
  it('defaults to dark and toggles the theme, remembering it', () => {
    render(<App />);
    expect(screen.getByText('Autosentry')).toBeInTheDocument();
    expect(document.documentElement).toHaveClass('dark');

    fireEvent.click(screen.getByRole('button', { name: 'Switch to light theme' }));
    expect(document.documentElement).not.toHaveClass('dark');
    expect(localStorage.getItem('theme')).toBe('light');
  });
});
