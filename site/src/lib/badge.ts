export const RANK_COLORS: Record<string, string> = {
  "Mr. Robot": "#e91e63",
  "Bug Slayer": "#ff9800",
  "Hello World Engineer": "#6366f1",
};


function rankIcon(rankName: string, color: string): string {
  const g = (d: string) =>
    `<g transform="translate(30,36)" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</g>`;

  if (rankName === "Mr. Robot") {
    // Robot face
    return g(
      [
        `<rect x="2" y="6" width="24" height="18" rx="3"/>`,
        `<circle cx="10" cy="15" r="2.5" fill="${color}"/>`,
        `<circle cx="18" cy="15" r="2.5" fill="${color}"/>`,
        `<line x1="14" y1="0" x2="14" y2="6"/>`,
        `<circle cx="14" cy="0" r="2" fill="${color}"/>`,
        `<line x1="0" y1="14" x2="2" y2="14"/>`,
        `<line x1="26" y1="14" x2="28" y2="14"/>`,
      ].join(""),
    );
  }

  if (rankName === "Bug Slayer") {
    // Bug
    return g(
      [
        `<ellipse cx="14" cy="17" rx="7" ry="9"/>`,
        `<line x1="14" y1="8" x2="14" y2="26"/>`,
        `<path d="M3 12 L7 14"/>`,
        `<path d="M25 12 L21 14"/>`,
        `<path d="M3 22 L7 20"/>`,
        `<path d="M25 22 L21 20"/>`,
        `<path d="M10 8 Q10 3 7 1"/>`,
        `<path d="M18 8 Q18 3 21 1"/>`,
      ].join(""),
    );
  }

  // Hello World Engineer — terminal/console
  return g(
    [
      `<rect x="1" y="2" width="26" height="22" rx="3"/>`,
      `<polyline points="7,10 12,14 7,18"/>`,
      `<line x1="15" y1="18" x2="21" y2="18"/>`,
    ].join(""),
  );
}

export function renderCardSVG({
  username,
  points,
  rankName,
  contributions,
  position,
  progress,
  nextRankName,
}: {
  username: string;
  points: number;
  rankName: string;
  contributions: number;
  position: number;
  progress: number;
  nextRankName: string | null;
}): string {
  const W = 280;
  const H = 140;
  const accent = RANK_COLORS[rankName] ?? "#6366f1";

  const posLabel = position > 0 ? `#${position}` : "";
  const progressPct = Math.round(progress * 100);
  const barW = W - 48;
  const filledW = Math.round(barW * Math.min(progress, 1));

  const progressLabel = nextRankName
    ? `${progressPct}% to ${nextRankName}`
    : "Max rank reached";

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" fill="none">`,
    `<defs>`,
    `  <linearGradient id="bg" x1="0" y1="0" x2="${W}" y2="${H}" gradientUnits="userSpaceOnUse">`,
    `    <stop stop-color="#0d0d14"/>`,
    `    <stop offset="1" stop-color="#141420"/>`,
    `  </linearGradient>`,
    `  <linearGradient id="bar" x1="0" y1="0" x2="1" y2="0">`,
    `    <stop stop-color="${accent}"/>`,
    `    <stop offset="1" stop-color="${accent}cc"/>`,
    `  </linearGradient>`,
    `  <filter id="glow">`,
    `    <feGaussianBlur stdDeviation="8" result="blur"/>`,
    `    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>`,
    `  </filter>`,
    `</defs>`,
    // Card background
    `<rect width="${W}" height="${H}" rx="12" fill="url(#bg)"/>`,
    `<rect width="${W}" height="${H}" rx="12" stroke="${accent}33" stroke-width="1" fill="none"/>`,
    // Decorative accent circle
    `<circle cx="${W - 20}" cy="20" r="60" fill="${accent}" opacity="0.04"/>`,
    // Username
    `<text x="24" y="36" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="16" font-weight="700" fill="#e4e4e7">${escapeXml(username)}</text>`,
    // Position badge
    posLabel
      ? `<text x="${24 + username.length * 9.5 + 8}" y="36" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="11" font-weight="600" fill="${accent}">${posLabel}</text>`
      : "",
    // Rank name
    `<text x="24" y="53" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="11.5" fill="${accent}" font-weight="600">${escapeXml(rankName)}</text>`,
    // Stats row
    `<text x="24" y="72" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="11" fill="#a1a1aa">`,
    `  <tspan font-weight="700" fill="#e4e4e7">${points}</tspan>`,
    `  <tspan> pts</tspan>`,
    `  <tspan dx="12" font-weight="700" fill="#e4e4e7">${contributions}</tspan>`,
    `  <tspan> merged</tspan>`,
    `</text>`,
    // Progress bar background
    `<rect x="24" y="84" width="${barW}" height="6" rx="3" fill="#1e1e2e"/>`,
    // Progress bar fill
    filledW > 0
      ? `<rect x="24" y="84" width="${filledW}" height="6" rx="3" fill="url(#bar)"/>`
      : "",
    // Progress label
    `<text x="24" y="104" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="9" fill="#71717a">${progressLabel}</text>`,
    // Arena branding
    `<text x="${W - 16}" y="${H - 12}" text-anchor="end" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="9" fill="#ffffff">Weekly Issue Arena</text>`,
    `</svg>`,
  ].join("\n");
}

function escapeXml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

