import Link from "next/link";

import { PlantArt, variantFor } from "@/components/PlantArt";
import { formatPrice, type Plant } from "@/data/plants";

export function PlantCard({ plant }: { plant: Plant }) {
  return (
    <Link
      href={`/catalogo/${plant.slug}`}
      className="group flex flex-col overflow-hidden rounded-2xl border border-eden-100 bg-white transition-shadow hover:shadow-lg hover:shadow-eden-900/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-eden-600"
    >
      <div className="relative bg-sand-100">
        <PlantArt
          palette={plant.palette}
          variant={variantFor(plant.slug)}
          className="aspect-square w-full transition-transform duration-300 group-hover:scale-105"
        />
        {plant.stock <= 8 && (
          <span className="absolute left-3 top-3 rounded-full bg-clay-500 px-3 py-1 text-xs font-semibold text-white">
            Últimas {plant.stock}
          </span>
        )}
        {plant.petFriendly && (
          <span className="absolute right-3 top-3 rounded-full bg-white/90 px-3 py-1 text-xs font-semibold text-eden-800">
            Pet friendly
          </span>
        )}
      </div>

      <div className="flex flex-1 flex-col p-5">
        <h3 className="font-display text-lg font-semibold text-eden-900">
          {plant.name}
        </h3>
        <p className="mt-1 text-sm italic text-eden-500">{plant.botanical}</p>
        <p className="mt-3 flex-1 text-sm text-eden-700">{plant.tagline}</p>

        <div className="mt-4 flex items-center justify-between">
          <span className="font-display text-xl font-semibold text-eden-900">
            {formatPrice(plant.price)}
          </span>
          <span className="rounded-full bg-eden-50 px-3 py-1 text-xs font-medium text-eden-700">
            {plant.difficulty}
          </span>
        </div>
      </div>
    </Link>
  );
}
