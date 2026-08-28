# My Eden Plants · web

Sitio público del vivero My Eden Plants, hecho con Next.js 16 (App Router),
React 19 y Tailwind CSS 4.

## Arrancar en local

```bash
cd myedenplants-web
npm install
npm run dev      # http://localhost:3000
```

Build de producción:

```bash
npm run build
npm run start
```

## Estructura

```
src/
  app/
    layout.tsx              cabecera, pie, fuentes y metadatos globales
    page.tsx                portada
    catalogo/page.tsx       listado con filtros por categoría, luz y dificultad
    catalogo/[slug]/        ficha de cada planta (estática, con JSON-LD)
    cuidados/page.tsx       guía de riego, diagnóstico de problemas y calendario
    contacto/page.tsx       formulario + datos del vivero
    sitemap.ts, robots.ts   SEO
  components/
    SiteHeader / SiteFooter navegación (menú móvil incluido)
    PlantCard               tarjeta de producto
    PlantArt                ilustración SVG generada por planta
    ContactForm             formulario (cliente)
  data/plants.ts            catálogo: fuente única de datos
```

## Cómo añadir o editar plantas

Todo el catálogo vive en `src/data/plants.ts`. Añade un objeto al array
`plants` con su `slug` (se convierte en la URL), precio, luz, riego,
categorías y consejos de cuidado. El listado, los filtros, la ficha, el
sitemap y las plantas relacionadas se actualizan solos.

Las ilustraciones no son fotos: se generan en SVG a partir del par de colores
`palette` y de una de las cuatro variantes (`leafy`, `columnar`, `trailing`,
`rosette`). La variante se fija en `EXPLICIT` dentro de
`src/components/PlantArt.tsx`; si no está listada, se elige por hash del slug.
Para usar fotos reales, sustituye `<PlantArt />` por `next/image` en
`PlantCard` y en la ficha.

## Pendiente para producción

- El formulario de contacto no envía nada: hay que conectar el `onSubmit` de
  `ContactForm` con un endpoint o servicio de correo.
- El carrito y el pago no están implementados: el CTA de la ficha lleva al
  formulario de contacto.
- Textos, dirección, teléfono y datos de la empresa son de ejemplo.
