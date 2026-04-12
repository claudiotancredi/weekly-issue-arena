import type { APIRoute, GetStaticPaths } from "astro";
import {
  getAllPlayerUsernames,
  getPlayer,
  getLeaderboard,
} from "../../lib/data";
import { getRank, getProgressToNextRank } from "../../lib/ranks";
import { renderCardSVG } from "../../lib/badge";

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
  const { next, progress } = getProgressToNextRank(player.total_points);
  const leaderboard = getLeaderboard();
  const position =
    leaderboard.find((e) => e.username === params.username)?.position ?? 0;

  const svg = renderCardSVG({
    username: params.username!,
    points: player.total_points,
    rankName: rank.name,
    contributions: player.contributions.length,
    position,
    progress,
    nextRankName: next?.name ?? null,
  });

  return new Response(svg, {
    headers: {
      "Content-Type": "image/svg+xml",
      "Cache-Control": "public, max-age=3600",
    },
  });
};
