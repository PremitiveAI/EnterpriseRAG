import { redirect } from "next/navigation";
import { AgentRulesScreen } from "@/components/agent-rules/AgentRulesScreen";
import { NotAuthenticated, backendGet } from "@/lib/api";
import { getSubjectType } from "@/lib/session";
import { SESSION_ENDED_PATH } from "@/lib/session-ended";
import type { AgentRuleListPage } from "@/types/api";

export const metadata = { title: "Agent Rules · EnterpriseRAG" };

export const dynamic = "force-dynamic";

export default async function AgentRulesPage() {
  if ((await getSubjectType()) !== "SUPER_ADMIN") redirect("/dashboard");

  let response;
  try {
    response = await backendGet<AgentRuleListPage>("/super-admin/agent-rules");
  } catch (error) {
    if (error instanceof NotAuthenticated) redirect(SESSION_ENDED_PATH);
    throw error;
  }

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight text-on-background">
          Agent Rules
        </h1>
        <p className="mt-1 text-on-surface-variant">
          The instructions given to the chat agents, in plain text. Rules are global —
          every organization&apos;s chat uses them — and take effect on the next
          message without a restart.
        </p>
      </header>

      {response.success ? (
        <AgentRulesScreen agents={response.data.items} />
      ) : (
        <div className="rounded-[32px] border border-outline-variant bg-surface-container-lowest px-8 py-16 text-center">
          <p className="font-medium text-on-surface">Could not load the agents</p>
          <p className="mt-1 text-sm text-on-surface-variant">{response.message}</p>
          <p className="mt-3 font-mono text-[11px] text-on-surface-variant">
            {response.error_code}
          </p>
        </div>
      )}
    </div>
  );
}
