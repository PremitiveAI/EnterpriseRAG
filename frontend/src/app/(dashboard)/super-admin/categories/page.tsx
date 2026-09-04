import { redirect } from "next/navigation";
import { CategoriesScreen } from "@/components/categories/CategoriesScreen";
import { NotAuthenticated, backendGet } from "@/lib/api";
import { getSubjectType } from "@/lib/session";
import { SESSION_ENDED_PATH } from "@/lib/session-ended";
import type { CategoryListPage } from "@/types/api";

export const metadata = { title: "Categories · EnterpriseRAG" };

export const dynamic = "force-dynamic";

export default async function CategoriesPage() {
  if ((await getSubjectType()) !== "SUPER_ADMIN") redirect("/dashboard");

  let response;
  try {
    response = await backendGet<CategoryListPage>("/super-admin/categories");
  } catch (error) {
    if (error instanceof NotAuthenticated) redirect(SESSION_ENDED_PATH);
    throw error;
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight text-on-background">
          Categories
        </h1>
        <p className="mt-1 text-on-surface-variant">
          One taxonomy, shared by every organization. Each description is sent to the
          classifier, so it shapes how documents are sorted — everywhere.
        </p>
      </header>

      {response.success ? (
        <CategoriesScreen categories={response.data.items} />
      ) : (
        <div className="rounded-[32px] border border-outline-variant bg-surface-container-lowest px-8 py-16 text-center">
          <p className="font-medium text-on-surface">Could not load categories</p>
          <p className="mt-1 text-sm text-on-surface-variant">{response.message}</p>
          <p className="mt-3 font-mono text-[11px] text-on-surface-variant">
            {response.error_code}
          </p>
        </div>
      )}
    </div>
  );
}
