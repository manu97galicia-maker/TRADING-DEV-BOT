import type { Metadata } from "next";

import { ContactForm } from "@/components/ContactForm";
import { getPlant } from "@/data/plants";

export const metadata: Metadata = {
  title: "Contacto",
  description:
    "Escríbenos para elegir planta, reservar una del catálogo o resolver un problema. Vivero en Rúa das Camelias 42, Vigo.",
};

const horario = [
  ["Lunes", "Cerrado"],
  ["Martes a viernes", "10:00 - 14:00 y 17:00 - 20:00"],
  ["Sábado", "10:00 - 20:00"],
  ["Domingo", "11:00 - 14:00"],
];

export default async function ContactoPage({
  searchParams,
}: {
  searchParams: Promise<{ planta?: string }>;
}) {
  const { planta } = await searchParams;
  const seleccionada = planta ? getPlant(planta) : undefined;

  return (
    <div className="container-eden py-12 md:py-16">
      <header className="max-w-2xl">
        <h1 className="font-display text-4xl font-semibold text-eden-950">
          Hablamos
        </h1>
        <p className="mt-3 text-lg text-eden-700">
          {seleccionada
            ? `Vas a preguntarnos por la ${seleccionada.name}. Cuéntanos dónde la vas a poner y te decimos si encaja.`
            : "Cuéntanos cómo es tu casa y te proponemos dos o tres plantas que encajen. También resolvemos problemas de plantas que no has comprado aquí."}
        </p>
      </header>

      <div className="mt-10 grid gap-10 lg:grid-cols-[1.4fr_1fr]">
        <ContactForm plantaInicial={seleccionada?.slug} />

        <aside className="space-y-8">
          <section id="visita" className="rounded-2xl bg-eden-50 p-6">
            <h2 className="font-display text-xl font-semibold text-eden-900">
              Visita el vivero
            </h2>
            <p className="mt-3 text-eden-700">
              Rúa das Camelias 42
              <br />
              36202 Vigo, Pontevedra
            </p>
            <p className="mt-3 text-sm text-eden-600">
              Autobús 15 y 27 a 100 m. Aparcamiento en la calle, normalmente
              tranquilo por la mañana.
            </p>
            <dl className="mt-5 divide-y divide-eden-200 border-t border-eden-200 text-sm">
              {horario.map(([dia, horas]) => (
                <div key={dia} className="flex justify-between gap-4 py-2">
                  <dt className="text-eden-600">{dia}</dt>
                  <dd className="text-right font-medium text-eden-900">
                    {horas}
                  </dd>
                </div>
              ))}
            </dl>
          </section>

          <section className="rounded-2xl border border-eden-100 bg-white p-6">
            <h2 className="font-display text-xl font-semibold text-eden-900">
              Directo
            </h2>
            <ul className="mt-3 space-y-2 text-eden-700">
              <li>
                <a
                  href="mailto:hola@myedenplants.com"
                  className="underline underline-offset-4 hover:text-eden-900"
                >
                  hola@myedenplants.com
                </a>
              </li>
              <li>
                <a
                  href="tel:+34986000000"
                  className="underline underline-offset-4 hover:text-eden-900"
                >
                  +34 986 00 00 00
                </a>
              </li>
              <li className="text-sm text-eden-600">
                Respondemos en menos de 24 h laborables.
              </li>
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}
