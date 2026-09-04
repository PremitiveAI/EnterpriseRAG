/**
 * EnterpriseRAG chat widget loader.
 *
 * A third-party page includes one <script> tag; this puts a launcher button in
 * the corner and opens the organization's public chat page in an iframe.
 *
 *   <script src="https://your-host/widget.js"
 *           data-organization="00000000-0000-0000-0000-000000000000"
 *           defer></script>
 *
 * Optional attributes: data-position="left", data-label="...",
 * data-accent="#15157d".
 *
 * The isolation is the iframe, not a sandbox attribute. The widget is served
 * from the app's origin and embedded on somebody else's, so the host page and
 * the chat are already cross-origin: neither can read the other's DOM, storage
 * or cookies. `sandbox` on top of that bought nothing and broke the page - an
 * opaque origin cannot satisfy the `crossorigin` font preloads Next.js emits,
 * so the panel rendered as unstyled serif text.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;

  var organization = script.getAttribute("data-organization");
  if (!organization) {
    // Silence here would look like "the widget is broken" with nothing to go on.
    console.error("[enterpriserag] widget.js needs a data-organization attribute");
    return;
  }

  if (document.getElementById("erag-widget-root")) return; // included twice

  // Declared here, not at the end of the file: they are used during mount, and
  // as globals they would both be undefined at that point (var hoists the name,
  // not the value) as well as leaking two names into the host page.
  var ICON_CHAT =
    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"' +
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9' +
    'L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5' +
    'a8.48 8.48 0 0 1 8 8v.5z"/></svg>';

  var ICON_CLOSE =
    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"' +
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M18 6 6 18M6 6l12 12"/></svg>';

  // The origin the script itself came from, so an embed never has to be told
  // where the app lives twice.
  var origin = new URL(script.src, window.location.href).origin;
  // ?embed=1 tells the page it is in a 400px panel rather than a browser tab,
  // so it can drop the page chrome and tighten its spacing.
  var chatUrl =
    origin + "/" + encodeURIComponent(organization) + "/chat?embed=1";

  var side = script.getAttribute("data-position") === "left" ? "left" : "right";
  var label = script.getAttribute("data-label") || "Chat with us";
  var accent = script.getAttribute("data-accent") || "#15157d";

  // --- Elements ---------------------------------------------------------- //

  var root = document.createElement("div");
  root.id = "erag-widget-root";

  var panel = document.createElement("div");
  panel.className = "erag-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", label);
  panel.hidden = true;

  var frame = document.createElement("iframe");
  frame.title = label;
  frame.setAttribute("loading", "lazy");
  // The src is set on first open, not now: an embed on a busy page should not
  // cost a page load and a model warm-up for a visitor who never clicks.
  panel.appendChild(frame);

  var launcher = document.createElement("button");
  launcher.className = "erag-launcher";
  launcher.type = "button";
  launcher.setAttribute("aria-label", label);
  launcher.setAttribute("aria-expanded", "false");
  launcher.innerHTML = ICON_CHAT;

  var style = document.createElement("style");
  style.textContent = css(side, accent);

  // --- Behaviour --------------------------------------------------------- //

  var open = false;

  function setOpen(next) {
    open = next;
    if (open && !frame.src) frame.src = chatUrl;

    panel.hidden = !open;
    // Toggled on the next frame so the panel has a layout to animate from.
    if (open) requestAnimationFrame(function () { panel.classList.add("is-open"); });
    else panel.classList.remove("is-open");

    launcher.setAttribute("aria-expanded", String(open));
    launcher.innerHTML = open ? ICON_CLOSE : ICON_CHAT;
    launcher.classList.toggle("is-open", open);
  }

  launcher.addEventListener("click", function () {
    setOpen(!open);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && open) {
      setOpen(false);
      launcher.focus();
    }
  });

  function mount() {
    document.head.appendChild(style);
    root.appendChild(panel);
    root.appendChild(launcher);
    document.body.appendChild(root);
  }

  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);

  // --- Presentation ------------------------------------------------------ //

  function css(side, accent) {
    return [
      "#erag-widget-root{position:fixed;bottom:20px;" + side + ":20px;",
      "z-index:2147483000;font-family:inherit}",

      ".erag-launcher{width:56px;height:56px;border:0;border-radius:9999px;",
      "cursor:pointer;background:" + accent + ";color:#fff;display:flex;",
      "align-items:center;justify-content:center;padding:0;",
      "box-shadow:0 6px 20px rgba(0,0,0,.24);position:relative;z-index:1}",
      ".erag-launcher:focus-visible{outline:3px solid " + accent + ";outline-offset:3px}",

      ".erag-panel{position:absolute;bottom:72px;" + side + ":0;",
      "width:400px;height:min(620px,calc(100vh - 130px));",
      "border-radius:20px;overflow:hidden;background:#fff;",
      "box-shadow:0 16px 48px rgba(0,0,0,.22);",
      // Transform-only transition: animating height would reflow the iframe on
      // every frame and make the whole panel judder.
      "opacity:0;transform:translateY(12px) scale(.98);transform-origin:bottom " + side + ";",
      "transition:opacity .16s ease-out,transform .16s ease-out}",
      ".erag-panel.is-open{opacity:1;transform:none}",
      ".erag-panel[hidden]{display:none}",
      ".erag-panel iframe{width:100%;height:100%;border:0;display:block}",

      // On a phone the panel is the screen. A 400px card floating over a 380px
      // viewport is unusable, and clipping it would hide the input.
      "@media (max-width:480px){",
      "#erag-widget-root{bottom:16px;" + side + ":16px}",
      ".erag-panel{position:fixed;inset:0;width:100%;height:100%;",
      "border-radius:0;transform:translateY(16px)}",
      ".erag-panel.is-open{transform:none}}",

      "@media (prefers-reduced-motion:reduce){",
      ".erag-panel{transition:none}}",
    ].join("");
  }
})();
