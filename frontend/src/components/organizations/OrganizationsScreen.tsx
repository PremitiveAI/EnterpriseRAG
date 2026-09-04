"use client";

import { Building2, Plus, Search } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, useTransition } from "react";
import { OrganizationDrawer } from "@/components/organizations/OrganizationDrawer";
import { Button } from "@/components/ui/Button";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type { OrganizationListPage, OrganizationSummary } from "@/types/api";

export function OrganizationsScreen({ page }: { page: OrganizationListPage }) {
  const router = useRouter();
  const params = useSearchParams();
  const [, startTransition] = useTransition();

  const [openId, setOpenId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState(params.get("search") ?? "");

  function apply(next: Record<string, string | null>) {
    const query = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(next)) {
      if (!value) query.delete(key);
      else query.set(key, value);
    }
    if (!("page" in next)) query.delete("page");
    startTransition(() => router.push(`/super-admin/organizations?${query}`));
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            apply({ search: search.trim() || null });
          }}
          className="relative flex-1"
        >
          <Search
            size={16}
            className="pointer-events-none absolute left-5 top-1/2 -translate-y-1/2 text-on-surface-variant"
            aria-hidden
          />
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search by name or slug"
            aria-label="Search organizations"
            className="h-11 w-full rounded-full border border-outline-variant bg-surface-container-lowest pl-12 pr-5 text-sm text-on-surface outline-none placeholder:text-on-surface-variant focus:border-primary"
          />
        </form>

        <select
          value={params.get("status") ?? ""}
          onChange={(event) => apply({ status: event.target.value || null })}
          className="h-11 rounded-full border border-outline-variant bg-surface-container-lowest px-5 text-sm text-on-surface outline-none"
          aria-label="Filter by status"
        >
          <option value="">Any status</option>
          <option value="ACTIVE">Active</option>
          <option value="SUSPENDED">Suspended</option>
        </select>

        <Button onClick={() => setCreating(true)}>
          <Plus size={16} aria-hidden /> New organization
        </Button>
      </div>

      {page.items.length === 0 ? (
        <div className="rounded-[32px] border border-outline-variant bg-surface-container-lowest px-8 py-16 text-center">
          <Building2 size={28} className="mx-auto text-on-surface-variant" aria-hidden />
          <p className="mt-3 font-medium text-on-surface">No organizations yet</p>
          <p className="mt-1 text-sm text-on-surface-variant">
            Create one, then add an administrator who can sign in and upload documents.
          </p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-[32px] border border-outline-variant bg-surface-container-lowest">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="border-b border-outline-variant text-on-surface-variant">
                <tr>
                  <th className="px-6 py-4 font-medium">Organization</th>
                  <th className="px-6 py-4 font-medium">Status</th>
                  <th className="px-6 py-4 font-medium">Admins</th>
                  <th className="px-6 py-4 font-medium">Documents</th>
                  <th className="px-6 py-4 font-medium">Public chat</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((organization) => (
                  <Row
                    key={organization.id}
                    organization={organization}
                    onOpen={() => setOpenId(organization.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {page.total_pages > 1 && (
        <div className="flex items-center justify-between px-2 text-sm text-on-surface-variant">
          <span>
            {(page.page - 1) * page.page_size + 1}–
            {Math.min(page.page * page.page_size, page.total)} of {page.total}
          </span>
          <div className="flex items-center gap-2">
            <PageButton
              disabled={page.page <= 1}
              onClick={() => apply({ page: String(page.page - 1) })}
            >
              Previous
            </PageButton>
            <span className="px-2">
              {page.page} / {page.total_pages}
            </span>
            <PageButton
              disabled={page.page >= page.total_pages}
              onClick={() => apply({ page: String(page.page + 1) })}
            >
              Next
            </PageButton>
          </div>
        </div>
      )}

      {(openId || creating) && (
        <OrganizationDrawer
          organizationId={openId}
          onClose={() => {
            setOpenId(null);
            setCreating(false);
          }}
          onChanged={() => router.refresh()}
        />
      )}
    </div>
  );
}

function Row({
  organization,
  onOpen,
}: {
  organization: OrganizationSummary;
  onOpen: () => void;
}) {
  return (
    <tr
      onClick={onOpen}
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      className="cursor-pointer border-b border-outline-variant/60 transition-colors last:border-0 hover:bg-surface-container-low focus:bg-surface-container-low focus:outline-none"
    >
      <td className="px-6 py-4">
        <div className="font-medium text-on-surface">{organization.name}</div>
        <div className="font-mono text-[11px] text-on-surface-variant">
          {organization.slug}
        </div>
      </td>
      <td className="px-6 py-4">
        <StatusBadge status={organization.status} />
      </td>
      <td className="px-6 py-4 text-on-surface-variant">{organization.counts.admins}</td>
      <td className="px-6 py-4 text-on-surface-variant">
        {organization.counts.documents}
        {organization.counts.public_documents > 0 && (
          <span className="ml-1 text-[11px]">
            ({organization.counts.public_documents} public)
          </span>
        )}
      </td>
      <td className="px-6 py-4 text-on-surface-variant">
        {organization.public_chat_enabled ? "Enabled" : "Off"}
      </td>
    </tr>
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
