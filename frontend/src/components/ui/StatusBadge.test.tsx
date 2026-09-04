/**
 * Status is conveyed by text as well as colour, so a chip is never colour-only
 * (ADR-008). These assert the states the dashboard and list actually render.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "@/components/ui/StatusBadge";

describe("StatusBadge", () => {
  it.each([
    "QUEUED",
    "PROCESSING",
    "COMPLETED",
    "FAILED",
    "DUPLICATE",
    "DELETED",
    "REJECTED",
  ])("renders %s as readable text", (status) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(status.toLowerCase())).toBeInTheDocument();
  });

  it("falls back rather than rendering nothing for an unknown status", () => {
    /* New pipeline states arrive over time; an unstyled chip is fine, a blank
       one is not. */
    render(<StatusBadge status="EXTRACTING" />);
    expect(screen.getByText("extracting")).toBeInTheDocument();
  });

  it("never relies on colour alone", () => {
    const { container } = render(<StatusBadge status="FAILED" />);
    expect(container.textContent?.trim()).toBe("failed");
  });
});
