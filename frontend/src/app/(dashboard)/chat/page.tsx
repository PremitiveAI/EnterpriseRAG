import { redirect } from "next/navigation";
import { ChatShell } from "@/components/chat/ChatShell";
import { NotAuthenticated, backendGet } from "@/lib/api";
import { SESSION_ENDED_PATH } from "@/lib/session-ended";
import type { ConversationSummary } from "@/types/api";

export const metadata = { title: "Chat · EnterpriseRAG" };
export const dynamic = "force-dynamic";

export default async function ChatIndexPage() {
  let response;
  try {
    response = await backendGet<{ items: ConversationSummary[] }>("/chat/conversations");
  } catch (error) {
    if (error instanceof NotAuthenticated) redirect(SESSION_ENDED_PATH);
    throw error;
  }

  return (
    <ChatShell conversations={response.success ? response.data.items : []}>
      <div className="flex h-full items-center justify-center px-6 text-center">
        <div>
          <h2 className="text-2xl font-semibold text-on-background">
            How can I help you today?
          </h2>
          <p className="mt-2 text-on-surface-variant">
            Start a new chat to ask about your documents.
          </p>
        </div>
      </div>
    </ChatShell>
  );
}
