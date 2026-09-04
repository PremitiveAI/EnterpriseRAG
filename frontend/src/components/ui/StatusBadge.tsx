/** Status chip. Colour comes from tokens, never a literal hex (ADR-008). */
const STYLES: Record<string, string> = {
  QUEUED: "bg-[--color-status-queued-bg] text-[--color-status-queued-fg]",
  PROCESSING: "bg-[--color-status-processing-bg] text-[--color-status-processing-fg]",
  COMPLETED: "bg-[--color-status-completed-bg] text-[--color-status-completed-fg]",
  FAILED: "bg-[--color-status-failed-bg] text-[--color-status-failed-fg]",
  DUPLICATE: "bg-[--color-status-duplicate-bg] text-[--color-status-duplicate-fg]",
  REJECTED: "bg-[--color-status-failed-bg] text-[--color-status-failed-fg]",
  DELETED: "bg-[--color-status-deleted-bg] text-[--color-status-deleted-fg]",
  READY: "bg-surface-container-high text-on-surface-variant",
  // Organization states. Suspended borrows the failed palette on purpose: it is
  // reversible, but while it lasts nobody in that organization can sign in.
  ACTIVE: "bg-[--color-status-completed-bg] text-[--color-status-completed-fg]",
  SUSPENDED: "bg-[--color-status-failed-bg] text-[--color-status-failed-fg]",
  INVALID: "bg-[--color-status-failed-bg] text-[--color-status-failed-fg]",
};

export function StatusBadge({ status }: { status: string }) {
  const style = STYLES[status] ?? STYLES.READY;
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full px-3 py-1 font-mono text-[11px] font-medium uppercase tracking-wider ${style}`}
    >
      {/* Status is conveyed by text as well as colour, so a chip is never colour-only. */}
      {status.toLowerCase()}
    </span>
  );
}
