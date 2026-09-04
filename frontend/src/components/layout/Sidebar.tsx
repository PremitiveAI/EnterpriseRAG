"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Bot,
  Building2,
  Cpu,
  FileText,
  LayoutDashboard,
  LogOut,
  MessageSquare,
  Settings,
  Tags,
  Upload,
} from "lucide-react";
import type { ReactNode } from "react";
import type { SubjectType } from "@/lib/session";

/**
 * ONE sidebar for every screen. The Stitch files ship three with three
 * divergent nav sets; that divergence is not preserved (ADR-008).
 */
const ORG_NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/upload", label: "Upload", icon: Upload },
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/settings", label: "Settings", icon: Settings },
];

/**
 * A Super Admin belongs to no organization, so every screen above answers 403
 * for them - the backend refuses an organization-scoped call made with no
 * organization in the token. Showing those links would be showing five doors
 * that are all locked, so they get only the screens that are theirs.
 */
const SUPER_ADMIN_NAV = [
  { href: "/super-admin/organizations", label: "Organizations", icon: Building2 },
  { href: "/super-admin/categories", label: "Categories", icon: Tags },
  { href: "/super-admin/agent-rules", label: "Agent Rules", icon: Bot },
  { href: "/super-admin/llm-providers", label: "LLM Providers", icon: Cpu },
];

export function Sidebar({
  open,
  onClose,
  slot,
  subjectType,
}: {
  open: boolean;
  onClose: () => void;
  slot?: ReactNode;
  subjectType?: SubjectType | null;
}) {
  const NAV = subjectType === "SUPER_ADMIN" ? SUPER_ADMIN_NAV : ORG_NAV;
  const pathname = usePathname();
  const router = useRouter();

  async function signOut() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-20 bg-inverse-surface/20 backdrop-blur-sm md:hidden"
          onClick={onClose}
          aria-hidden
        />
      )}

      <aside
        className={`fixed left-0 top-0 z-30 flex h-full w-[280px] flex-col border-r border-outline-variant bg-surface-container-lowest p-6 transition-transform duration-300 md:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="mb-8 flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary-container font-bold text-on-primary-container">
            ER
          </div>
          <div className="min-w-0">
            <h2 className="truncate text-[17px] font-bold leading-tight text-primary">EnterpriseRAG</h2>
            <p className="truncate font-mono text-[11px] text-on-surface-variant">
              {subjectType === "SUPER_ADMIN" ? "Super Admin" : "RAG Knowledge Base"}
            </p>
          </div>
        </div>

        <nav className="flex flex-1 flex-col gap-1 overflow-y-auto">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                onClick={onClose}
                aria-current={active ? "page" : undefined}
                className={`flex items-center gap-3 rounded-full px-5 py-2.5 text-sm transition-colors ${
                  active
                    ? "bg-secondary-container font-semibold text-primary"
                    : "text-on-surface-variant hover:bg-surface-container-low"
                }`}
              >
                <Icon size={18} aria-hidden />
                <span>{label}</span>
              </Link>
            );
          })}
          {slot}
        </nav>

        <div className="mt-auto border-t border-outline-variant pt-4">
          <button
            onClick={signOut}
            className="flex w-full items-center gap-3 rounded-full px-5 py-2.5 text-sm text-error transition-colors hover:bg-error-container hover:text-on-error-container"
          >
            <LogOut size={18} aria-hidden />
            <span>Sign out</span>
          </button>
        </div>
      </aside>
    </>
  );
}
