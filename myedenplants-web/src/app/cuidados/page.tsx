import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Cuidados",
  description:
    "Guía práctica de riego, luz y diagnóstico de problemas para plantas de interior y terraza.",
};

const reglas = [
  {
    titulo: "El riego se mide con el dedo, no con el calendario",
    texto:
      "Mete el dedo dos o tres centímetros en el sustrato. Si sale seco, riega. Si sale húmedo, espera. Un calendario fijo mata más plantas que el olvido.",
  },
  {
    titulo: "Riega poco y bien, no mucho y a menudo",
    texto:
      "Cuando toque, riega hasta que salga agua por el agujero de drenaje, espera diez minutos y vacía el plato. Encharcar es la causa número uno de raíces podridas.",
  },
  {
    titulo: "La luz brillante indirecta es más luz de la que crees",
    texto:
      "Significa a menos de un metro de una ventana. Al fondo del salón hay bastante menos luz de la que percibe el ojo.",
  },
  {
    titulo: "En invierno casi todo se para",
    texto:
      "Menos luz y menos temperatura significan menos agua y nada de abono. Entre noviembre y febrero, reduce el riego a la mitad.",
  },
];

const diagnostico = [
  {
    sintoma: "Hojas amarillas y blandas, sustrato húmedo",
    causa: "Exceso de riego",
    solucion:
      "Deja secar el sustrato del todo, comprueba que la maceta drena y baja la frecuencia. Si el tallo está blando en la base, saca el cepellón y corta raíces podridas.",
  },
  {
    sintoma: "Puntas marrones y secas",
    causa: "Aire seco o cal en el agua",
    solucion:
      "Aleja la planta del radiador, agrupa varias plantas juntas y riega con agua reposada 24 h o filtrada.",
  },
  {
    sintoma: "Hojas nuevas pequeñas y tallos alargados",
    causa: "Falta de luz",
    solucion:
      "Acércala a una ventana. Si no hay más luz disponible, cambia a una especie de sombra como sansevieria o zamioculca.",
  },
  {
    sintoma: "Manchas marrones secas en el centro de la hoja",
    causa: "Quemadura de sol directo",
    solucion:
      "Filtra la luz con una cortina fina o retírala del alféizar en las horas centrales del día.",
  },
  {
    sintoma: "Telarañas finas en el envés y punteado claro",
    causa: "Araña roja",
    solucion:
      "Ducha las hojas, sube la humedad ambiental y trata con jabón potásico una vez por semana durante tres semanas.",
  },
  {
    sintoma: "Mosquitas negras alrededor de la maceta",
    causa: "Sustrato demasiado húmedo",
    solucion:
      "Deja secar más entre riegos, añade una capa de arena o grava encima y coloca trampas amarillas.",
  },
];

const calendario = [
  {
    estacion: "Primavera",
    tareas: [
      "Trasplanta las que se hayan quedado pequeñas de maceta.",
      "Empieza a abonar cada 3 semanas.",
      "Aumenta el riego a medida que suben las temperaturas.",
    ],
  },
  {
    estacion: "Verano",
    tareas: [
      "Riega por la mañana temprano o al atardecer.",
      "Aleja las de interior del aire acondicionado.",
      "Vigila plagas: con calor se multiplican rápido.",
    ],
  },
  {
    estacion: "Otoño",
    tareas: [
      "Reduce el abono y luego suspéndelo.",
      "Mete dentro las plantas sensibles al frío.",
      "Limpia las hojas para aprovechar la luz que queda.",
    ],
  },
  {
    estacion: "Invierno",
    tareas: [
      "Riega la mitad y solo si el sustrato está seco.",
      "Nada de trasplantes ni podas fuertes.",
      "Aparta las macetas de radiadores y corrientes de aire.",
    ],
  },
];

export default function CuidadosPage() {
  return (
    <div className="container-eden py-12 md:py-16">
      <header className="max-w-2xl">
        <h1 className="font-display text-4xl font-semibold text-eden-950">
          Cuidados sin misterio
        </h1>
        <p className="mt-3 text-lg text-eden-700">
          Todo lo que contamos en el mostrador, escrito. Cuatro reglas, una
          tabla para diagnosticar problemas y qué hacer en cada estación.
        </p>
      </header>

      <section className="mt-12">
        <h2 className="font-display text-2xl font-semibold text-eden-950">
          Las cuatro reglas que evitan el 90% de los problemas
        </h2>
        <div className="mt-6 grid gap-6 md:grid-cols-2">
          {reglas.map((regla, index) => (
            <article
              key={regla.titulo}
              className="rounded-2xl border border-eden-100 bg-white p-6"
            >
              <span className="font-display text-3xl font-semibold text-eden-200">
                {String(index + 1).padStart(2, "0")}
              </span>
              <h3 className="mt-2 font-display text-lg font-semibold text-eden-900">
                {regla.titulo}
              </h3>
              <p className="mt-2 text-eden-700">{regla.texto}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="mt-16">
        <h2 className="font-display text-2xl font-semibold text-eden-950">
          Mi planta tiene algo: diagnóstico rápido
        </h2>
        <div className="mt-6 overflow-x-auto rounded-2xl border border-eden-100 bg-white">
          <table className="w-full min-w-3xl border-collapse text-left text-sm">
            <thead className="bg-eden-50 text-eden-800">
              <tr>
                <th scope="col" className="p-4 font-semibold">
                  Qué ves
                </th>
                <th scope="col" className="p-4 font-semibold">
                  Causa probable
                </th>
                <th scope="col" className="p-4 font-semibold">
                  Qué hacer
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-eden-100">
              {diagnostico.map((fila) => (
                <tr key={fila.sintoma} className="align-top">
                  <th scope="row" className="p-4 font-medium text-eden-900">
                    {fila.sintoma}
                  </th>
                  <td className="p-4 text-eden-700">{fila.causa}</td>
                  <td className="p-4 text-eden-700">{fila.solucion}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mt-16">
        <h2 className="font-display text-2xl font-semibold text-eden-950">
          El año en cuatro pasos
        </h2>
        <div className="mt-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {calendario.map((bloque) => (
            <article
              key={bloque.estacion}
              className="rounded-2xl bg-eden-50 p-6"
            >
              <h3 className="font-display text-lg font-semibold text-eden-900">
                {bloque.estacion}
              </h3>
              <ul className="mt-3 space-y-2 text-sm text-eden-700">
                {bloque.tareas.map((tarea) => (
                  <li key={tarea} className="flex gap-2">
                    <span aria-hidden="true" className="text-eden-400">
                      ·
                    </span>
                    <span>{tarea}</span>
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      </section>

      <section className="mt-16 rounded-3xl bg-eden-900 px-8 py-12 text-sand-50 md:px-12">
        <h2 className="font-display text-2xl font-semibold">
          ¿Tu caso no está aquí?
        </h2>
        <p className="mt-3 max-w-xl text-eden-200">
          Mándanos una foto de la planta y del sitio donde está. Te
          respondemos con un diagnóstico concreto, aunque no la hayas comprado
          con nosotros.
        </p>
        <Link
          href="/contacto"
          className="mt-6 inline-block rounded-full bg-sand-50 px-6 py-3 font-semibold text-eden-900 transition-colors hover:bg-white"
        >
          Consultar un problema
        </Link>
      </section>
    </div>
  );
}
