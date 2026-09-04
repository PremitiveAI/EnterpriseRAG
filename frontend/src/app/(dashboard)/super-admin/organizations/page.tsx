import { redirect } from "next/navigation";
import { Suspense } from "react";
import { OrganizationsScreen } from "@/components/organizations/OrganizationsScreen";
import { NotAuthenticated, backendGet } from "@/lib/api";
import { getSubjectType } from "@/lib/session";
import { SESSION_ENDED_PATH } from "@/lib/session-ended";
import type { OrganizationListPage } from "@/types/api";

export const metadata = { title: "Organizations · EnterpriseRAG" };

export const dynamic = "force-dynamic";

const PASS_THROUGH = ["search", "status", "page", "page_size"] as const;

export default async function OrganizationsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  // An organization admin who guesses this URL gets sent to their own
  // dashboard rather than a screen that renders and then 403s on every call.
  if ((await getSubjectType()) !== "SUPER_ADMIN") redirect("/dashboard");

  const params = await searchParams;
  const query = new URLSearchParams();
  for (const key of PASS_THROUGH) {
    const value = params[key];
    if (typeof value === "string" && value) query.set(key, value);
  }

  let response;
  try {
    response = await backendGet<OrganizationListPage>(
      `/super-admin/organizations?${query.toString()}`,
    );
  } catch (error) {
    if (error instanceof NotAuthenticated) redirect(SESSION_ENDED_PATH);
    throw error;
  }

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight text-on-background">
          Organizations
        </h1>
        <p className="mt-1 text-on-surface-variant">
          Create tenants, set their limits and manage who administers them. Their
          documents and conversations stay private to them.
        </p>
      </header>

      {response.success ? (
        <Suspense fallback={null}>
          <OrganizationsScreen page={response.data} />
        </Suspense>
      ) : (
        <div className="rounded-[32px] border border-outline-variant bg-surface-container-lowest px-8 py-16 text-center">
          <p className="font-medium text-on-surface">Could not load organizations</p>
          <p className="mt-1 text-sm text-on-surface-variant">{response.message}</p>
          <p className="mt-3 font-mono text-[11px] text-on-surface-variant">
            {response.error_code}
          </p>
        </div>
      )}
    </div>
  );
}
