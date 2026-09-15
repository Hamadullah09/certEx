import type { Metadata, Viewport } from "next";

import { Providers } from "@/components/providers";

import "./globals.css";

const appName = process.env.NEXT_PUBLIC_APP_NAME ?? "CertExtract";

export const metadata: Metadata = {
  title: {
    default: appName,
    template: `%s · ${appName}`,
  },
  description:
    "Batch extraction of structured fields from birth, marriage and death certificates.",
  // These pages render personal identity data; keep them out of search indexes
  // and out of any archiver that honours the directive.
  robots: { index: false, follow: false, nocache: true },
  applicationName: appName,
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#16181d" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-dvh bg-background text-foreground antialiased">
        {/* Skip link: the results grid puts a large table between the page top
            and the content, so keyboard users need a way past the chrome. */}
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
        >
          Skip to main content
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
