export type Light = "Sombra" | "Luz indirecta" | "Sol directo";
export type Difficulty = "Fácil" | "Media" | "Exigente";

export type Plant = {
  slug: string;
  name: string;
  botanical: string;
  price: number;
  size: string;
  light: Light;
  /** Riegos por semana, en verano. */
  watering: string;
  difficulty: Difficulty;
  petFriendly: boolean;
  categories: string[];
  tagline: string;
  description: string;
  care: string[];
  /** Par de colores para la ilustración generada de la ficha. */
  palette: [string, string];
  stock: number;
};

export const plants: Plant[] = [
  {
    slug: "monstera-deliciosa",
    name: "Monstera Deliciosa",
    botanical: "Monstera deliciosa",
    price: 34,
    size: "Maceta Ø19 cm · 70-90 cm de alto",
    light: "Luz indirecta",
    watering: "1 riego por semana",
    difficulty: "Fácil",
    petFriendly: false,
    categories: ["Interior", "Grandes", "Purificadoras"],
    tagline: "La de las hojas rotas que sale en todas las fotos.",
    description:
      "Crece rápido y perdona los despistes. Sus hojas se abren en fenestras a medida que madura, así que cada hoja nueva es un pequeño evento. Ideal para llenar una esquina luminosa del salón sin complicarte la vida.",
    care: [
      "Luz brillante pero filtrada: nunca sol directo del mediodía.",
      "Riega cuando los 3 primeros centímetros de sustrato estén secos.",
      "Limpia las hojas con un paño húmedo una vez al mes.",
      "Ponle un tutor de musgo si quieres hojas más grandes.",
    ],
    palette: ["#2b4326", "#78a067"],
    stock: 12,
  },
  {
    slug: "pothos-dorado",
    name: "Potos Dorado",
    botanical: "Epipremnum aureum",
    price: 14,
    size: "Maceta Ø13 cm · guías de 40 cm",
    light: "Luz indirecta",
    watering: "1 riego cada 7-10 días",
    difficulty: "Fácil",
    petFriendly: false,
    categories: ["Interior", "Colgantes", "Purificadoras"],
    tagline: "Imposible de matar, incluso si lo intentas.",
    description:
      "La planta que recomendamos a quien dice que se le mueren todas. Aguanta poca luz, riegos irregulares y calefacción. Colgada de una estantería alta echa guías de varios metros en un par de temporadas.",
    care: [
      "Tolera desde sombra hasta luz muy brillante filtrada.",
      "Deja secar bien el sustrato entre riegos: odia el encharcamiento.",
      "Poda las guías largas y planta los esquejes en agua para multiplicarla.",
      "Si las hojas pierden el veteado amarillo, necesita más luz.",
    ],
    palette: ["#426837", "#c6d9be"],
    stock: 40,
  },
  {
    slug: "sansevieria-zeylanica",
    name: "Sansevieria",
    botanical: "Dracaena zeylanica",
    price: 19,
    size: "Maceta Ø15 cm · 55 cm de alto",
    light: "Sombra",
    watering: "1 riego cada 3 semanas",
    difficulty: "Fácil",
    petFriendly: false,
    categories: ["Interior", "Poca luz", "Purificadoras"],
    tagline: "Para pasillos oscuros y viajes largos.",
    description:
      "Almacena agua en sus hojas rígidas, así que puedes irte de vacaciones tres semanas sin pedirle favores a nadie. Es de las pocas plantas que siguen fotosintetizando de noche, perfecta para el dormitorio.",
    care: [
      "Sobrevive en sombra, pero crece más rápido con luz media.",
      "Menos agua siempre es mejor: en invierno, una vez al mes basta.",
      "Sustrato muy drenante, tipo mezcla para cactus.",
      "No mojes el centro de la roseta.",
    ],
    palette: ["#35532e", "#a0be93"],
    stock: 25,
  },
  {
    slug: "ficus-lyrata",
    name: "Ficus Lyrata",
    botanical: "Ficus lyrata",
    price: 59,
    size: "Maceta Ø24 cm · 120-140 cm de alto",
    light: "Luz indirecta",
    watering: "1 riego por semana",
    difficulty: "Exigente",
    petFriendly: false,
    categories: ["Interior", "Grandes", "Statement"],
    tagline: "El árbol de interior con hojas de violín.",
    description:
      "Impone en cualquier salón, pero tiene carácter: odia las corrientes de aire, los cambios de sitio y los riegos a destiempo. Si le encuentras el rincón bueno y no lo mueves, te lo devuelve con hojas enormes.",
    care: [
      "Mucha luz indirecta, cerca de una ventana orientada al este.",
      "No lo cambies de sitio: tira las hojas al mínimo estrés.",
      "Riego semanal en verano, cada 12-15 días en invierno.",
      "Gira la maceta un cuarto de vuelta cada mes para que crezca recto.",
    ],
    palette: ["#1f3220", "#598348"],
    stock: 6,
  },
  {
    slug: "calathea-orbifolia",
    name: "Calathea Orbifolia",
    botanical: "Goeppertia orbifolia",
    price: 29,
    size: "Maceta Ø17 cm · 45 cm de alto",
    light: "Luz indirecta",
    watering: "2 riegos por semana",
    difficulty: "Exigente",
    petFriendly: true,
    categories: ["Interior", "Pet friendly", "Statement"],
    tagline: "Hojas rayadas que se cierran al anochecer.",
    description:
      "Una planta que se mueve: pliega las hojas por la noche y las vuelve a abrir por la mañana. A cambio pide humedad alta y agua sin cal, así que va de lujo en el baño con ventana.",
    care: [
      "Agua filtrada o de lluvia: la cal le quema las puntas.",
      "Humedad ambiental por encima del 60%, agrupa plantas o usa humidificador.",
      "Sustrato siempre ligeramente húmedo, nunca encharcado.",
      "Nada de sol directo: le decolora el dibujo de las hojas.",
    ],
    palette: ["#2b4326", "#e3bfa0"],
    stock: 9,
  },
  {
    slug: "zamioculca",
    name: "Zamioculca",
    botanical: "Zamioculcas zamiifolia",
    price: 24,
    size: "Maceta Ø17 cm · 60 cm de alto",
    light: "Sombra",
    watering: "1 riego cada 2-3 semanas",
    difficulty: "Fácil",
    petFriendly: false,
    categories: ["Interior", "Poca luz", "Oficina"],
    tagline: "La favorita de las oficinas sin ventanas.",
    description:
      "Tiene rizomas bajo tierra que guardan agua, así que aguanta olvidos épicos. Sus hojas brillantes parecen de plástico de lo perfectas que son. Crece despacio, pero no se rinde nunca.",
    care: [
      "Aguanta luz baja; con luz media crece bastante más rápido.",
      "Riega solo cuando el sustrato esté seco del todo.",
      "En invierno, casi no la riegues.",
      "Si las hojas amarillean, es exceso de agua, no falta.",
    ],
    palette: ["#101c11", "#78a067"],
    stock: 18,
  },
  {
    slug: "olivo-bonsai",
    name: "Olivo de Interior",
    botanical: "Olea europaea",
    price: 45,
    size: "Maceta Ø20 cm · 80 cm de alto",
    light: "Sol directo",
    watering: "2 riegos por semana",
    difficulty: "Media",
    petFriendly: true,
    categories: ["Exterior", "Terraza", "Pet friendly"],
    tagline: "Mediterráneo en la terraza, todo el año.",
    description:
      "Un olivo joven formado en maceta, con tronco ya marcado y copa compacta. Necesita sol de verdad, así que es de balcón o ventana muy luminosa. Resiste el frío y la sequía como pocos.",
    care: [
      "Mínimo 6 horas de sol directo al día.",
      "Riego generoso en verano, muy espaciado en invierno.",
      "Poda ligera al final del invierno para mantener la copa.",
      "Abona en primavera con fertilizante para árboles mediterráneos.",
    ],
    palette: ["#598348", "#f4efe4"],
    stock: 7,
  },
  {
    slug: "helecho-nido-de-ave",
    name: "Helecho Nido de Ave",
    botanical: "Asplenium nidus",
    price: 22,
    size: "Maceta Ø15 cm · 40 cm de alto",
    light: "Sombra",
    watering: "2 riegos por semana",
    difficulty: "Media",
    petFriendly: true,
    categories: ["Interior", "Pet friendly", "Baño"],
    tagline: "Verde intenso para el baño con poca luz.",
    description:
      "Sus frondes brillantes salen del centro como un nido. Le encanta la humedad del baño y no le importa la luz escasa, siempre que el sustrato no se seque del todo.",
    care: [
      "Luz suave o sombra luminosa; el sol directo lo quema.",
      "Mantén el sustrato húmedo pero suelto.",
      "Pulveriza las hojas si el aire está muy seco.",
      "No riegues en el centro de la roseta para evitar podredumbre.",
    ],
    palette: ["#426837", "#c6d9be"],
    stock: 14,
  },
  {
    slug: "cactus-san-pedro",
    name: "Cactus Columnar",
    botanical: "Echinopsis pachanoi",
    price: 27,
    size: "Maceta Ø14 cm · 45 cm de alto",
    light: "Sol directo",
    watering: "1 riego cada 3-4 semanas",
    difficulty: "Fácil",
    petFriendly: true,
    categories: ["Interior", "Exterior", "Pet friendly"],
    tagline: "Arquitectura vegetal sin mantenimiento.",
    description:
      "Una columna verde-azulada que crece hacia arriba y estructura cualquier estantería. Sol, sustrato drenante y olvidarte de él: esa es toda la receta.",
    care: [
      "Cuanto más sol, mejor color y crecimiento.",
      "Riego profundo y espaciado; nada de agua en invierno.",
      "Sustrato mineral con arena gruesa o perlita.",
      "Trasplanta cada 2-3 años con guantes gruesos.",
    ],
    palette: ["#35532e", "#e8e0cf"],
    stock: 11,
  },
  {
    slug: "lavanda",
    name: "Lavanda",
    botanical: "Lavandula angustifolia",
    price: 12,
    size: "Maceta Ø14 cm · 30 cm de alto",
    light: "Sol directo",
    watering: "1 riego cada 5 días",
    difficulty: "Media",
    petFriendly: true,
    categories: ["Exterior", "Terraza", "Aromáticas", "Pet friendly"],
    tagline: "Huele a verano y espanta mosquitos.",
    description:
      "Perfecta para barandillas soleadas. Florece de mayo a agosto y, si la podas después de la floración, vuelve más compacta al año siguiente.",
    care: [
      "Pleno sol, cuanto más aireado el sitio mejor.",
      "Suelo pobre y muy drenante: se pudre con exceso de riego.",
      "Poda un tercio de la mata al terminar la floración.",
      "No la abones demasiado o dará hoja en vez de flor.",
    ],
    palette: ["#598348", "#e3bfa0"],
    stock: 30,
  },
  {
    slug: "aloe-vera",
    name: "Aloe Vera",
    botanical: "Aloe barbadensis",
    price: 16,
    size: "Maceta Ø15 cm · 35 cm de alto",
    light: "Sol directo",
    watering: "1 riego cada 3 semanas",
    difficulty: "Fácil",
    petFriendly: false,
    categories: ["Interior", "Suculentas", "Botiquín"],
    tagline: "Botiquín de quemaduras en maceta.",
    description:
      "Además de decorar, sirve: corta una hoja madura y usa el gel para quemaduras leves. Necesita mucha luz y muy poca agua para no ponerse blanda.",
    care: [
      "Sol directo o luz muy brillante todo el día.",
      "Riega solo cuando las hojas empiecen a arrugarse ligeramente.",
      "Sustrato de cactus con drenaje abundante.",
      "Separa los hijuelos en primavera para tener plantas nuevas.",
    ],
    palette: ["#78a067", "#f4efe4"],
    stock: 22,
  },
  {
    slug: "peperomia-sandia",
    name: "Peperomia Sandía",
    botanical: "Peperomia argyreia",
    price: 17,
    size: "Maceta Ø12 cm · 25 cm de alto",
    light: "Luz indirecta",
    watering: "1 riego cada 10 días",
    difficulty: "Fácil",
    petFriendly: true,
    categories: ["Interior", "Pequeñas", "Pet friendly", "Escritorio"],
    tagline: "Hojas con rayas de sandía, tamaño escritorio.",
    description:
      "Compacta, alegre y segura para gatos y perros. Cabe en cualquier mesa y se conforma con la luz de una ventana orientada al norte.",
    care: [
      "Luz indirecta media; evita el sol de la tarde.",
      "Deja secar la mitad superior del sustrato entre riegos.",
      "Le va bien una maceta pequeña: no le gusta el exceso de tierra.",
      "Multiplícala poniendo una hoja con su peciolo en agua.",
    ],
    palette: ["#2b4326", "#a0be93"],
    stock: 26,
  },
];

export const categories = Array.from(
  new Set(plants.flatMap((plant) => plant.categories)),
).sort((a, b) => a.localeCompare(b, "es"));

export function getPlant(slug: string): Plant | undefined {
  return plants.find((plant) => plant.slug === slug);
}

export function formatPrice(price: number): string {
  return new Intl.NumberFormat("es-ES", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 0,
  }).format(price);
}
