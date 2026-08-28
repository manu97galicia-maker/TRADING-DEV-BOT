"use client";

import { useState } from "react";

import { plants } from "@/data/plants";

type Estado = "editando" | "enviado";

export function ContactForm({ plantaInicial }: { plantaInicial?: string }) {
  const [estado, setEstado] = useState<Estado>("editando");
  const [nombre, setNombre] = useState("");

  if (estado === "enviado") {
    return (
      <div
        role="status"
        className="rounded-2xl border border-eden-200 bg-eden-50 p-8"
      >
        <h2 className="font-display text-2xl font-semibold text-eden-900">
          Recibido{nombre ? `, ${nombre}` : ""} 🌿
        </h2>
        <p className="mt-3 text-eden-700">
          Te contestamos en menos de 24 h laborables desde
          hola@myedenplants.com. Si es urgente, llámanos al 986 00 00 00.
        </p>
        <button
          type="button"
          onClick={() => setEstado("editando")}
          className="mt-6 rounded-full border border-eden-300 px-5 py-2.5 text-sm font-semibold text-eden-800 hover:bg-white"
        >
          Enviar otra consulta
        </button>
      </div>
    );
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        // Demo sin backend: aquí iría el POST al servicio de correo.
        setEstado("enviado");
      }}
      className="space-y-5 rounded-2xl border border-eden-100 bg-white p-6 md:p-8"
    >
      <div className="grid gap-5 sm:grid-cols-2">
        <label className="block">
          <span className="text-sm font-medium text-eden-800">Nombre</span>
          <input
            required
            name="nombre"
            value={nombre}
            onChange={(event) => setNombre(event.target.value)}
            className="mt-1.5 w-full rounded-lg border border-eden-200 px-3 py-2.5 text-eden-900 outline-none focus:border-eden-500 focus:ring-2 focus:ring-eden-200"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-eden-800">Email</span>
          <input
            required
            type="email"
            name="email"
            className="mt-1.5 w-full rounded-lg border border-eden-200 px-3 py-2.5 text-eden-900 outline-none focus:border-eden-500 focus:ring-2 focus:ring-eden-200"
          />
        </label>
      </div>

      <label className="block">
        <span className="text-sm font-medium text-eden-800">Motivo</span>
        <select
          name="motivo"
          defaultValue={plantaInicial ? "reserva" : "consejo"}
          className="mt-1.5 w-full rounded-lg border border-eden-200 bg-white px-3 py-2.5 text-eden-900 outline-none focus:border-eden-500 focus:ring-2 focus:ring-eden-200"
        >
          <option value="consejo">Quiero consejo para elegir planta</option>
          <option value="reserva">Reservar una planta del catálogo</option>
          <option value="problema">Mi planta tiene un problema</option>
          <option value="pedido">Duda sobre un pedido</option>
        </select>
      </label>

      <label className="block">
        <span className="text-sm font-medium text-eden-800">
          Planta (opcional)
        </span>
        <select
          name="planta"
          defaultValue={plantaInicial ?? ""}
          className="mt-1.5 w-full rounded-lg border border-eden-200 bg-white px-3 py-2.5 text-eden-900 outline-none focus:border-eden-500 focus:ring-2 focus:ring-eden-200"
        >
          <option value="">Ninguna en concreto</option>
          {plants.map((plant) => (
            <option key={plant.slug} value={plant.slug}>
              {plant.name}
            </option>
          ))}
        </select>
      </label>

      <label className="block">
        <span className="text-sm font-medium text-eden-800">Cuéntanos</span>
        <textarea
          required
          name="mensaje"
          rows={5}
          placeholder="Salón orientado al norte, una ventana grande, dos gatos y me olvido de regar cada dos semanas…"
          className="mt-1.5 w-full rounded-lg border border-eden-200 px-3 py-2.5 text-eden-900 outline-none focus:border-eden-500 focus:ring-2 focus:ring-eden-200"
        />
      </label>

      <label className="flex items-start gap-3 text-sm text-eden-700">
        <input
          required
          type="checkbox"
          name="privacidad"
          className="mt-1 size-4 rounded border-eden-300"
        />
        <span>
          Acepto que guardéis mis datos para responder a esta consulta.
        </span>
      </label>

      <button
        type="submit"
        className="w-full rounded-full bg-eden-700 px-6 py-3 font-semibold text-sand-50 transition-colors hover:bg-eden-800 sm:w-auto"
      >
        Enviar consulta
      </button>
    </form>
  );
}
