import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "GroundTruth",
  description: "A self-evaluating, version-aware RAG platform over the Kubernetes docs.",
};

const NAV = [
  { href: "/", label: "Ask" },
  { href: "/traces", label: "Traces" },
  { href: "/experiments", label: "Experiments" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <div className="min-h-screen flex flex-col">
          <header className="sticky top-0 z-30 bg-ink/95 backdrop-blur border-b border-line">
            <div className="mx-auto max-w-[1440px] px-4 sm:px-6 h-14 flex items-center gap-6">
              <Link href="/" className="flex items-center gap-2.5 shrink-0">
                {/* A survey benchmark disk: the mark that fixes a known point. */}
                <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
                  <circle cx="9" cy="9" r="8" fill="none" stroke="#E0B33A" strokeWidth="1.5" />
                  <circle cx="9" cy="9" r="2" fill="#E0B33A" />
                  <path d="M9 0v3M9 15v3M0 9h3M15 9h3" stroke="#E0B33A" strokeWidth="1.5" />
                </svg>
                <span className="font-semibold tracking-tight text-bright">GroundTruth</span>
              </Link>

              <nav className="flex items-center gap-1 text-sm">
                {NAV.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="px-3 py-1.5 rounded text-mute hover:text-bright hover:bg-raised transition-colors"
                  >
                    {item.label}
                  </Link>
                ))}
              </nav>

              <div className="ml-auto hidden sm:block mono text-[11px] text-dim">
                Kubernetes docs · 1.26 / 1.28 / 1.30
              </div>
            </div>
          </header>

          <main className="flex-1">{children}</main>
        </div>
      </body>
    </html>
  );
}
