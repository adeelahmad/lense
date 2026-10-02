import type { Metadata } from "next";

import { THEME_SCRIPT } from "@/components/app-shell/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Lens", template: "%s · Lens" },
  description: "An archive for recorded speech and video.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Applies a saved light/dark choice before first paint */}
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
