import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "sage :: terminal",
  description: "SAGE personal intelligence console",
};

export const viewport = {
  themeColor: "#000000",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-black">{children}</body>
    </html>
  );
}