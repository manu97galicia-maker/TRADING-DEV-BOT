import Link from "next/link";

import { PlantArt, variantFor } from "@/components/PlantArt";
import { PlantCard } from "@/components/PlantCard";
import { plants } from "@/data/plants";

const destacadas = ["monstera-deliciosa", "pothos-dorado", "calathea-orbifolia", "olivo-bonsai"];

const porLuz = [
  {
    luz: "Sombra",
    titulo: "Rincones sin ventana",
    texto: "Pasillos, baños interiores y esa mesa que nunca ve el sol.",
    emoji: "🌙",
  },
  {
    luz: "Luz indirecta",
    titulo: "Cerca de una ventana",
    texto: "El caso más común en un piso: claridad sin sol directo.",
    emoji: "🌤️",
  },
  {
    luz: "Sol directo",
    titulo: "Balcón y terraza",
    texto: "Sol de verdad varias horas al día, mediterráneo puro.",
    emoji: "☀️",
  },
];

const promesas = [
  {
    titulo: "Cultivo propio",
    texto:
      "El 70% del catálogo se cultiva en nuestro invernadero de Vigo. Nada de plantas que llevan meses en un almacén.",
  },
  {
    titulo: "Llega viva o te la reponemos",
    texto:
      "Embalaje con tutor interno y caja rígida. Si llega dañada, mandamos otra sin preguntas en 72 h.",
  },
  {
    titulo: "Ficha de cuidados real",
    texto:
      "Cada planta va con instrucciones concretas: cuánta agua, qué luz y qué hacer cuando amarillea una hoja.",
  },
];

const testimonios = [
  {
    texto:
      "Me habían muerto cuatro plantas seguidas. Me recomendaron un potos y una sansevieria para el pasillo y llevan año y medio.",
    autor: "Lucía R.",
    lugar: "Compra online",
  },
  {
    texto:
      "El ficus llegó impecable, con el tronco atado a la caja. Se nota que lo empaquetó alguien que sabe.",
    autor: "Andrés M.",
    lugar: "Envío a Madrid",
  },
  {
    texto:
      "Fui al vivero sin saber qué quería y salí con la calathea y media hora de clase magistral sobre humedad.",
    autor: "Nerea F.",
    lugar: "Tienda de Vigo",
  },
];

export default function HomePage() {
  const seleccion = destacadas
    .map((slug) => plants.find((plant) => plant.slug === slug))
    .filter((plant) => plant !== undefined);

  return (
    <>
      <section className="border-b border-eden-100 bg-gradient-to-b from-eden-50 to-sand-50">
        <div className="container-eden grid items-center gap-12 py-16 md:grid-cols-2 md:py-24">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full border border-eden-200 bg-white px-4 py-1.5 text-sm font-medium text-eden-700">
              🌱 Vivero urbano en Vigo desde 2016
            </p>
            <h1 className="mt-6 font-display text-4xl font-semibold leading-tight text-eden-950 sm:text-5xl md:text-6xl">
              Plantas que sí sobreviven a tu casa
            </h1>
            <p className="mt-6 max-w-lg text-lg text-eden-700">
              Te preguntamos por tu luz, tus horarios y tus gatos, y te
              mandamos la planta que encaja. Con ficha de cuidados y alguien
              al otro lado del email cuando algo se tuerce.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                href="/catalogo"
                className="rounded-full bg-eden-700 px-6 py-3 text-base font-semibold text-sand-50 transition-colors hover:bg-eden-800"
              >
                Ver el catálogo
              </Link>
              <Link
                href="/cuidados"
                className="rounded-full border border-eden-300 px-6 py-3 text-base font-semibold text-eden-800 transition-colors hover:bg-eden-50"
              >
                Guías de cuidado
              </Link>
            </div>
            <dl className="mt-10 grid max-w-md grid-cols-3 gap-6">
              {[
                ["+9.000", "plantas enviadas"],
                ["48-72 h", "de entrega"],
                ["4,9/5", "en reseñas"],
              ].map(([valor, etiqueta]) => (
                <div key={etiqueta}>
                  <dt className="font-display text-2xl font-semibold text-eden-900">
                    {valor}
                  </dt>
                  <dd className="text-sm text-eden-600">{etiqueta}</dd>
                </div>
              ))}
            </dl>
          </div>

          <div className="relative">
            <div className="grid grid-cols-2 gap-4">
              {seleccion.slice(0, 4).map((plant, index) => (
                <div
                  key={plant.slug}
                  className={`overflow-hidden rounded-3xl border border-eden-100 bg-white shadow-sm ${
                    index % 2 === 1 ? "translate-y-6" : ""
                  }`}
                >
                  <PlantArt
                    palette={plant.palette}
                    variant={variantFor(plant.slug)}
                    className="aspect-square w-full"
                  />
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="container-eden py-16 md:py-20">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="font-display text-3xl font-semibold text-eden-950">
              Las que más salen del invernadero
            </h2>
            <p className="mt-2 text-eden-700">
              Cuatro apuestas seguras según lo que nos piden cada semana.
            </p>
          </div>
          <Link
            href="/catalogo"
            className="text-sm font-semibold text-eden-700 underline underline-offset-4 hover:text-eden-900"
          >
            Ver las {plants.length} plantas →
          </Link>
        </div>

        <div className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {seleccion.map((plant) => (
            <PlantCard key={plant.slug} plant={plant} />
          ))}
        </div>
      </section>

      <section className="bg-eden-900 py-16 text-sand-50 md:py-20">
        <div className="container-eden">
          <h2 className="font-display text-3xl font-semibold">
            Empieza por la luz que tienes, no por la planta que te gusta
          </h2>
          <p className="mt-2 max-w-2xl text-eden-200">
            Es el error número uno. Mira cuánta luz entra en el sitio donde la
            vas a poner y filtra el catálogo por ahí.
          </p>

          <div className="mt-10 grid gap-6 md:grid-cols-3">
            {porLuz.map((item) => (
              <Link
                key={item.luz}
                href={`/catalogo?luz=${encodeURIComponent(item.luz)}`}
                className="group rounded-2xl border border-eden-700 bg-eden-800 p-6 transition-colors hover:bg-eden-700"
              >
                <span aria-hidden="true" className="text-3xl">
                  {item.emoji}
                </span>
                <h3 className="mt-4 font-display text-xl font-semibold">
                  {item.titulo}
                </h3>
                <p className="mt-2 text-sm text-eden-200">{item.texto}</p>
                <p className="mt-4 text-sm font-semibold text-eden-300 group-hover:text-white">
                  Ver plantas para {item.luz.toLowerCase()} →
                </p>
              </Link>
            ))}
          </div>
        </div>
      </section>

      <section className="container-eden py-16 md:py-20">
        <div className="grid gap-10 md:grid-cols-3">
          {promesas.map((promesa) => (
            <div key={promesa.titulo}>
              <h3 className="font-display text-xl font-semibold text-eden-900">
                {promesa.titulo}
              </h3>
              <p className="mt-3 text-eden-700">{promesa.texto}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="border-y border-eden-100 bg-sand-100 py-16 md:py-20">
        <div className="container-eden">
          <h2 className="font-display text-3xl font-semibold text-eden-950">
            Lo que nos cuentan
          </h2>
          <div className="mt-8 grid gap-6 md:grid-cols-3">
            {testimonios.map((testimonio) => (
              <figure
                key={testimonio.autor}
                className="rounded-2xl border border-eden-100 bg-white p-6"
              >
                <blockquote className="text-eden-800">
                  «{testimonio.texto}»
                </blockquote>
                <figcaption className="mt-4 text-sm text-eden-600">
                  <span className="font-semibold text-eden-800">
                    {testimonio.autor}
                  </span>{" "}
                  · {testimonio.lugar}
                </figcaption>
              </figure>
            ))}
          </div>
        </div>
      </section>

      <section className="container-eden py-16 md:py-20">
        <div className="rounded-3xl bg-eden-700 px-8 py-12 text-center text-sand-50 md:px-16">
          <h2 className="font-display text-3xl font-semibold">
            ¿No sabes cuál elegir?
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-eden-100">
            Cuéntanos cómo es tu casa y te proponemos dos o tres opciones. Sin
            compromiso y sin venderte el ficus más caro.
          </p>
          <Link
            href="/contacto"
            className="mt-8 inline-block rounded-full bg-sand-50 px-6 py-3 text-base font-semibold text-eden-900 transition-colors hover:bg-white"
          >
            Escríbenos
          </Link>
        </div>
      </section>
    </>
  );
}
