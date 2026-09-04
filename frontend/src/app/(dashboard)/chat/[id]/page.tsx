import { notFound, redirect } from "next/navigation";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { ChatShell } from "@/components/chat/ChatShell";
import { NotAuthenticated, backendGet } from "@/lib/api";
import { SESSION_ENDED_PATH } from "@/lib/session-ended";
import type { ConversationDetail, ConversationSummary } from "@/types/api";

export const dynamic = "force-dynamic";

export default async function ConversationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let detail;
  let list;
  try {
    [detail, list] = await Promise.all([
      backendGet<ConversationDetail>(`/chat/conversations/${id}`),
      backendGet<{ items: ConversationSummary[] }>("/chat/conversations"),
    ]);
  } catch (error) {
    if (error instanceof NotAuthenticated) redirect(SESSION_ENDED_PATH);
    throw error;
  }

  if (!detail.success) {
    if (detail.error_code === "CONVERSATION_NOT_FOUND") notFound();
    throw new Error(detail.message);
  }

  return (
    <ChatShell conversations={list.success ? list.data.items : []} activeId={id}>
      <ChatPanel conversationId={id} initialMessages={detail.data.messages} />
    </ChatShell>
  );
}
