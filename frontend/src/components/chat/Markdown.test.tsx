/**
 * The composer picks a shape per question, so every shape has to render.
 *
 * The security tests here matter as much as the formatting ones: this content
 * is written by a model that has just read admin-uploaded documents, so a
 * document is an injection vector into this component.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "@/components/chat/Markdown";

describe("Markdown — shapes", () => {
  it("renders a one-sentence answer as a plain paragraph", () => {
    const { container } = render(<Markdown>Twenty-four days per calendar year.</Markdown>);

    expect(screen.getByText("Twenty-four days per calendar year.")).toBeInTheDocument();
    // A short answer must not be dressed up as a list.
    expect(container.querySelector("ul")).toBeNull();
    expect(container.querySelector("ol")).toBeNull();
  });

  it("renders multiple paragraphs", () => {
    const { container } = render(<Markdown>{"First para.\n\nSecond para."}</Markdown>);
    expect(container.querySelectorAll("p")).toHaveLength(2);
  });

  it("renders a bulleted list", () => {
    const { container } = render(<Markdown>{"- PAN card\n- Address proof\n- Photos"}</Markdown>);

    expect(container.querySelector("ul")).not.toBeNull();
    expect(container.querySelectorAll("li")).toHaveLength(3);
    expect(screen.getByText("PAN card")).toBeInTheDocument();
  });

  it("renders a numbered list as ordered, not bulleted", () => {
    /* Order carries meaning in a procedure; a <ul> would lose it. */
    const { container } = render(
      <Markdown>{"1. Submit the claim\n2. Attach receipts\n3. Await approval"}</Markdown>,
    );

    expect(container.querySelector("ol")).not.toBeNull();
    expect(container.querySelector("ul")).toBeNull();
    expect(container.querySelectorAll("li")).toHaveLength(3);
  });

  it("renders a GFM table with headers and cells", () => {
    const table = [
      "| City type | Cap |",
      "| --- | --- |",
      "| Metro | 4,000 |",
      "| Other | 2,500 |",
    ].join("\n");

    const { container } = render(<Markdown>{table}</Markdown>);

    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelectorAll("th")).toHaveLength(2);
    expect(container.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(screen.getByText("4,000")).toBeInTheDocument();
  });

  it("lets a wide table scroll inside its own box", () => {
    /* Otherwise it stretches the 800px message column. */
    const { container } = render(
      <Markdown>{"| A | B |\n| --- | --- |\n| 1 | 2 |"}</Markdown>,
    );
    expect(container.querySelector(".overflow-x-auto")).not.toBeNull();
  });

  it("renders a fenced code block", () => {
    const { container } = render(
      <Markdown>{"```bash\ntesseract --version\n```"}</Markdown>,
    );

    expect(container.querySelector("pre")).not.toBeNull();
    expect(screen.getByText(/tesseract --version/)).toBeInTheDocument();
  });

  it("distinguishes inline code from a fenced block", () => {
    const { container } = render(<Markdown>{"Set `TESSERACT_CMD` in the env file."}</Markdown>);

    expect(container.querySelector("code")).not.toBeNull();
    expect(container.querySelector("pre")).toBeNull();
  });

  it("renders bold and blockquotes", () => {
    const { container } = render(<Markdown>{"**Important**\n\n> A quoted clause"}</Markdown>);

    expect(container.querySelector("strong")).not.toBeNull();
    expect(container.querySelector("blockquote")).not.toBeNull();
  });

  it("renders headings as sub-headings, never as page titles", () => {
    /* An answer inside a chat bubble must not out-rank the page's own h1. */
    const { container } = render(<Markdown># Leave policy</Markdown>);

    expect(container.querySelector("h1")).toBeNull();
    expect(container.querySelector("h3")).not.toBeNull();
  });

  it("renders an empty answer without crashing", () => {
    expect(() => render(<Markdown>{""}</Markdown>)).not.toThrow();
  });
});

describe("Markdown — untrusted content", () => {
  it("escapes raw HTML instead of parsing it", () => {
    /* A document could try to smuggle markup through the model. Without
       rehype-raw this is text, not an element. */
    const { container } = render(
      <Markdown>{'<img src=x onerror="alert(1)"> <b>bold?</b>'}</Markdown>,
    );

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toContain("<img");
  });

  it("does not execute a script tag", () => {
    const { container } = render(<Markdown>{'<script>alert(1)</script>'}</Markdown>);
    expect(container.querySelector("script")).toBeNull();
  });

  it("strips a javascript: link", () => {
    const { container } = render(<Markdown>{"[click](javascript:alert(1))"}</Markdown>);

    const link = container.querySelector("a");
    // Either rendered inert or not as a link at all — never with the href.
    expect(link?.getAttribute("href") ?? "").not.toContain("javascript:");
  });

  it("keeps an ordinary https link and opens it safely", () => {
    const { container } = render(<Markdown>{"[docs](https://example.com/x)"}</Markdown>);

    const link = container.querySelector("a");
    expect(link).toHaveAttribute("href", "https://example.com/x");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });
});
