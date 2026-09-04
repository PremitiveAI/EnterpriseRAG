"use client";

import { MessageSquare, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiFetch } from "@/lib/client-fetch";
import type { ConversationSummary } from "@/types/api";

/** Date buckets, exactly the grouping the chat design uses. */
function bucketOf(iso: string | null): string {
  if (!iso) return "Older";

  const date = new Date(iso);
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);

  const days = Math.floor((startOfToday.getTime() - date.getTime()) / 86_400_000);
  if (days <= 0) return "Today";
  if (days <= 1) return "Yesterday";
  if (days <= 7) return "Previous 7 Days";
  return "Older";
}

const ORDER = ["Today", "Yesterday", "Previous 7 Days", "Older"];

export function ConversationList({
  conversations,
  activeId,
}: {
  conversations: ConversationSummary[];
  activeId?: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [removed, setRemoved] = useState<Set<string>>(new Set());

  const visible = conversations.filter((c) => !removed.has(c.id));

  const grouped = ORDER.map((bucket) => ({
    bucket,
    items: visible.filter((c) => bucketOf(c.last_message_at) === bucket),
  })).filter((group) => group.items.length > 0);

  async function startNew() {
    if (busy) return;
    setBusy(true);
    const response = await apiFetch("/api/chat/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: null }),
    });
    const body = await response.json();
    setBusy(false);
    if (body.success) router.push(`/chat/${body.data.id}`);
  }

  async function remove(id: string) {
    // Optimistic, with rollback if the server disagrees.
    setRemoved((current) => new Set(current).add(id));

    const response = await apiFetch(`/api/chat/conversations/${id}`, { method: "DELETE" });
    const body = await response.json();

    if (!body.success) {
      setRemoved((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
      return;
    }
    if (id === activeId) router.push("/chat");
    router.refresh();
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <button
        onClick={startNew}
        disabled={busy}
        className="inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-full bg-primary px-6 text-sm font-medium text-on-primary transition-colors hover:bg-primary-container disabled:opacity-50"
      >
        <Plus size={16} aria-hidden /> New chat
      </button>

      <nav className="flex-1 space-y-5 overflow-y-auto">
        {grouped.length === 0 && (
          <p className="px-2 text-sm text-on-surface-variant">No conversations yet.</p>
        )}

        {grouped.map((group) => (
          <div key={group.bucket}>
            <h3 className="px-4 pb-1.5 font-mono text-[10px] uppercase tracking-wider text-on-surface-variant">
              {group.bucket}
            </h3>
            <ul className="space-y-0.5">
              {group.items.map((conversation) => (
                <li key={conversation.id} className="group relative">
                  <Link
                    href={`/chat/${conversation.id}`}
                    className={`flex items-center gap-2.5 rounded-full py-2 pl-4 pr-10 text-sm transition-colors ${
                      conversation.id === activeId
                        ? "bg-secondary-container font-medium text-primary"
                        : "text-on-surface-variant hover:bg-surface-container-low"
                    }`}
                  >
                    <MessageSquare size={15} className="shrink-0" aria-hidden />
                    {/* Rendered as text, so a question containing markup
                        cannot inject into the sidebar. */}
                    <span className="truncate">{conversation.title}</span>
                  </Link>
                  <button
                    onClick={() => remove(conversation.id)}
                    aria-label={`Delete ${conversation.title}`}
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-1.5 text-on-surface-variant opacity-0 transition-opacity hover:bg-error-container hover:text-on-error-container focus:opacity-100 group-hover:opacity-100"
                  >
                    <Trash2 size={14} aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>
    </div>
  );
}
