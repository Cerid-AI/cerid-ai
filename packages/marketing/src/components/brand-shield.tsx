/**
 * Cerid brand shield — inline SVG with teal glow "C" and optional gold trim.
 * Used in hero sections and as a visual anchor across pages.
 *
 * Variants:
 * - "core": teal outline, no gold
 * - "pro": silver/chrome outline
 * - "vault": gold trim with accent
 */
export function BrandShield({
  variant = "vault",
  size = 64,
  className = "",
  animate = false,
}: {
  variant?: "core" | "pro" | "vault"
  size?: number
  className?: string
  animate?: boolean
}) {
  const trim =
    variant === "vault"
      ? "#D4AF37"
      : variant === "pro"
        ? "#A8B5C8"
        : "#00C4B4"
  const trimWidth = variant === "vault" ? 1.5 : 1

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      fill="none"
      width={size}
      height={size}
      className={`${animate ? "float" : ""} ${className}`}
      aria-hidden="true"
    >
      {/* Glow filter */}
      <defs>
        <filter id="teal-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur in="SourceGraphic" stdDeviation="1.5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <linearGradient id="teal-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#00E5D8" />
          <stop offset="100%" stopColor="#7FF9E8" />
        </linearGradient>
      </defs>

      {/* Shield body */}
      <path
        d="M16 2L4 7.5v9.5c0 6.8 5.1 12.8 12 14 6.9-1.2 12-7.2 12-14V7.5L16 2z"
        fill="#0A1F3D"
      />
      {/* Shield border */}
      <path
        d="M16 2L4 7.5v9.5c0 6.8 5.1 12.8 12 14 6.9-1.2 12-7.2 12-14V7.5L16 2z"
        stroke={trim}
        strokeWidth={trimWidth}
        fill="none"
      />
      {/* Glowing C */}
      <text
        x="16"
        y="21.5"
        textAnchor="middle"
        fontFamily="system-ui,-apple-system,sans-serif"
        fontSize="14"
        fontWeight="bold"
        fill="url(#teal-grad)"
        filter="url(#teal-glow)"
      >
        C
      </text>
      {/* Vault accent dot */}
      {variant === "vault" && (
        <circle cx="16" cy="28" r="1" fill="#D4AF37" opacity="0.8" />
      )}
    </svg>
  )
}
