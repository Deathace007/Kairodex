import Link from "next/link";
import { SEGMENTS, SEGMENT_LABEL } from "@/lib/types";

export function Nav() {
  return (
    <nav
      className="flex items-center gap-6 border-b px-6 py-3 text-sm"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <Link href="/" className="font-semibold" style={{ color: "var(--text-primary)" }}>
        Kairodex
      </Link>
      <div className="flex gap-4">
        {SEGMENTS.map((seg) => (
          <Link
            key={seg}
            href={`/segment/${seg}`}
            style={{ color: "var(--text-secondary)" }}
            className="hover:underline"
          >
            {SEGMENT_LABEL[seg]}
          </Link>
        ))}
      </div>
    </nav>
  );
}
