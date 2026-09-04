import { redirect } from "next/navigation";
import { LLMProvidersScreen } from "@/components/llm-providers/LLMProvidersScreen";
import { NotAuthenticated, backendGet } from "@/lib/api";
import { getSubjectType } from "@/lib/session";
import { SESSION_ENDED_PATH } from "@/lib/session-ended";
import type { LLMProviderListPage } from "@/types/api";

export const metadata = { title: "LLM Providers · EnterpriseRAG" };

export const dynamic = "force-dynamic";

export default async function LLMProvidersPage() {
  if ((await getSubjectType()) !== "SUPER_ADMIN") redirect("/dashboard");

  let response;
  try {
    response = await backendGet<LLMProviderListPage>("/super-admin/llm-providers");
  } catch (error) {
    if (error instanceof NotAuthenticated) redirect(SESSION_ENDED_PATH);
    throw error;
  }

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight text-on-background">
          LLM Providers
        </h1>
        <p className="mt-1 text-on-surface-variant">
          Which model answers questions and reads uploaded documents. The choice is
          global — every organization uses it — and switching takes effect on the
          next request, in the API and the background worker, without a restart.
        </p>
      </header>

      {response.success ? (
        <LLMProvidersScreen providers={response.data.items} />
      ) : (
        <div className="rounded-[32px] border border-outline-variant bg-surface-container-lowest px-8 py-16 text-center">
          <p className="font-medium text-on-surface">Could not load the providers</p>
          <p className="mt-1 text-sm text-on-surface-variant">{response.message}</p>
          <p className="mt-3 font-mono text-[11px] text-on-surface-variant">
            {response.error_code}
          </p>
        </div>
      )}
    </div>
  );
}
