/**
 * The public chat's answer rendering.
 *
 * Two behaviours here are not cosmetic and are easy to break without noticing:
 *
 * - Citation markers are stripped, because the composer numbers them by
 *   PASSAGE while the source list is rebuilt from only the cited passages and
 *   re-ranked from 1. "[2]" above a one-item list points at nothing.
 * - Sources are grouped by file, because three chunks out of one PDF arrived as
 *   three near-identical chips and read as three documents.
 */
import { describe, expect, it } from "vitest";
import {
  describePages,
  groupByFile,
  shortenName,
  stripCitationMarkers,
} from "@/components/public/PublicChat";

describe("stripCitationMarkers", () => {
  it("removes a marker and the space before it", () => {
    expect(stripCitationMarkers("The PAN number is ******234A [1].")).toBe(
      "The PAN number is ******234A.",
    );
  });

  it("removes multi-number and ranged markers", () => {
    expect(stripCitationMarkers("Leave is 24 days [1, 2].")).toBe("Leave is 24 days.");
    expect(stripCitationMarkers("Leave is 24 days [1-3].")).toBe("Leave is 24 days.");
  });

  it("removes several markers in one answer", () => {
    expect(stripCitationMarkers("First [1]. Second [2]. Third [3].")).toBe(
      "First. Second. Third.",
    );
  });

  it("leaves a Markdown link alone", () => {
    // The lookahead is the whole reason this is a regex and not an indexOf.
    expect(stripCitationMarkers("See [1](https://example.com) for more.")).toBe(
      "See [1](https://example.com) for more.",
    );
  });

  it("leaves non-numeric brackets alone", () => {
    expect(stripCitationMarkers("Use [see appendix] for detail.")).toBe(
      "Use [see appendix] for detail.",
    );
    expect(stripCitationMarkers("The array is a[i] = 3.")).toBe("The array is a[i] = 3.");
  });

  it("leaves an answer with no markers untouched", () => {
    const answer = "I could not find this information in the available documents.";
    expect(stripCitationMarkers(answer)).toBe(answer);
  });
});

describe("groupByFile", () => {
  it("collapses several chunks of one document into one file", () => {
    const files = groupByFile([
      { document_name: "leave-policy.pdf", page: 3 },
      { document_name: "leave-policy.pdf", page: 7 },
      { document_name: "leave-policy.pdf", page: 3 },
    ]);

    expect(files).toHaveLength(1);
    expect(files[0].name).toBe("leave-policy.pdf");
    // Page 3 appeared twice and is listed once.
    expect(files[0].pages).toEqual([3, 7]);
  });

  it("keeps distinct documents separate, best match first", () => {
    const files = groupByFile([
      { document_name: "handbook.pdf", page: 12 },
      { document_name: "leave-policy.pdf", page: 3 },
    ]);

    expect(files.map((file) => file.name)).toEqual(["handbook.pdf", "leave-policy.pdf"]);
  });

  it("sorts pages, whatever order retrieval returned them in", () => {
    const files = groupByFile([
      { document_name: "a.pdf", page: 9 },
      { document_name: "a.pdf", page: 2 },
    ]);

    expect(files[0].pages).toEqual([2, 9]);
  });

  it("handles a source with no page, as an image has", () => {
    const files = groupByFile([{ document_name: "pan-card.jpg", page: null }]);

    expect(files).toEqual([{ name: "pan-card.jpg", pages: [] }]);
  });
});

describe("describePages", () => {
  it("says nothing when there are no pages", () => {
    expect(describePages([])).toBeNull();
  });

  it("lists one or two pages in full", () => {
    expect(describePages([3])).toBe("p3");
    expect(describePages([3, 7])).toBe("p3, 7");
  });

  it("summarises the rest rather than wrapping the row", () => {
    expect(describePages([3, 7, 11, 14])).toBe("p3, 7 +2 more");
  });
});

describe("shortenName", () => {
  it("leaves a short name alone", () => {
    expect(shortenName("leave-policy.pdf")).toBe("leave-policy.pdf");
  });

  it("keeps the extension, which is how a file is recognised", () => {
    const shortened = shortenName(
      "Employee-Leave-And-Attendance-Policy-2025-2026-final.pdf",
    );

    expect(shortened.endsWith(".pdf")).toBe(true);
    expect(shortened.length).toBeLessThanOrEqual(34);
    expect(shortened).toContain("…");
  });

  it("does not mistake a dotted name for an extension", () => {
    // No short trailing extension, so the whole thing is the stem.
    const shortened = shortenName("report.2026.annual.summary.for.the.whole.company");

    expect(shortened.endsWith("…")).toBe(true);
  });
});
