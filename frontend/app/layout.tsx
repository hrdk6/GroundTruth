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
        {/* Newsreader is a text serif drawn for reading on screens, with the
            italics a sea chart letters its water in; Schibsted Grotesk is the
            interface. The mono is kept only for code and paths, where telling
            I, l and 1 apart is the whole job. */}
        <link
          href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible+Mono:wght@400;700&family=Newsreader:ital,opsz,wght@0,6..72,400..700;1,6..72,400..600&family=Schibsted+Grotesk:wght@400;500;600;700;800&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <div className="min-h-screen flex flex-col">
          <header className="on-harbor sticky top-0 z-30 bg-harbor/95 backdrop-blur-sm text-harbor-ink border-b border-harbor-line/70">
            <div className="mx-auto max-w-[1320px] px-4 sm:px-8 h-16 flex items-stretch gap-3 sm:gap-8">
              <Link href="/" aria-label="GroundTruth, home" className="flex items-center shrink-0">
                <Wordmark />
              </Link>
              <Nav />
              <p className="ml-auto hidden md:flex items-center serif italic text-[16px] text-harbor-dim">
                Kubernetes docs, answered with receipts
              </p>
            </div>
          </header>

          <main className="flex-1">{children}</main>

          <footer className="border-t border-rule mt-20">
            <div className="mx-auto max-w-[1320px] px-4 sm:px-8 py-8 text-[14px] text-ink-3 flex flex-wrap gap-x-8 gap-y-2">
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
