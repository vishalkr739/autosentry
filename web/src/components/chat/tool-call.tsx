import { CheckCircle2, Loader2, XCircle } from 'lucide-react';

import type { ToolActivity } from '@/lib/chat';
import { cn } from '@/lib/utils';

/** What each tool is doing, in words an analyst reads at a glance. */
export const TOOL_LABELS: Record<string, string> = {
  find_mule_candidates: 'Scoring accounts for mule signals',
  trace_fund_transfer_chain: 'Tracing where the money went',
  find_shared_infrastructure: 'Checking shared devices and IPs',
  find_structuring_pattern: 'Looking for structuring',
  run_graph_query: 'Running a custom graph query',
  search_knowledge: 'Searching FinCEN/OFAC guidance',
};

/** One tool call: a spinner while it runs, then its one-line result (cloud-portal's ToolCall chip). */
export function ToolCall({ activity }: { activity: ToolActivity }) {
  const label = TOOL_LABELS[activity.tool] ?? activity.tool;
  return (
    <div
      className={cn(
        'flex items-start gap-2 rounded-md border border-border px-3 py-2 text-xs',
        activity.status === 'failed' ? 'text-destructive' : 'text-muted-foreground'
      )}
      data-status={activity.status}
    >
      {activity.status === 'started' && <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin" />}
      {activity.status === 'done' && <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
      {activity.status === 'failed' && <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
      <div className="min-w-0">
        <span className="font-medium text-foreground">{label}</span>
        <code className="ml-2 opacity-60">{activity.tool}</code>
        {activity.summary && <div className="mt-0.5 break-words">{activity.summary}</div>}
        {activity.error && <div className="mt-0.5 break-words opacity-80">{activity.error}</div>}
      </div>
    </div>
  );
}
