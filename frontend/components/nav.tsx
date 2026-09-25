"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Ask" },
  { href: "/traces", label: "Traces" },
  { href: "/experiments", label: "Experiments" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Main" className="flex items-stretch gap-1 sm:gap-2 text-[15px] h-full">
      {NAV.map((item) => {
        const current = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={current ? "page" : undefined}
            className={`relative flex items-center px-2 sm:px-3 transition-colors ${
              current ? "text-harbor-ink font-semibold" : "text-harbor-dim hover:text-harbor-ink"
            }`}
          >
            {item.label}
            {/* The current page is marked by a periwinkle rule on the
                masthead's lower edge. */}
            {current && <span className="absolute inset-x-2 sm:inset-x-3 bottom-0 h-[3px] rounded-t bg-signal" />}
          </Link>
        );
      })}
    </nav>
  );
}
