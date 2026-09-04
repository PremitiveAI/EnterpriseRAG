/**
 * The empty state is the one worth testing: "no documents yet" and "no
 * documents match these filters" are different situations and must not look
 * the same (docs/frontend/screens.md).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DocumentTable } from "@/components/documents/DocumentTable";
import type { DocumentListPage, DocumentSummary } from "@/types/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

function row(overrides: Partial<DocumentSummary> = {}): DocumentSummary {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    file_name: "leave-policy.pdf",
    title: "Annual Leave Policy",
    file_type: "pdf",
    file_size: 865_280,
    document_type: "policy",
    category: { id: "c1", slug: "hr-policies", name: "HR Policies" },
    status: "COMPLETED",
    language: "en",
    page_count: 42,
    chunk_count: 87,
    tags: ["hr"],
    is_possible_duplicate: false,
    // Private by default, exactly as the backend defaults it. A fixture that
    // published everything would make the badge test pass by accident.
    is_public: false,
    error_code: null,
    created_at: "2026-08-14T09:12:00Z",
    deleted_at: null,
    ...overrides,
  };
}

function page(items: DocumentSummary[], overrides: Partial<DocumentListPage> = {}) {
  return {
    items,
    page: 1,
    page_size: 25,
    total: items.length,
    total_pages: items.length ? 1 : 0,
    ...overrides,
  };
}

describe("DocumentTable", () => {
  it("renders a row with its name, size and status", () => {
    render(<DocumentTable page={page([row()])} onOpen={vi.fn()} />);

    expect(screen.getByText("Annual Leave Policy")).toBeInTheDocument();
    expect(screen.getByText(/leave-policy\.pdf/)).toBeInTheDocument();
    expect(screen.getByText("845 KB")).toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("marks a published document, so nothing is public without showing it", () => {
    render(<DocumentTable page={page([row({ is_public: true })])} onOpen={vi.fn()} />);

    expect(screen.getByText("Public")).toBeInTheDocument();
  });

  it("does not mark a private document", () => {
    render(<DocumentTable page={page([row()])} onOpen={vi.fn()} />);

    // The absence is the assertion: a badge on every row would be worse than
    // no badge at all, because it would stop meaning anything.
    expect(screen.queryByText("Public")).not.toBeInTheDocument();
  });

  it("falls back to the filename when the AI produced no title", () => {
    render(<DocumentTable page={page([row({ title: null })])} onOpen={vi.fn()} />);
    expect(screen.getAllByText(/leave-policy\.pdf/).length).toBeGreaterThan(0);
  });

  it("shows the error code on a failed row, next to its status", () => {
    /* Diagnosis and fix adjacent, rather than a click apart. */
    render(
      <DocumentTable
        page={page([row({ status: "FAILED", error_code: "OCR_UNAVAILABLE" })])}
        onOpen={vi.fn()}
      />,
    );

    expect(screen.getByText("OCR_UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
  });

  it("does not show an error code on a healthy row", () => {
    render(<DocumentTable page={page([row({ error_code: "OCR_FAILED" })])} onOpen={vi.fn()} />);
    expect(screen.queryByText("OCR_FAILED")).not.toBeInTheDocument();
  });

  it("renders the no-results state with a filter hint", () => {
    render(<DocumentTable page={page([])} onOpen={vi.fn()} />);

    expect(screen.getByText(/no documents match these filters/i)).toBeInTheDocument();
    expect(screen.getByText(/clear a filter/i)).toBeInTheDocument();
  });

  it("shows the range and total in the pager", () => {
    const items = Array.from({ length: 3 }, (_, i) =>
      row({ id: `1111111${i}-1111-4111-8111-111111111111` }),
    );
    render(
      <DocumentTable
        page={page(items, { page: 1, page_size: 3, total: 57, total_pages: 19 })}
        onOpen={vi.fn()}
      />,
    );

    // The range is assembled from several nodes, so match on the container.
    const pager = screen.getByText(/of 57/).parentElement;
    expect(pager?.textContent?.replace(/\s+/g, "")).toContain("1–3of57");
  });

  it("shows a short last page correctly", () => {
    const items = Array.from({ length: 2 }, (_, i) =>
      row({ id: `2222222${i}-2222-4222-8222-222222222222` }),
    );
    render(
      <DocumentTable
        page={page(items, { page: 3, page_size: 3, total: 8, total_pages: 3 })}
        onOpen={vi.fn()}
      />,
    );

    const pager = screen.getByText(/of 8/).parentElement;
    expect(pager?.textContent?.replace(/\s+/g, "")).toContain("7–8of8");
  });

  it("disables Previous on the first page and Next on the last", () => {
    render(
      <DocumentTable page={page([row()], { page: 1, total_pages: 1 })} onOpen={vi.fn()} />,
    );

    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });
});
