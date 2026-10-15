import Link from "next/link";
import { cookies, headers } from "next/headers";
import type { JobApplication } from "@/lib/types";
import ReviewClient from "./ReviewClient";

async function loadApplication(id: string): Promise<JobApplication | null> {
  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host");
  if (!host) return null;
  const proto = h.get("x-forwarded-proto") ?? "http";
  const cookieHeader = (await cookies()).toString();

  try {
    const res = await fetch(`${proto}://${host}/api/applications/${encodeURIComponent(id)}`, {
      cache: "no-store",
      headers: { cookie: cookieHeader },
    });
    if (!res.ok) return null;
    const data = await res.json();
    return (data?.application ?? data) as JobApplication;
  } catch {
    return null;
  }
}

export default async function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const app = await loadApplication(id);

  if (!app || !app.id) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-bg text-fg">
        <p>Application not found</p>
        <Link href="/applications" className="text-indigo-300 underline">
          Back to applications
        </Link>
      </div>
    );
  }

  return <ReviewClient app={app} />;
}
