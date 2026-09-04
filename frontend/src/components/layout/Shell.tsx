"use client";

import { Menu } from "lucide-react";
import { useState } from "react";
import { Sidebar } from "./Sidebar";
import type { SubjectType } from "@/lib/session";

/** Responsive shell. The Stitch dashboard is desktop-only; this is not. */
export function Shell({
  children,
  subjectType,
}: {
  children: React.ReactNode;
  subjectType?: SubjectType | null;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="min-h-full">
      <Sidebar open={open} onClose={() => setOpen(false)} subjectType={subjectType} />

      <div className="flex min-h-screen flex-col md:ml-[280px]">
        <header className="flex h-16 shrink-0 items-center gap-4 border-b border-outline-variant bg-surface px-6">
          <button
            onClick={() => setOpen(true)}
            aria-label="Open navigation"
            className="rounded-full p-2 text-on-surface-variant transition-colors hover:bg-surface-container-low md:hidden"
          >
            <Menu size={20} aria-hidden />
          </button>
          <span className="font-semibold text-primary md:hidden">EnterpriseRAG</span>

          {/* Brand mark, top-right. `hidden md:block` keeps it off phones,
              where the header already carries the menu button and the product
              name and has no room to spare.

              Served from `public/`, not from premitivekey.com: an external
              asset makes every page load depend on a third-party host, and
              fails silently when it is unreachable.

              `drop-shadow`, not `shadow`. The PNG has a transparent
              background, so a box shadow would outline the empty rectangle
              around the mark; a drop shadow follows the artwork itself. */}
          <div className="group relative ml-auto hidden h-11 origin-right [transform:perspective(700px)_rotateY(-14deg)_rotateX(7deg)] transition-transform duration-500 ease-out hover:[transform:perspective(700px)_rotateY(0deg)_rotateX(0deg)_translateY(-2px)_scale(1.05)] md:block lg:h-12">
            {/* Coloured halo, revealed on hover. */}
            <span
              aria-hidden
              className="pointer-events-none absolute inset-0 bg-primary opacity-0 blur-[7px] transition-opacity duration-500 group-hover:opacity-70 [mask-image:url(/premitivekey-logo.png)] [mask-position:center] [mask-repeat:no-repeat] [mask-size:contain] [-webkit-mask-image:url(/premitivekey-logo.png)] [-webkit-mask-position:center] [-webkit-mask-repeat:no-repeat] [-webkit-mask-size:contain]"
            />

            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/premitivekey-logo.png"
              alt="Premitive Key"
              width={720}
              height={196}
              className="relative h-full w-auto"
            />

            {/* Light sweep. Moving the background position rather than an
                element keeps it to one paintable property. */}
            <span
              aria-hidden
              className="pointer-events-none absolute inset-0 bg-[linear-gradient(105deg,transparent_38%,rgba(255,255,255,0.85)_50%,transparent_62%)] bg-[length:260%_100%] bg-[position:135%_0] transition-[background-position] duration-[900ms] ease-out group-hover:bg-[position:-35%_0] [mask-image:url(/premitivekey-logo.png)] [mask-position:center] [mask-repeat:no-repeat] [mask-size:contain] [-webkit-mask-image:url(/premitivekey-logo.png)] [-webkit-mask-position:center] [-webkit-mask-repeat:no-repeat] [-webkit-mask-size:contain]"
            />
          </div>
        </header>

        <main className="flex-1 px-6 py-8 md:px-12">{children}</main>

        <footer className="border-t border-outline-variant px-6 py-5 md:px-12">
          {/* The whole line is the link, not a word inside it. Same tab, so no
              target attribute. */}
          <a
            href="https://premitivekey.com/"
            className="block text-center text-sm font-medium text-on-surface-variant transition-colors hover:text-primary"
          >
            Designed and Developed by Premitive Key
          </a>
        </footer>
      </div>
    </div>
  );
}
