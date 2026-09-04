/**
 * The chat state that matters most: a grounded refusal is a correct ANSWER,
 * not an error, and must not be styled as one. Confusing the two teaches the
 * admin to distrust both (docs/frontend/screens.md §6).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatPanel } from "@/components/chat/ChatPanel";
import type { ChatMessage } from "@/types/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

function message(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "m1",
    role: "assistant",
    content: "Employees receive twenty-four days of annual leave.",
    is_grounded: true,
    error_code: null,
    latency_ms: 3120,
    created_at: "2026-08-21T09:00:00Z",
    sources: [],
    ...overrides,
  };
}

const SOURCE = {
  document_id: "d1",
  document_name: "Employee Handbook.pdf",
  chunk_id: "c1",
  page: 4,
  score: 0.91,
  rank: 1,
  document_deleted: false,
};

describe("ChatPanel", () => {
  it("shows the empty state before anything is asked", () => {
    render(<ChatPanel conversationId="c" initialMessages={[]} />);
    expect(screen.getByText(/how can i help you today/i)).toBeInTheDocument();
  });

  it("renders a grounded answer with its source chips", () => {
    render(
      <ChatPanel
        conversationId="c"
        initialMessages={[message({ sources: [SOURCE] })]}
      />,
    );

    expect(screen.getByText(/twenty-four days/)).toBeInTheDocument();
    expect(screen.getByText("Employee Handbook.pdf")).toBeInTheDocument();
    expect(screen.getByText("Page 4")).toBeInTheDocument();
  });

  it("renders a refusal as an answer with no chips", () => {
    render(
      <ChatPanel
        conversationId="c"
        initialMessages={[
          message({
            content: "I could not find this information in the available documents.",
            is_grounded: false,
            error_code: "NO_RELEVANT_CONTEXT",
            sources: [],
          }),
        ]}
      />,
    );

    expect(screen.getByText(/could not find this information/i)).toBeInTheDocument();
    // No error affordance: it is a correct outcome.
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("greys a citation whose document has been deleted instead of linking to a 404", () => {
    render(
      <ChatPanel
        conversationId="c"
        initialMessages={[
          message({ sources: [{ ...SOURCE, document_deleted: true }] }),
        ]}
      />,
    );

    expect(screen.getByText("Employee Handbook.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("links a citation whose document still exists", () => {
    render(
      <ChatPanel conversationId="c" initialMessages={[message({ sources: [SOURCE] })]} />,
    );
    expect(screen.getByRole("link")).toHaveAttribute(
      "href",
      expect.stringContaining("Employee%20Handbook.pdf"),
    );
  });

  it("disables Send until something is typed", () => {
    render(<ChatPanel conversationId="c" initialMessages={[]} />);
    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
  });

  it("renders the user's own message distinctly from the assistant's", () => {
    render(
      <ChatPanel
        conversationId="c"
        initialMessages={[
          message({ id: "q", role: "user", content: "How much leave?", sources: [] }),
          message(),
        ]}
      />,
    );

    expect(screen.getByText("How much leave?")).toBeInTheDocument();
    expect(screen.getByText(/twenty-four days/)).toBeInTheDocument();
  });
});
