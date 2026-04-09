import type { APIRoute, GetStaticPaths } from "astro";
import { getAllPlayerUsernames, getPlayer } from "../../lib/data";
import { getRank } from "../../lib/ranks";
import { renderBadgeSVG } from "../../lib/badge";

export const getStaticPaths: GetStaticPaths = () => {
  const usernames = getAllPlayerUsernames();
  return usernames.map((username) => ({ params: { username } }));
};

export const GET: APIRoute = ({ params }) => {
  const player = getPlayer(params.username!);
  if (!player) {
    return new Response("Not found", { status: 404 });
  }

  const rank = getRank(player.total_points);
  const svg = renderBadgeSVG({
    points: player.total_points,
    rankName: rank.name,
  });

  return new Response(svg, {
    headers: {
      "Content-Type": "image/svg+xml",
      "Cache-Control": "public, max-age=3600",
    },
  });
};
