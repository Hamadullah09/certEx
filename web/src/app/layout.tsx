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
  // Declaring both schemes lets native widgets - scrollbars, range thumbs, the
  // date picker - follow the theme instead of staying light on a dark page.
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f1f7ff" },
    { media: "(prefers-color-scheme: dark)", color: "#0e141f" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      {/* `app-canvas` washes the top of the page in the primary and secondary
          tints. It sits on the body so the login screen, which renders outside
          the app shell, is coloured too. */}
      <body className="app-canvas min-h-dvh bg-background text-foreground antialiased">
        {/* Skip link: the results grid puts a large table between the page top
            and the content, so keyboard users need a way past the chrome. */}
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-primary focus:px-5 focus:py-3 focus:text-base focus:font-semibold focus:text-primary-foreground focus:shadow-lift"
        >
          Skip to main content
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
