import Link from "next/link";
import { redirect } from "next/navigation";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { NotAuthenticated, backendGet } from "@/lib/api";
import { getSubjectType } from "@/lib/session";
import { formatBytes } from "@/lib/validation";
import { SESSION_ENDED_PATH } from "@/lib/session-ended";
import type { DocumentListPage } from "@/types/api";

export const metadata = { title: "Dashboard · EnterpriseRAG" };
export const dynamic = "force-dynamic";

// Counts come from the list endpoint's `total` with page_size=1: the rows are
// discarded, so each is a COUNT rather than a page of data. A dedicated stats
// endpoint would be faster; it is not worth a second contract for four numbers.
const ACTIVE = ["QUEUED", "PROCESSING", "EXTRACTING", "OCR", "CLASSIFYING", "CHUNKING",
  "EMBEDDING", "INDEXING"];

async function count(params: string): Promise<number | null> {
  const response = await backendGet<DocumentListPage>(`/admin/documents?page_size=1&${params}`);
  return response.success ? response.data.total : null;
}

export default async function DashboardPage() {
  // Every count below is organization-scoped, and a Super Admin has no
  // organization: they would land on four tiles reading "unavailable".
  if ((await getSubjectType()) === "SUPER_ADMIN") redirect("/super-admin/organizations");

  let total: number | null;
  let completed: number | null;
  let processing: number | null;
  let failed: number | null;
  let recent;

  try {
    [total, completed, processing, failed, recent] = await Promise.all([
      count(""),
      count("status=COMPLETED"),
      count(ACTIVE.map((s) => `status=${s}`).join("&")),
      count("status=FAILED"),
      backendGet<DocumentListPage>("/admin/documents?page_size=5&sort=created_at&order=desc"),
    ]);
  } catch (error) {
    if (error instanceof NotAuthenticated) redirect(SESSION_ENDED_PATH);
    throw error;
  }

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight text-on-background">Overview</h1>
        <p className="mt-1 text-on-surface-variant">Monitor the corpus and the processing queue.</p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Documents" value={total} />
        <Stat label="Indexed" value={completed} />
        <Stat label="Processing" value={processing} />
        <Stat label="Failed" value={failed} href="/documents?status=FAILED" />
      </div>

      <section className="overflow-hidden rounded-[32px] border border-outline-variant bg-surface-container-lowest">
        <div className="flex items-center justify-between border-b border-outline-variant px-8 py-5">
          <h2 className="font-semibold text-on-surface">Recent documents</h2>
          <Link href="/documents" className="text-sm text-primary hover:underline">
            View all
          </Link>
        </div>

        {recent.success && recent.data.items.length > 0 ? (
          <ul>
            {recent.data.items.map((item) => (
              <li
                key={item.id}
                className="flex items-center gap-4 border-b border-outline-variant/60 px-8 py-4 last:border-0"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-on-surface">
                    {item.title ?? item.file_name}
                  </p>
                  <p className="truncate font-mono text-[11px] text-on-surface-variant">
                    {item.file_name} · {formatBytes(item.file_size)}
                  </p>
                </div>
                <StatusBadge status={item.status} />
              </li>
            ))}
          </ul>
        ) : (
          <div className="px-8 py-12 text-center">
            <p className="text-on-surface-variant">
              {recent.success ? "Nothing has been uploaded yet." : recent.message}
            </p>
            <Link
              href="/upload"
              className="mt-5 inline-flex h-11 items-center rounded-full bg-primary px-7 text-sm font-medium text-on-primary transition-colors hover:bg-primary-container"
            >
              Upload a document
            </Link>
          </div>
        )}
      </section>
    </div>
  );
}

function Stat({ label, value, href }: { label: string; value: number | null; href?: string }) {
  const body = (
    <div className="rounded-[32px] border border-outline-variant bg-surface-container-lowest px-7 py-6">
      <p className="text-sm text-on-surface-variant">{label}</p>
      {/* An em dash, not a zero: "0 failed" and "could not reach the server"
          must not look the same. */}
      <p className="mt-1 text-3xl font-semibold text-on-surface">{value ?? "—"}</p>
    </div>
  );
  return href ? <Link href={href}>{body}</Link> : body;
}
