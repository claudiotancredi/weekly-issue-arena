const RANK_COLORS: Record<string, string> = {
  "Mr. Robot": "#e91e63",
  "Bug Slayer": "#ff9800",
  "Hello World Engineer": "#6366f1",
};

const FONT = 'font-family="Verdana,Geneva,DejaVu Sans,sans-serif"';
const FONT_SIZE = 11;
const CHAR_WIDTH = 6.8;
const HEIGHT = 20;
const PADDING = 8;
const RADIUS = 3;

function textWidth(text: string): number {
  return Math.ceil(text.length * CHAR_WIDTH) + PADDING * 2;
}

export function renderBadgeSVG({
  points,
  rankName,
}: {
  points: number;
  rankName: string;
}): string {
  const leftText = `Weekly Issue Arena: ${rankName}`;
  const rightText = `${points} pts`;

  const leftWidth = textWidth(leftText);
  const rightWidth = textWidth(rightText);
  const totalWidth = leftWidth + rightWidth;

  const leftColor = RANK_COLORS[rankName] ?? "#6366f1";
  const rightColor = "#333";

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${totalWidth}" height="${HEIGHT}" role="img" aria-label="${rankName}: ${points} pts">
  <title>${rankName}: ${points} pts</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="${totalWidth}" height="${HEIGHT}" rx="${RADIUS}" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="${leftWidth}" height="${HEIGHT}" fill="${leftColor}"/>
    <rect x="${leftWidth}" width="${rightWidth}" height="${HEIGHT}" fill="${rightColor}"/>
    <rect width="${totalWidth}" height="${HEIGHT}" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" ${FONT} text-rendering="geometricPrecision" font-size="${FONT_SIZE}">
    <text x="${leftWidth / 2}" y="14">${leftText}</text>
    <text x="${leftWidth + rightWidth / 2}" y="14">${rightText}</text>
  </g>
</svg>`;
}
