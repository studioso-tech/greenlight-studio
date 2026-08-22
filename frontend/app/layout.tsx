import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Greenlight Studio — Autonomous Film Intelligence Suite",
  description: "Powered by Gemini Enterprise Agent Platform & ClickHouse MCP for Blockbuster Decision-Making",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
