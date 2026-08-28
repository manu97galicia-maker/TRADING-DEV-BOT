type Props = {
  palette: [string, string];
  variant: "leafy" | "columnar" | "trailing" | "rosette";
  className?: string;
};

/**
 * Ilustración vectorial generada para cada planta.
 *
 * Evitamos fotografías externas: el SVG pesa poco, escala sin pixelarse y
 * mantiene la identidad de color de la ficha.
 */
export function PlantArt({ palette, variant, className }: Props) {
  const [dark, light] = palette;

  return (
    <svg
      viewBox="0 0 200 200"
      role="presentation"
      aria-hidden="true"
      className={className}
    >
      <defs>
        <linearGradient id={`bg-${variant}-${dark.slice(1)}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor={light} stopOpacity="0.35" />
          <stop offset="100%" stopColor={dark} stopOpacity="0.15" />
        </linearGradient>
      </defs>

      <rect
        width="200"
        height="200"
        fill={`url(#bg-${variant}-${dark.slice(1)})`}
      />

      {variant === "leafy" && (
        <g>
          {[-58, -30, 0, 30, 58].map((angle, i) => (
            <g key={angle} transform={`rotate(${angle} 100 152)`}>
              <line
                x1="100"
                y1="152"
                x2="100"
                y2="106"
                stroke={i % 2 ? light : dark}
                strokeWidth="3"
              />
              <path
                d="M100 108 C82 96 80 74 100 58 C120 74 118 96 100 108 Z"
                fill={i % 2 ? light : dark}
              />
              <path
                d="M100 96 L100 68"
                stroke={i % 2 ? dark : light}
                strokeWidth="1.5"
                opacity="0.55"
              />
            </g>
          ))}
        </g>
      )}

      {variant === "columnar" && (
        <g>
          <rect x="88" y="52" width="26" height="98" rx="13" fill={dark} />
          <rect x="62" y="86" width="20" height="64" rx="10" fill={dark} opacity="0.8" />
          <rect x="120" y="98" width="18" height="52" rx="9" fill={light} />
          <line x1="101" y1="60" x2="101" y2="142" stroke={light} strokeWidth="2" opacity="0.6" />
        </g>
      )}

      {variant === "trailing" && (
        <g>
          <path d="M100 92 C70 104 58 128 54 158" stroke={dark} strokeWidth="3" fill="none" />
          <path d="M100 92 C130 106 142 130 148 160" stroke={dark} strokeWidth="3" fill="none" />
          {[0, 1, 2, 3].map((i) => (
            <g key={i}>
              <ellipse
                cx={92 - i * 12}
                cy={104 + i * 16}
                rx="11"
                ry="8"
                fill={i % 2 ? light : dark}
                transform={`rotate(-30 ${92 - i * 12} ${104 + i * 16})`}
              />
              <ellipse
                cx={110 + i * 12}
                cy={106 + i * 16}
                rx="11"
                ry="8"
                fill={i % 2 ? dark : light}
                transform={`rotate(30 ${110 + i * 12} ${106 + i * 16})`}
              />
            </g>
          ))}
          <circle cx="100" cy="88" r="14" fill={dark} />
        </g>
      )}

      {variant === "rosette" && (
        <g>
          {[-60, -30, 0, 30, 60].map((angle, i) => (
            <ellipse
              key={angle}
              cx="100"
              cy="98"
              rx="11"
              ry="46"
              fill={i % 2 ? light : dark}
              transform={`rotate(${angle} 100 144)`}
            />
          ))}
        </g>
      )}

      <path
        d="M66 150 H134 L126 186 A6 6 0 0 1 120 190 H80 A6 6 0 0 1 74 186 Z"
        fill="#c2794a"
      />
      <rect x="62" y="144" width="76" height="12" rx="4" fill="#8c5230" />
    </svg>
  );
}

const VARIANTS: Array<PlantArtVariant> = ["leafy", "columnar", "trailing", "rosette"];
type PlantArtVariant = "leafy" | "columnar" | "trailing" | "rosette";

const EXPLICIT: Record<string, PlantArtVariant> = {
  "monstera-deliciosa": "leafy",
  "ficus-lyrata": "leafy",
  "calathea-orbifolia": "leafy",
  "olivo-bonsai": "leafy",
  "pothos-dorado": "trailing",
  "peperomia-sandia": "trailing",
  "sansevieria-zeylanica": "rosette",
  "aloe-vera": "rosette",
  "helecho-nido-de-ave": "rosette",
  "cactus-san-pedro": "columnar",
  "lavanda": "columnar",
  "zamioculca": "columnar",
};

/** Variante estable por planta: explícita si la conocemos, si no por hash del slug. */
export function variantFor(slug: string): PlantArtVariant {
  if (EXPLICIT[slug]) return EXPLICIT[slug];
  const hash = Array.from(slug).reduce((acc, char) => acc + char.charCodeAt(0), 0);
  return VARIANTS[hash % VARIANTS.length];
}
