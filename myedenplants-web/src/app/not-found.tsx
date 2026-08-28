import Link from "next/link";

export default function NotFound() {
  return (
    <div className="container-eden flex flex-col items-center py-24 text-center">
      <span aria-hidden="true" className="text-5xl">
        🪴
      </span>
      <h1 className="mt-6 font-display text-4xl font-semibold text-eden-950">
        Esta página no ha germinado
      </h1>
      <p className="mt-3 max-w-md text-eden-700">
        La dirección que buscas no existe o la planta ya no está en catálogo.
      </p>
      <Link
        href="/catalogo"
        className="mt-8 rounded-full bg-eden-700 px-6 py-3 font-semibold text-sand-50 hover:bg-eden-800"
      >
        Volver al catálogo
      </Link>
    </div>
  );
}
