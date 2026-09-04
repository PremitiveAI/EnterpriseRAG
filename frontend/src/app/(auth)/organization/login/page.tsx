import Link from "next/link";
import { OtpLoginForm } from "@/components/auth/OtpLoginForm";

export const metadata = { title: "Organization sign-in · EnterpriseRAG" };

/**
 * Organization Admin sign-in.
 *
 * Separate from /login, which is the Super Admin's password form. The two
 * subjects have different credentials and different route sets, so they get
 * different doors rather than one form that guesses.
 */
export default function OrganizationLoginPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background px-4">
      <div className="text-center">
        <h2 className="text-2xl font-bold text-primary">EnterpriseRAG</h2>
        <p className="mt-1 font-mono text-[11px] text-on-surface-variant">
          RAG Knowledge Base
        </p>
      </div>

      <OtpLoginForm />

      <p className="text-center font-mono text-[11px] text-on-surface-variant">
        Administrator?{" "}
        <Link href="/login" className="text-primary hover:underline">
          Sign in with a password
        </Link>
      </p>
    </main>
  );
}
