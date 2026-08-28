import type { MetadataRoute } from "next";

import { plants } from "@/data/plants";

const BASE_URL = "https://myedenplants.com";

export default function sitemap(): MetadataRoute.Sitemap {
  const estaticas = ["", "/catalogo", "/cuidados", "/contacto"].map((path) => ({
    url: `${BASE_URL}${path}`,
    lastModified: new Date(),
    changeFrequency: "weekly" as const,
    priority: path === "" ? 1 : 0.8,
  }));

  const fichas = plants.map((plant) => ({
    url: `${BASE_URL}/catalogo/${plant.slug}`,
    lastModified: new Date(),
    changeFrequency: "monthly" as const,
    priority: 0.6,
  }));

  return [...estaticas, ...fichas];
}
