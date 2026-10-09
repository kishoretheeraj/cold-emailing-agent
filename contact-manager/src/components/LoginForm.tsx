"use client";

import { useState } from "react";
import { Label, TextInput } from "@/components/Field";
import { safeNextPath } from "@/lib/loginNext";

export function LoginForm({ next }: { next?: string }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (res.ok) {
        window.location.assign(safeNextPath(next));
        return;
      }
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      setError(res.status === 401 ? "Wrong password." : body.error ?? "Could not sign in.");
    } catch {
      setError("Could not reach the server.");
    }
    setLoading(false);
  }

  return (
    <main className="mx-auto mt-24 w-full max-w-sm px-4">
      <form onSubmit={onSubmit} className="rounded-xl border border-border bg-surface p-6">
        <h1 className="mb-1 text-lg font-semibold text-fg">Sign in</h1>
        <p className="mb-5 text-sm text-fg-muted">Approving applications needs the operator password.</p>
        <Label required>Password</Label>
        <TextInput
          type="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-label="Password"
        />
        {error && (
          <p role="alert" className="mt-3 text-sm text-red-400">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={loading || password.length === 0}
          className="mt-5 w-full rounded-md bg-indigo-600 px-3 py-2 text-sm text-white disabled:opacity-50"
        >
          {loading ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </main>
  );
}
