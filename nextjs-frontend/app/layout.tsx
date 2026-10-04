import type { Metadata, Viewport } from "next";

import { THEME_SCRIPT } from "@/components/app-shell/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Lens", template: "%s · Lens" },
  description: "Your life. Your AI.",
  // Setting icons here replaces Next's app/icon.svg and app/apple-icon.png links, so all of them are listed.
  icons: {
    icon: [{ url: "/icon.svg", type: "image/svg+xml" }],
    apple: "/apple-icon.png",
    other: [{ rel: "mask-icon", url: "/safari-pinned-tab.svg", color: "#1A73E8" }],
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#FFFFFF" },
    { media: "(prefers-color-scheme: dark)", color: "#202124" },
  ],
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
