"use client";

import { ArrowDown, ArrowUp, FileText, Globe } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { formatBytes } from "@/lib/validation";
import type { DocumentListPage, DocumentSummary } from "@/types/api";

const COLUMNS: { key: string; label: string; sortable: boolean; className?: string }[] = [
  { key: "file_name", label: "Document", sortable: true },
  { key: "category", label: "Category", sortable: false, className: "hidden lg:table-cell" },
  { key: "status", label: "Status", sortable: true },
  { key: "file_size", label: "Size", sortable: true, className: "hidden md:table-cell" },
  { key: "created_at", label: "Uploaded", sortable: true, className: "hidden sm:table-cell" },
];

export function DocumentTable({
  page,
  onOpen,
}: {
  page: DocumentListPage;
  onOpen: (document: DocumentSummary) => void;
}) {
  const router = useRouter();
  const params = useSearchParams();

  const sort = params.get("sort") ?? "created_at";
  const order = params.get("order") ?? "desc";

  function toggleSort(key: string) {
    const query = new URLSearchParams(params.toString());
    query.set("sort", key);
    query.set("order", sort === key && order === "desc" ? "asc" : "desc");
    query.delete("page");
    router.push(`/documents?${query.toString()}`);
  }

  function goTo(target: number) {
    const query = new URLSearchParams(params.toString());
    query.set("page", String(target));
    router.push(`/documents?${query.toString()}`);
  }

  if (page.items.length === 0) {
    return (
      <div className="rounded-[32px] border border-outline-variant bg-surface-container-lowest px-8 py-16 text-center">
        <FileText size={28} className="mx-auto text-on-surface-variant" aria-hidden />
        <p className="mt-3 font-medium text-on-surface">No documents match these filters</p>
        <p className="mt-1 text-sm text-on-surface-variant">
          Clear a filter, or upload something new.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="overflow-hidden rounded-[32px] border border-outline-variant bg-surface-container-lowest">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="border-b border-outline-variant text-on-surface-variant">
              <tr>
                {COLUMNS.map((column) => (
                  <th key={column.key} scope="col" className={`px-6 py-4 font-medium ${column.className ?? ""}`}>
                    {column.sortable ? (
                      <button
                        onClick={() => toggleSort(column.key)}
                        className="inline-flex items-center gap-1 transition-colors hover:text-on-surface"
                        aria-sort={
                          sort === column.key
                            ? order === "asc"
                              ? "ascending"
                              : "descending"
                            : "none"
                        }
                      >
                        {column.label}
                        {sort === column.key &&
                          (order === "asc" ? (
                            <ArrowUp size={13} aria-hidden />
                          ) : (
                            <ArrowDown size={13} aria-hidden />
                          ))}
                      </button>
                    ) : (
                      column.label
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {page.items.map((item) => (
                <tr
                  key={item.id}
                  onClick={() => onOpen(item)}
                  tabIndex={0}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onOpen(item);
                    }
                  }}
                  className="cursor-pointer border-b border-outline-variant/60 last:border-0 transition-colors hover:bg-surface-container-low focus:bg-surface-container-low focus:outline-none"
                >
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-on-surface">
                        {item.title ?? item.file_name}
                      </span>
                      {/* Which documents strangers can ask about has to be
                          visible from the list. Finding out one row at a time
                          is how a document stays published by accident. */}
                      {item.is_public && (
                        <span
                          title="Published to the public chatbot"
                          className="inline-flex shrink-0 items-center gap-1 rounded-full bg-primary-container px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-on-primary-container"
                        >
                          <Globe size={10} aria-hidden />
                          Public
                        </span>
                      )}
                    </div>
                    <div className="truncate font-mono text-[11px] text-on-surface-variant">
                      {item.file_name}
                      {item.chunk_count > 0 && ` · ${item.chunk_count} chunks`}
                    </div>
                    {item.error_code && item.status === "FAILED" && (
                      <div className="mt-1 font-mono text-[11px] text-error">{item.error_code}</div>
                    )}
                  </td>
                  <td className="hidden px-6 py-4 text-on-surface-variant lg:table-cell">
                    {item.category?.name ?? "—"}
                  </td>
                  <td className="px-6 py-4">
                    <StatusBadge status={item.status} />
                  </td>
                  <td className="hidden px-6 py-4 text-on-surface-variant md:table-cell">
                    {formatBytes(item.file_size)}
                  </td>
                  <td className="hidden px-6 py-4 text-on-surface-variant sm:table-cell">
                    {new Date(item.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex items-center justify-between px-2 text-sm text-on-surface-variant">
        <span>
          {(page.page - 1) * page.page_size + 1}–
          {Math.min(page.page * page.page_size, page.total)} of {page.total}
        </span>
        <div className="flex items-center gap-2">
          <PageButton disabled={page.page <= 1} onClick={() => goTo(page.page - 1)}>
            Previous
          </PageButton>
          <span className="px-2">
            {page.page} / {page.total_pages}
          </span>
          <PageButton disabled={page.page >= page.total_pages} onClick={() => goTo(page.page + 1)}>
            Next
          </PageButton>
        </div>
      </div>
    </div>
  );
}

function PageButton({
  children,
  disabled,
  onClick,
}: {
  children: React.ReactNode;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="h-9 rounded-full border border-outline-variant px-5 text-on-surface transition-colors hover:bg-surface-container-low disabled:cursor-not-allowed disabled:opacity-40"
    >
      {children}
    </button>
  );
}
