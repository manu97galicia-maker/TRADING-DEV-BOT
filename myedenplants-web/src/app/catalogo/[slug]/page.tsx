import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Badge } from "@/components/Badge";
import { PlantArt, variantFor } from "@/components/PlantArt";
import { PlantCard } from "@/components/PlantCard";
import { formatPrice, getPlant, plants } from "@/data/plants";

type Params = { slug: string };

export function generateStaticParams(): Params[] {
  return plants.map((plant) => ({ slug: plant.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<Params>;
}): Promise<Metadata> {
  const { slug } = await params;
  const plant = getPlant(slug);

  if (!plant) return { title: "Planta no encontrada" };

  return {
    title: plant.name,
    description: `${plant.tagline} ${plant.description.slice(0, 110)}…`,
  };
}

export default async function PlantPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { slug } = await params;
  const plant = getPlant(slug);

  if (!plant) notFound();

  const relacionadas = plants
    .filter(
      (other) =>
        other.slug !== plant.slug &&
        other.categories.some((category) => plant.categories.includes(category)),
    )
    .slice(0, 3);

  const fichaTecnica = [
    ["Luz", plant.light],
    ["Riego", plant.watering],
    ["Nivel de cuidado", plant.difficulty],
    ["Tamaño", plant.size],
    ["Apta para mascotas", plant.petFriendly ? "Sí" : "No, mejor fuera de su alcance"],
  ];

  return (
    <div className="container-eden py-10 md:py-14">
      <nav aria-label="Migas de pan" className="text-sm text-eden-600">
        <Link href="/catalogo" className="hover:text-eden-900">
          Catálogo
        </Link>
        <span aria-hidden="true"> / </span>
        <span className="text-eden-900">{plant.name}</span>
      </nav>

      <div className="mt-8 grid gap-10 lg:grid-cols-2">
        <div className="self-start overflow-hidden rounded-3xl border border-eden-100 bg-sand-100">
          <PlantArt
            palette={plant.palette}
            variant={variantFor(plant.slug)}
            className="aspect-square w-full"
          />
        </div>

        <div>
          <h1 className="font-display text-4xl font-semibold text-eden-950">
            {plant.name}
          </h1>
          <p className="mt-2 text-lg italic text-eden-500">{plant.botanical}</p>

          <p className="mt-6 font-display text-3xl font-semibold text-eden-900">
            {formatPrice(plant.price)}
          </p>
          <p className="mt-1 text-sm text-eden-600">
            IVA incluido · Envío gratis a partir de 49 €
          </p>

          <div className="mt-6 flex flex-wrap gap-2">
            <Badge>{plant.light}</Badge>
            <Badge>{plant.watering}</Badge>
            <Badge>Cuidado {plant.difficulty.toLowerCase()}</Badge>
            {plant.petFriendly && <Badge>Pet friendly</Badge>}
          </div>

          <p className="mt-6 text-lg text-eden-800">{plant.description}</p>

          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href={`/contacto?planta=${plant.slug}`}
              className="rounded-full bg-eden-700 px-6 py-3 text-base font-semibold text-sand-50 transition-colors hover:bg-eden-800"
            >
              Reservar esta planta
            </Link>
            <Link
              href="/cuidados"
              className="rounded-full border border-eden-300 px-6 py-3 text-base font-semibold text-eden-800 transition-colors hover:bg-eden-50"
            >
              Guía de cuidados
            </Link>
          </div>

          <p className="mt-4 text-sm text-eden-600">
            {plant.stock > 8
              ? "Disponible en el vivero y para envío inmediato."
              : `Quedan ${plant.stock} unidades de este tamaño.`}
          </p>

          <dl className="mt-10 divide-y divide-eden-100 border-t border-eden-100">
            {fichaTecnica.map(([label, value]) => (
              <div key={label} className="flex justify-between gap-6 py-3 text-sm">
                <dt className="font-medium text-eden-600">{label}</dt>
                <dd className="text-right text-eden-900">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>

      <section className="mt-16 grid gap-10 rounded-3xl bg-eden-50 p-8 md:grid-cols-2 md:p-12">
        <div>
          <h2 className="font-display text-2xl font-semibold text-eden-950">
            Cómo cuidarla
          </h2>
          <ul className="mt-4 space-y-3">
            {plant.care.map((tip) => (
              <li key={tip} className="flex gap-3 text-eden-800">
                <span aria-hidden="true" className="text-eden-500">
                  ✓
                </span>
                <span>{tip}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h2 className="font-display text-2xl font-semibold text-eden-950">
            Cómo te llega
          </h2>
          <ul className="mt-4 space-y-3 text-eden-800">
            <li>Sale del invernadero el mismo día que preparamos el pedido.</li>
            <li>Cepellón envuelto y maceta sujeta con tutor interno.</li>
            <li>Caja rígida con ventilación, sin plásticos innecesarios.</li>
            <li>Si llega dañada, mandamos otra en 72 h sin coste.</li>
          </ul>
        </div>
      </section>

      {relacionadas.length > 0 && (
        <section className="mt-16">
          <h2 className="font-display text-2xl font-semibold text-eden-950">
            Combina bien con
          </h2>
          <div className="mt-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {relacionadas.map((otra) => (
              <PlantCard key={otra.slug} plant={otra} />
            ))}
          </div>
        </section>
      )}

      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            "@context": "https://schema.org",
            "@type": "Product",
            name: plant.name,
            description: plant.description,
            category: plant.categories.join(", "),
            offers: {
              "@type": "Offer",
              price: plant.price,
              priceCurrency: "EUR",
              availability:
                plant.stock > 0
                  ? "https://schema.org/InStock"
                  : "https://schema.org/OutOfStock",
            },
          }),
        }}
      />
    </div>
  );
}
