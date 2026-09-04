import type { ReactNode } from "react";
import { ConversationList } from "@/components/chat/ConversationList";
import type { ConversationSummary } from "@/types/api";

/**
 * Two-column chat layout: history on the left, the 800px message column on the
 * right. The list is server-rendered, so it is correct on first paint and after
 * every `router.refresh()`.
 */
export function ChatShell({
  conversations,
  activeId,
  children,
}: {
  conversations: ConversationSummary[];
  activeId?: string;
  children: ReactNode;
}) {
  return (
    // Negative margins cancel the shell's own padding (px-6 py-8 / md:px-12),
    // so the chat column reaches the window edges the way the design has it.
    <div className="-mx-6 -my-8 flex h-[calc(100vh-4rem)] md:-mx-12">
      <aside className="hidden w-[280px] shrink-0 flex-col border-r border-outline-variant bg-surface-container-lowest p-4 lg:flex">
        <ConversationList conversations={conversations} activeId={activeId} />
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">{children}</section>
    </div>
  );
}
