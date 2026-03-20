export const POINTS: Record<string, number> = {
  gfi: 1,
  bug: 2,
  hard: 4,
};

export const CATEGORY_LABELS: Record<string, string> = {
  gfi: "Good First Issue",
  bug: "Bug Fix",
  hard: "Hard Issue",
};

export interface Rank {
  threshold: number;
  name: string;
  badge: string;
}

export const RANKS: Rank[] = [
  { threshold: 500, name: "Mr. Robot", badge: "/badges/mrrobot.png" },
  { threshold: 100, name: "Bug Slayer", badge: "/badges/bugslayer.png" },
  {
    threshold: 0,
    name: "Hello World Engineer",
    badge: "/badges/hwengineer.png",
  },
];

export function getRank(points: number): Rank {
  return RANKS.find((r) => points >= r.threshold) ?? RANKS[RANKS.length - 1];
}
