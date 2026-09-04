"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { DocumentDrawer } from "@/components/documents/DocumentDrawer";
import { DocumentTable } from "@/components/documents/DocumentTable";
import { FilterBar } from "@/components/documents/FilterBar";
import type { DocumentListPage, FilterOptions } from "@/types/api";

export function DocumentsScreen({
  page,
  options,
}: {
  page: DocumentListPage;
  options: FilterOptions | null;
}) {
  const router = useRouter();
  const [openId, setOpenId] = useState<string | null>(null);

  return (
    <div className="space-y-6">
      <FilterBar options={options} />
      <DocumentTable page={page} onOpen={(document) => setOpenId(document.id)} />

      {openId && (
        <DocumentDrawer
          documentId={openId}
          categories={options?.categories ?? []}
          onClose={() => setOpenId(null)}
          // The list is server-rendered, so a mutation has to ask the server
          // for the new page rather than patching local state — otherwise the
          // row's status and chunk count drift from the database.
          onChanged={() => router.refresh()}
        />
      )}
    </div>
  );
}
