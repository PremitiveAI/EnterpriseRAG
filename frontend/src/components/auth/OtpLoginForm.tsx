"use client";

import { AlertCircle, ArrowLeft, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";

type Step = "identifier" | "code";

const OTP_LENGTH = 4;

/**
 * Two-step Organization Admin sign-in.
 *
 * Step one asks for an email or mobile number; step two asks for the code. The
 * two are separate screens because the first response is deliberately
 * ambiguous - it says the same thing whether or not the identifier exists, so
 * the form cannot be used to discover which accounts are real.
 */
export function OtpLoginForm() {
  const router = useRouter();

  const [step, setStep] = useState<Step>("identifier");
  const [identifier, setIdentifier] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [devCode, setDevCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);

  const codeRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (step === "code") codeRef.current?.focus();
  }, [step]);

  // Cosmetic only: expiry is enforced server-side, and this countdown just
  // tells the admin whether it is worth typing.
  useEffect(() => {
    if (secondsLeft <= 0) return;
    const timer = setInterval(() => setSecondsLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(timer);
  }, [secondsLeft]);

  async function requestCode(event?: React.FormEvent) {
    event?.preventDefault();
    if (!identifier.trim()) return;

    setBusy(true);
    setError(null);

    try {
      const response = await fetch("/api/auth/otp/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier: identifier.trim() }),
      });
      const body = await response.json();

      if (!response.ok || !body.success) {
        setError(body.message ?? "Could not send a code.");
        return;
      }

      setStep("code");
      setNotice(body.message);
      setSecondsLeft(body.data?.expires_in_seconds ?? 300);
      // Present outside production only, so the admin is not guessing at a
      // value the environment has fixed.
      setDevCode(body.data?.development_code ?? null);
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  async function verify(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      const response = await fetch("/api/auth/otp/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier: identifier.trim(), otp: code.trim() }),
      });
      const body = await response.json();

      if (!response.ok || !body.success) {
        setError(body.message ?? "That code is not valid.");
        setCode("");
        codeRef.current?.focus();
        return;
      }

      router.push("/dashboard");
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  const minutes = Math.floor(secondsLeft / 60);
  const seconds = String(secondsLeft % 60).padStart(2, "0");

  return (
    <div className="w-full max-w-[420px] rounded-[32px] border border-outline-variant bg-surface-container-lowest p-8">
      <h1 className="text-lg font-semibold text-on-surface">
        {step === "identifier" ? "Organization sign-in" : "Enter your code"}
      </h1>
      <p className="mt-1 text-sm text-on-surface-variant">
        {step === "identifier"
          ? "Use the email address or mobile number registered for your organization."
          : `We sent a ${OTP_LENGTH}-digit code to ${identifier}.`}
      </p>

      {error && (
        <p className="mt-4 flex items-start gap-2 rounded-[20px] bg-error-container px-4 py-3 text-sm text-on-error-container">
          <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
          {error}
        </p>
      )}

      {step === "identifier" ? (
        <form onSubmit={requestCode} className="mt-6 space-y-4">
          <label className="block space-y-1.5">
            <span className="px-2 text-xs font-medium text-on-surface-variant">
              Email or mobile number
            </span>
            <input
              type="text"
              autoComplete="username"
              value={identifier}
              onChange={(event) => setIdentifier(event.target.value)}
              placeholder="you@organization.example"
              className={inputClass}
              required
            />
          </label>

          <Button type="submit" className="w-full" disabled={busy || !identifier.trim()}>
            {busy && <Loader2 size={15} className="animate-spin" aria-hidden />}
            Send code
          </Button>
        </form>
      ) : (
        <form onSubmit={verify} className="mt-6 space-y-4">
          {devCode && (
            <p className="rounded-[20px] bg-surface-container-high px-4 py-3 text-sm text-on-surface-variant">
              Development environment — your code is{" "}
              <span className="font-mono font-semibold text-on-surface">{devCode}</span>.
              Codes are not sent by email or SMS until Release&nbsp;3.
            </p>
          )}

          <label className="block space-y-1.5">
            <span className="px-2 text-xs font-medium text-on-surface-variant">
              {OTP_LENGTH}-digit code
            </span>
            <input
              ref={codeRef}
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={OTP_LENGTH}
              value={code}
              onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
              className={`${inputClass} text-center font-mono text-lg tracking-[0.5em]`}
              required
            />
          </label>

          <p className="px-2 text-xs text-on-surface-variant">
            {secondsLeft > 0
              ? `Expires in ${minutes}:${seconds}`
              : "This code has expired — request a new one."}
          </p>

          <Button
            type="submit"
            className="w-full"
            disabled={busy || code.length < OTP_LENGTH}
          >
            {busy && <Loader2 size={15} className="animate-spin" aria-hidden />}
            Sign in
          </Button>

          <div className="flex items-center justify-between pt-1">
            <button
              type="button"
              onClick={() => {
                setStep("identifier");
                setCode("");
                setError(null);
                setNotice(null);
              }}
              className="inline-flex items-center gap-1.5 text-sm text-on-surface-variant hover:text-on-surface"
            >
              <ArrowLeft size={14} aria-hidden />
              Change address
            </button>

            <button
              type="button"
              onClick={() => requestCode()}
              disabled={busy}
              className="text-sm text-primary hover:underline disabled:opacity-50"
            >
              Resend code
            </button>
          </div>
        </form>
      )}

      {notice && step === "code" && (
        <p className="mt-4 text-xs text-on-surface-variant">{notice}</p>
      )}
    </div>
  );
}

const inputClass =
  "h-11 w-full rounded-full border border-outline-variant bg-surface-container-lowest px-5 text-sm text-on-surface outline-none placeholder:text-on-surface-variant focus:border-primary disabled:opacity-50";
