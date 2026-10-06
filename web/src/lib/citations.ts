/**
 * Inline citations in an answer: [account-000127], [tx-1, device-2], [doc:<id>].
 *
 * The same rule as the agent's grounding check (agent/grounding.py): a
 * bracketed group counts only if every comma-separated part looks like an
 * id, so asides like "[1]" or "[as above]" stay text.
 */

const ID = /^(?:doc:)?[A-Za-z0-9][A-Za-z0-9_.:-]*$/;
const IPV4 = /^\d{1,3}(?:\.\d{1,3}){3}$/;
const BRACKETS = /\[([^[\]]{1,400})\](?!\()/g;

export const CITE_SCHEME = 'cite:';

export function looksLikeId(part: string): boolean {
  return ID.test(part) && (part.includes('-') || part.includes('_') || part.startsWith('doc:') || IPV4.test(part));
}

export function citationGroup(group: string): string[] | null {
  const parts = group.split(',').map((p) => p.trim());
  return parts.length > 0 && parts.every(looksLikeId) ? parts : null;
}

/** Rewrite citations as markdown links with a `cite:` href, which the renderer turns into chips. */
export function linkCitations(markdown: string): string {
  return markdown.replace(BRACKETS, (whole, group: string) => {
    const ids = citationGroup(group);
    if (!ids) return whole;
    return ids.map((id) => `[${id}](${CITE_SCHEME}${encodeURIComponent(id)})`).join(' ');
  });
}

export function citedId(href: string | undefined): string | null {
  return href?.startsWith(CITE_SCHEME) ? decodeURIComponent(href.slice(CITE_SCHEME.length)) : null;
}
