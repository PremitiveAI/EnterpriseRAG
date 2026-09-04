"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/Button";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const body = await response.json();

      if (!response.ok || !body.success) {
        // Wrong email and wrong password are deliberately indistinguishable.
        setError(body.message ?? "Sign in failed.");
        setPassword(""); // clear the password, keep the email
        return;
      }
      // This form is the Super Admin door. They own no organization, so the
      // dashboard - which counts one organization's documents - would greet
      // them with four failed tiles.
      router.push("/super-admin/organizations");
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-full items-center justify-center px-4 py-12">
      <div className="">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold tracking-tight text-primary">EnterpriseRAG</h1>
          <p className="mt-1 font-mono text-xs text-on-surface-variant">RAG Knowledge Base</p>
        </div>

        <form
          onSubmit={onSubmit}
          className="rounded-[32px] border border-outline-variant bg-surface-container-lowest p-8"
          noValidate
        >
          <h2 className="mb-6 text-lg font-semibold text-on-surface">Sign in</h2>

          {error && (
            <div
              role="alert"
              className="mb-5 rounded-full bg-error-container px-5 py-3 text-sm text-on-error-container"
            >
              {error}
            </div>
          )}

          <label className="mb-1.5 block text-sm font-medium text-on-surface-variant" htmlFor="email">
            Email
          </label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mb-5 h-11 w-full rounded-full border border-outline-variant bg-surface-container-lowest px-5 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary-fixed-dim"
          />

          <label className="mb-1.5 block text-sm font-medium text-on-surface-variant" htmlFor="password">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mb-7 h-11 w-full rounded-full border border-outline-variant bg-surface-container-lowest px-5 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary-fixed-dim"
          />

          <Button type="submit" disabled={submitting} className="w-full">
            {submitting ? " Signing in…" : "  Sign in  "}
          </Button>
        </form>

        <p className="mt-6 text-center font-mono text-[11px] text-outline">
          Accounts are created with scripts/create_admin.py
        </p>
      </div>
    </main>
  );
}
