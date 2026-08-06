import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/Nav";

// ponytail: system font stack (globals.css) instead of next/font/google —
// one less network dependency at build time for a single-user internal
// tool with no brand-font requirement.
export const metadata: Metadata = {
  title: "Kairodex",
  description: "AI-assisted options-buying paper trading research platform",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col">
        <Nav />
        <main className="flex-1 p-6">{children}</main>
      </body>
    </html>
  );
}
