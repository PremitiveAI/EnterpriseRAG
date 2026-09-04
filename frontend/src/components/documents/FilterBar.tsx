"use client";

import { Search, X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import type { FilterOptions } from "@/types/api";

/**
 * Filters live in the URL, never in component state alone: a filtered view is
 * then shareable and survives a refresh (docs/features/document-management.md).
 */
export function FilterBar({ options }: { options: FilterOptions | null }) {
  const router = useRouter();
  const params = useSearchParams();
  const [pending, startTransition] = useTransition();

  const [search, setSearch] = useState(params.get("search") ?? "");

  // Keep the box in step when the user navigates back to a different filter.
  useEffect(() => {
    setSearch(params.get("search") ?? "");
  }, [params]);

  function apply(next: Record<string, string | null>) {
    const query = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === "") query.delete(key);
      else query.set(key, value);
    }
    // Any filter change invalidates the current page number: page 4 of the old
    // result set is usually past the end of the new one.
    if (!("page" in next)) query.delete("page");
    startTransition(() => router.push(`/documents?${query.toString()}`));
  }

  const active = [
    "search",
    "status",
    "category_id",
    "document_type",
    "language",
    "created_from",
    "created_to",
  ].filter((key) => params.get(key));

  return (
    <div className="space-y-3">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          apply({ search: search.trim() || null });
        }}
        className="flex items-center gap-2"
      >
        <div className="relative flex-1">
          <Search
            size={16}
            className="pointer-events-none absolute left-5 top-1/2 -translate-y-1/2 text-on-surface-variant"
            aria-hidden
          />
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search by title or file name"
            aria-label="Search documents"
            className="h-11 w-full rounded-full border border-outline-variant bg-surface-container-lowest pl-12 pr-5 text-sm text-on-surface outline-none placeholder:text-on-surface-variant focus:border-primary"
          />
        </div>
        <button
          type="submit"
          className="h-11 rounded-full bg-primary px-7 text-sm font-medium text-on-primary transition-colors hover:bg-primary-container disabled:opacity-50"
          disabled={pending}
        >
          Search
        </button>
      </form>

      <div className="flex flex-wrap items-center gap-2">
        <Select
          label="Status"
          value={params.get("status") ?? ""}
          options={(options?.statuses ?? []).map((s) => ({ value: s, label: s.toLowerCase() }))}
          onChange={(value) => apply({ status: value })}
        />
        <Select
          label="Category"
          value={params.get("category_id") ?? ""}
          options={(options?.categories ?? []).map((c) => ({ value: c.id, label: c.name }))}
          onChange={(value) => apply({ category_id: value })}
        />
        <Select
          label="Type"
          value={params.get("document_type") ?? ""}
          options={(options?.document_types ?? []).map((t) => ({ value: t, label: t }))}
          onChange={(value) => apply({ document_type: value })}
        />
        <Select
          label="Language"
          value={params.get("language") ?? ""}
          options={(options?.languages ?? []).map((l) => ({ value: l, label: l }))}
          onChange={(value) => apply({ language: value })}
        />

        <DateRange
          from={params.get("created_from") ?? ""}
          to={params.get("created_to") ?? ""}
          onChange={(key, value) => apply({ [key]: value || null })}
        />

        {active.length > 0 && (
          <button
            onClick={() => startTransition(() => router.push("/documents"))}
            className="inline-flex h-9 items-center gap-1.5 rounded-full px-4 text-sm text-on-surface-variant transition-colors hover:bg-surface-container-low"
          >
            <X size={14} aria-hidden />
            Clear {active.length} filter{active.length === 1 ? "" : "s"}
          </button>
        )}
      </div>
    </div>
  );
}

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="inline-flex h-9 items-center gap-2 rounded-full border border-outline-variant bg-surface-container-lowest pl-4 pr-2 text-sm">
      <span className="text-on-surface-variant">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-full bg-transparent pr-1 text-on-surface outline-none"
        // Disabled rather than hidden when empty: a select with no options at
        // all reads as broken, not as "nothing has been indexed yet".
        disabled={options.length === 0}
      >
        <option value="">Any</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function DateRange({
  from,
  to,
  onChange,
}: {
  from: string;
  to: string;
  onChange: (key: "created_from" | "created_to", value: string) => void;
}) {
  return (
    <span className="inline-flex h-9 items-center gap-2 rounded-full border border-outline-variant bg-surface-container-lowest px-4 text-sm">
      <span className="text-on-surface-variant">Uploaded</span>
      <input
        type="date"
        value={from}
        max={to || undefined}
        aria-label="Uploaded from"
        onChange={(event) => onChange("created_from", event.target.value)}
        className="bg-transparent text-on-surface outline-none"
      />
      <span className="text-on-surface-variant">-</span>
      <input
        type="date"
        value={to}
        min={from || undefined}
        aria-label="Uploaded to"
        onChange={(event) => onChange("created_to", event.target.value)}
        className="bg-transparent text-on-surface outline-none"
      />
    </span>
  );
}
