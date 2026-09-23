import type { Metadata } from "next";
import Link from "next/link";
import { Nav } from "@/components/nav";
import { Wordmark } from "@/components/primitives";
import "./globals.css";

export const metadata: Metadata = {
  title: "GroundTruth — answers you can check",
  description:
    "Question answering over the Kubernetes docs that cites every claim, verifies it against the source, and keeps releases apart.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        {/* Atkinson Hyperlegible was drawn for low-vision readers: letterforms
            that cannot be mistaken for each other (I l 1, O 0). This page's job
            is being read closely, so legibility is the brief, not a garnish. */}
        <link
          href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible+Mono:wght@400;500;700&family=Atkinson+Hyperlegible+Next:wght@400;500;700;800&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <div className="min-h-screen flex flex-col">
          <header className="sticky top-0 z-30 bg-paper/90 backdrop-blur-sm border-b border-rule">
            <div className="mx-auto max-w-[1320px] px-4 sm:px-8 h-14 flex items-stretch gap-3 sm:gap-8">
              <Link href="/" aria-label="GroundTruth, home" className="flex items-center shrink-0">
                <Wordmark />
              </Link>
              <Nav />
              <p className="ml-auto hidden md:flex items-center text-[13px] text-ink-3">
                Kubernetes docs, answered with receipts
              </p>
            </div>
          </header>

          <main className="flex-1">{children}</main>

          <footer className="border-t border-rule mt-16">
            <div className="mx-auto max-w-[1320px] px-4 sm:px-8 py-6 text-[13px] text-ink-3 flex flex-wrap gap-x-6 gap-y-2">
              <span>Answers quote the Kubernetes documentation, licensed CC BY 4.0.</span>
              <a href="https://github.com/hrdk6/GroundTruth" className="underline hover:text-ink">
                Source, experiments and the audit on GitHub
              </a>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
