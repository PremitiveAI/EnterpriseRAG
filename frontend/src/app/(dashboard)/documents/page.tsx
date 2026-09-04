import { redirect } from "next/navigation";
import { Suspense } from "react";
import { DocumentsScreen } from "@/components/documents/DocumentsScreen";
import { NotAuthenticated, backendGet } from "@/lib/api";
import { SESSION_ENDED_PATH } from "@/lib/session-ended";
import type { DocumentListPage, FilterOptions } from "@/types/api";

export const metadata = { title: "Documents · EnterpriseRAG" };

// The corpus changes constantly; a cached list showing a document that has
// since been deleted is worse than a slightly slower one.
export const dynamic = "force-dynamic";

const PASS_THROUGH = [
  "search",
  "status",
  "category_id",
  "document_type",
  "language",
  "tag",
  "created_from",
  "created_to",
  "sort",
  "order",
  "page",
  "page_size",
] as const;

export default async function DocumentsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;

  // Only known keys are forwarded. The backend whitelists sort and filters
  // anyway, but forwarding arbitrary query strings makes it easy to grow a
  // parameter here that nothing on the server validates.
  const query = new URLSearchParams();
  for (const key of PASS_THROUGH) {
    const value = params[key];
    if (Array.isArray(value)) value.forEach((entry) => query.append(key, entry));
    else if (value) query.set(key, value);
  }

  let listResponse;
  let optionsResponse;
  try {
    [listResponse, optionsResponse] = await Promise.all([
      backendGet<DocumentListPage>(`/admin/documents?${query.toString()}`),
      backendGet<FilterOptions>("/admin/documents/categories"),
    ]);
  } catch (error) {
    if (error instanceof NotAuthenticated) redirect(SESSION_ENDED_PATH);
    throw error;
  }

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight text-on-background">Documents</h1>
        <p className="mt-1 text-on-surface-variant">
          Search, filter and manage everything in the knowledge base.
        </p>
      </header>

      {listResponse.success ? (
        <Suspense fallback={null}>
          <DocumentsScreen
            page={listResponse.data}
            options={optionsResponse.success ? optionsResponse.data : null}
          />
        </Suspense>
      ) : (
        <div className="rounded-[32px] border border-outline-variant bg-surface-container-lowest px-8 py-16 text-center">
          <p className="font-medium text-on-surface">Could not load documents</p>
          <p className="mt-1 text-sm text-on-surface-variant">{listResponse.message}</p>
          <p className="mt-3 font-mono text-[11px] text-on-surface-variant">
            {listResponse.error_code}
          </p>
        </div>
      )}
    </div>
  );
}
