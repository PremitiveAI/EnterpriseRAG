import type { ReactNode } from "react";

/**
 * Fully-rounded container (ADR-008). px-8 rather than px-4: with a 9999px
 * radius the browser clamps to a stadium and content would otherwise collide
 * with the curve.
 */
export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`overflow-hidden rounded-[9999px] border border-outline-variant bg-surface-container-lowest px-8 py-6 ${className}`}
    >
      {children}
    </div>
  );
}

/** Square-cornered panel for content that cannot tolerate clipped corners. */
export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`overflow-hidden rounded-[32px] border border-outline-variant bg-surface-container-lowest ${className}`}
    >
      {children}
    </div>
  );
}
