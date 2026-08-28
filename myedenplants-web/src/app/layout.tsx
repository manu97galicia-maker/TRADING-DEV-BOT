import type { Metadata } from "next";
import { Fraunces, Inter } from "next/font/google";

import { SiteFooter } from "@/components/SiteFooter";
import { SiteHeader } from "@/components/SiteHeader";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-fraunces",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL("https://myedenplants.com"),
  title: {
    default: "My Eden Plants · Vivero urbano en Vigo",
    template: "%s · My Eden Plants",
  },
  description:
    "Plantas de interior y terraza seleccionadas una a una, con guías de cuidado claras y envío en 48-72 h a toda la península.",
  openGraph: {
    type: "website",
    locale: "es_ES",
    siteName: "My Eden Plants",
    title: "My Eden Plants · Vivero urbano en Vigo",
    description:
      "Plantas de interior y terraza seleccionadas una a una, con guías de cuidado claras.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es" className={`${inter.variable} ${fraunces.variable}`}>
      <body className="flex min-h-screen flex-col font-sans">
        <a
          href="#contenido"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-eden-800 focus:px-4 focus:py-2 focus:text-sand-50"
        >
          Saltar al contenido
        </a>
        <SiteHeader />
        <main id="contenido" className="flex-1">
          {children}
        </main>
        <SiteFooter />
      </body>
    </html>
  );
}
