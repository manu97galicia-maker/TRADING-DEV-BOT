"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

const links = [
  { href: "/catalogo", label: "Catálogo" },
  { href: "/cuidados", label: "Cuidados" },
  { href: "/contacto", label: "Contacto" },
];

export function SiteHeader() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-50 border-b border-eden-100 bg-sand-50/85 backdrop-blur">
      <div className="container-eden flex h-16 items-center justify-between gap-4">
        <Link
          href="/"
          className="flex items-center gap-2 font-display text-lg font-semibold tracking-tight text-eden-900"
          onClick={() => setOpen(false)}
        >
          <span aria-hidden="true" className="text-xl">
            🌿
          </span>
          My Eden Plants
        </Link>

        <nav className="hidden items-center gap-1 md:flex">
          {links.map((link) => {
            const active = pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={`rounded-full px-4 py-2 text-sm font-medium transition-colors ${
                  active
                    ? "bg-eden-100 text-eden-900"
                    : "text-eden-700 hover:bg-eden-50 hover:text-eden-900"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
          <Link
            href="/contacto#visita"
            className="ml-2 rounded-full bg-eden-700 px-4 py-2 text-sm font-semibold text-sand-50 transition-colors hover:bg-eden-800"
          >
            Visitar el vivero
          </Link>
        </nav>

        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-controls="menu-movil"
          className="rounded-full border border-eden-200 px-3 py-2 text-sm font-medium text-eden-800 md:hidden"
        >
          {open ? "Cerrar" : "Menú"}
        </button>
      </div>

      {open && (
        <nav id="menu-movil" className="border-t border-eden-100 md:hidden">
          <div className="container-eden flex flex-col py-2">
            {links.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                onClick={() => setOpen(false)}
                className="rounded-lg px-2 py-3 text-base font-medium text-eden-800 hover:bg-eden-50"
              >
                {link.label}
              </Link>
            ))}
            <Link
              href="/contacto#visita"
              onClick={() => setOpen(false)}
              className="mt-2 mb-3 rounded-full bg-eden-700 px-4 py-3 text-center text-base font-semibold text-sand-50"
            >
              Visitar el vivero
            </Link>
          </div>
        </nav>
      )}
    </header>
  );
}
