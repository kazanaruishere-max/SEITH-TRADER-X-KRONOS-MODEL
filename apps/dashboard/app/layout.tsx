import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SEITH — AI Hedge Fund",
  description: "Personal AI hedge fund control dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="id">
      <body>{children}</body>
    </html>
  );
}
