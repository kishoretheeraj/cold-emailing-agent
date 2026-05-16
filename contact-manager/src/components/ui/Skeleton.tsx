"use client";

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div className={`bg-surface-2 animate-pulse rounded-md ${className}`} />
  );
}
