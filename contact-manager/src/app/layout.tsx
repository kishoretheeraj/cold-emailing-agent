import { Suspense } from "react";
import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import AppProviders from "@/components/AppProviders";
import { Nav } from "@/components/Nav";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Cold Email Ops — Contact Manager",
  description: "Add job search contacts to the cold email pipeline",
  manifest: "/manifest.json",
};

export const viewport: Viewport = {
  themeColor: "#1a1a2e",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} h-full antialiased`}>
      <body className="min-h-full">
        <AppProviders>
          {/* Nav reads the ?view= search param, which needs a Suspense boundary. */}
          <Suspense fallback={null}>
            <Nav />
          </Suspense>
          {children}
        </AppProviders>
      </body>
    </html>
  );
}
