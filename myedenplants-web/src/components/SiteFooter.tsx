import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="mt-24 border-t border-eden-100 bg-eden-900 text-eden-100">
      <div className="container-eden grid gap-10 py-14 md:grid-cols-4">
        <div className="md:col-span-2">
          <p className="font-display text-xl font-semibold text-sand-50">
            🌿 My Eden Plants
          </p>
          <p className="mt-3 max-w-sm text-sm text-eden-200">
            Vivero urbano en Vigo. Cultivamos y seleccionamos plantas de
            interior y terraza, y te acompañamos para que sobrevivan al
            primer invierno.
          </p>
        </div>

        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-eden-300">
            Tienda
          </h2>
          <ul className="mt-3 space-y-2 text-sm">
            <li>
              <Link href="/catalogo" className="hover:text-white">
                Todo el catálogo
              </Link>
            </li>
            <li>
              <Link href="/catalogo?categoria=Interior" className="hover:text-white">
                Plantas de interior
              </Link>
            </li>
            <li>
              <Link href="/catalogo?categoria=Pet+friendly" className="hover:text-white">
                Pet friendly
              </Link>
            </li>
            <li>
              <Link href="/cuidados" className="hover:text-white">
                Guías de cuidado
              </Link>
            </li>
          </ul>
        </div>

        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-eden-300">
            Vivero
          </h2>
          <ul className="mt-3 space-y-2 text-sm text-eden-200">
            <li>Rúa das Camelias 42, Vigo</li>
            <li>Martes a sábado, 10:00 - 20:00</li>
            <li>
              <a href="mailto:hola@myedenplants.com" className="hover:text-white">
                hola@myedenplants.com
              </a>
            </li>
            <li>
              <a href="tel:+34986000000" className="hover:text-white">
                +34 986 00 00 00
              </a>
            </li>
          </ul>
        </div>
      </div>

      <div className="border-t border-eden-800">
        <div className="container-eden flex flex-col gap-2 py-6 text-xs text-eden-300 sm:flex-row sm:items-center sm:justify-between">
          <p>© {new Date().getFullYear()} My Eden Plants. Todos los derechos reservados.</p>
          <p>Envíos a península en 48-72 h · Garantía de llegada viva</p>
        </div>
      </div>
    </footer>
  );
}
