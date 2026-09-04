import "@testing-library/jest-dom/vitest";

// jsdom implements no layout, so these are absent rather than broken. Stubbing
// them here keeps the stubs out of every individual test file.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
