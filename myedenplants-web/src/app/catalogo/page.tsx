import type { Metadata } from "next";
import Link from "next/link";

import { PlantCard } from "@/components/PlantCard";
import { categories, plants, type Plant } from "@/data/plants";

export const metadata: Metadata = {
  title: "Catálogo",
  description:
    "Todas nuestras plantas de interior y terraza, filtrables por categoría, luz disponible y nivel de cuidado.",
};

type SearchParams = {
  categoria?: string;
  luz?: string;
  dificultad?: string;
  orden?: string;
};

const luces = ["Sombra", "Luz indirecta", "Sol directo"];
const dificultades = ["Fácil", "Media", "Exigente"];

function buildHref(params: SearchParams, key: keyof SearchParams, value?: string) {
  const next = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...params, [key]: value })) {
    if (v) next.set(k, v);
  }
  const query = next.toString();
  return query ? `/catalogo?${query}` : "/catalogo";
}

function FilterGroup({
  label,
  options,
  paramKey,
  params,
}: {
  label: string;
  options: string[];
  paramKey: keyof SearchParams;
  params: SearchParams;
}) {
  const current = params[paramKey];

  return (
    <div>
      <h2 className="text-xs font-semibold uppercase tracking-wide text-eden-500">
        {label}
      </h2>
      <div className="mt-3 flex flex-wrap gap-2">
        <Link
          href={buildHref(params, paramKey, undefined)}
          className={`rounded-full px-3 py-1.5 text-sm transition-colors ${
            current
              ? "border border-eden-200 bg-white text-eden-700 hover:bg-eden-50"
              : "bg-eden-700 text-sand-50"
          }`}
        >
          Todas
        </Link>
        {options.map((option) => {
          const active = current === option;
          return (
            <Link
              key={option}
              href={buildHref(params, paramKey, active ? undefined : option)}
              aria-pressed={active}
              className={`rounded-full px-3 py-1.5 text-sm transition-colors ${
                active
                  ? "bg-eden-700 text-sand-50"
                  : "border border-eden-200 bg-white text-eden-700 hover:bg-eden-50"
              }`}
            >
              {option}
            </Link>
          );
        })}
      </div>
    </div>
  );
}

function sortPlants(list: Plant[], orden?: string): Plant[] {
  const sorted = [...list];
  if (orden === "precio-asc") return sorted.sort((a, b) => a.price - b.price);
  if (orden === "precio-desc") return sorted.sort((a, b) => b.price - a.price);
  return sorted.sort((a, b) => a.name.localeCompare(b.name, "es"));
}

export default async function CatalogoPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;

  const filtradas = sortPlants(
    plants.filter((plant) => {
      if (params.categoria && !plant.categories.includes(params.categoria)) {
        return false;
      }
      if (params.luz && plant.light !== params.luz) return false;
      if (params.dificultad && plant.difficulty !== params.dificultad) {
        return false;
      }
      return true;
    }),
    params.orden,
  );

  return (
    <div className="container-eden py-12 md:py-16">
      <header className="max-w-2xl">
        <h1 className="font-display text-4xl font-semibold text-eden-950">
          Catálogo
        </h1>
        <p className="mt-3 text-lg text-eden-700">
          {plants.length} plantas seleccionadas a mano. Filtra por el sitio que
          tienes libre y por el tiempo que quieras dedicarle.
        </p>
      </header>

      <div className="mt-10 space-y-6 rounded-2xl border border-eden-100 bg-white p-6">
        <FilterGroup
          label="Categoría"
          options={categories}
          paramKey="categoria"
          params={params}
        />
        <FilterGroup label="Luz" options={luces} paramKey="luz" params={params} />
        <FilterGroup
          label="Nivel de cuidado"
          options={dificultades}
          paramKey="dificultad"
          params={params}
        />

        <div className="flex flex-wrap items-center justify-between gap-4 border-t border-eden-100 pt-6">
          <p className="text-sm text-eden-600">
            {filtradas.length}{" "}
            {filtradas.length === 1 ? "planta encontrada" : "plantas encontradas"}
          </p>
          <div className="flex flex-wrap gap-2">
            {[
              ["", "Nombre"],
              ["precio-asc", "Precio ↑"],
              ["precio-desc", "Precio ↓"],
            ].map(([value, label]) => {
              const active = (params.orden ?? "") === value;
              return (
                <Link
                  key={label}
                  href={buildHref(params, "orden", value || undefined)}
                  className={`rounded-full px-3 py-1.5 text-sm transition-colors ${
                    active
                      ? "bg-eden-100 font-semibold text-eden-900"
                      : "text-eden-600 hover:bg-eden-50"
                  }`}
                >
                  {label}
                </Link>
              );
            })}
          </div>
        </div>
      </div>

      {filtradas.length > 0 ? (
        <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filtradas.map((plant) => (
            <PlantCard key={plant.slug} plant={plant} />
          ))}
        </div>
      ) : (
        <div className="mt-10 rounded-2xl border border-dashed border-eden-200 p-12 text-center">
          <p className="font-display text-xl text-eden-900">
            Ninguna planta cumple ese filtro
          </p>
          <p className="mt-2 text-eden-600">
            Prueba a quitar alguna condición o escríbenos y te buscamos algo a
            medida.
          </p>
          <Link
            href="/catalogo"
            className="mt-6 inline-block rounded-full bg-eden-700 px-5 py-2.5 text-sm font-semibold text-sand-50 hover:bg-eden-800"
          >
            Limpiar filtros
          </Link>
        </div>
      )}
    </div>
  );
}
