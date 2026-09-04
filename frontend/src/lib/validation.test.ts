/**
 * Client validation must mirror the server's rules exactly (spec §16).
 *
 * It is UX only — the backend re-checks everything — but a client rule that
 * *disagrees* with the server is worse than no client rule: it either blocks a
 * file the server would accept, or promises acceptance the server refuses.
 */
import { describe, expect, it } from "vitest";
import { checkFiles, extensionOf, formatBytes } from "@/lib/validation";
import type { UploadLimits } from "@/types/api";

const LIMITS: UploadLimits = {
  max_document_size_mb: 20,
  max_image_size_mb: 2,
  max_files_per_batch: 20,
  document_extensions: ["docx", "pdf", "pptx", "txt"],
  image_extensions: ["jpeg", "jpg", "png", "webp"],
  legacy_extensions: ["doc", "ppt"],
};

function file(name: string, size = 1024): File {
  const blob = new Blob([new Uint8Array(size)]);
  return new File([blob], name, { type: "application/octet-stream" });
}

describe("extensionOf", () => {
  it("reads the last extension", () => {
    expect(extensionOf("report.final.pdf")).toBe("pdf");
  });

  it("lowercases", () => {
    expect(extensionOf("SCAN.PDF")).toBe("pdf");
  });

  it("returns empty for a dotfile or a bare name", () => {
    expect(extensionOf("README")).toBe("");
    expect(extensionOf(".gitignore")).toBe("");
  });
});

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [999, "999 B"],
    [1024, "1 KB"],
    [1024 * 1024, "1.0 MB"],
    [20 * 1024 * 1024, "20.0 MB"],
  ])("formats %i", (input, expected) => {
    expect(formatBytes(input)).toBe(expected);
  });
});

describe("checkFiles", () => {
  it("accepts every documented format", () => {
    const names = ["a.pdf", "b.docx", "c.pptx", "d.txt", "e.png", "f.jpg", "g.jpeg", "h.webp"];
    const results = checkFiles(names.map((n) => file(n)), LIMITS);
    expect(results.every((r) => r.status === "ready")).toBe(true);
  });

  it("rejects legacy formats with their own code and a suggestion", () => {
    const [doc, ppt] = checkFiles([file("old.doc"), file("deck.ppt")], LIMITS);

    expect(doc.errorCode).toBe("LEGACY_FORMAT_UNSUPPORTED");
    expect(doc.message).toContain(".docx");
    expect(ppt.message).toContain(".pptx");
  });

  it("rejects an unknown extension", () => {
    expect(checkFiles([file("archive.zip")], LIMITS)[0].errorCode).toBe("INVALID_FILE_TYPE");
  });

  it("rejects a file with no extension", () => {
    const [result] = checkFiles([file("README")], LIMITS);
    expect(result.errorCode).toBe("INVALID_FILE_TYPE");
    expect(result.message).toMatch(/no extension/i);
  });

  it("rejects an empty file", () => {
    expect(checkFiles([file("empty.pdf", 0)], LIMITS)[0].errorCode).toBe("FILE_EMPTY");
  });

  it("applies the smaller limit to images", () => {
    const threeMb = 3 * 1024 * 1024;
    const [image, document] = checkFiles(
      [file("photo.png", threeMb), file("report.pdf", threeMb)],
      LIMITS,
    );

    expect(image.errorCode).toBe("FILE_TOO_LARGE");
    expect(image.message).toContain("2 MB");
    expect(document.status).toBe("ready");
  });

  it("accepts a file exactly at the limit", () => {
    const exactly = 20 * 1024 * 1024;
    expect(checkFiles([file("big.pdf", exactly)], LIMITS)[0].status).toBe("ready");
  });

  it("flags a duplicate within the batch but keeps the first", () => {
    const [first, second] = checkFiles([file("same.pdf"), file("same.pdf")], LIMITS);

    expect(first.status).toBe("ready");
    expect(second.errorCode).toBe("DUPLICATE_IN_BATCH");
  });

  it("treats same name with a different size as distinct", () => {
    const results = checkFiles([file("same.pdf", 100), file("same.pdf", 200)], LIMITS);
    expect(results.every((r) => r.status === "ready")).toBe(true);
  });

  it("checks extension before size, so a .zip is never 'too large'", () => {
    const [result] = checkFiles([file("huge.zip", 999 * 1024 * 1024)], LIMITS);
    expect(result.errorCode).toBe("INVALID_FILE_TYPE");
  });

  it("uses the limits it is given rather than hard-coded ones", () => {
    /* The whole point of fetching /config/upload-limits (§16). */
    const tighter: UploadLimits = { ...LIMITS, max_document_size_mb: 1 };
    const [result] = checkFiles([file("report.pdf", 2 * 1024 * 1024)], tighter);

    expect(result.errorCode).toBe("FILE_TOO_LARGE");
    expect(result.message).toContain("1 MB");
  });
});
