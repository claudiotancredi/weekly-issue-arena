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

export function getProgressToNextRank(points: number): {
  current: Rank;
  next: Rank | null;
  progress: number;
  pointsNeeded: number;
} {
  const current = getRank(points);
  const currentIndex = RANKS.indexOf(current);
  const next = currentIndex > 0 ? RANKS[currentIndex - 1] : null;

  if (!next) {
    return { current, next: null, progress: 1, pointsNeeded: 0 };
  }

  const rangeStart = current.threshold;
  const rangeEnd = next.threshold;
  const progress = (points - rangeStart) / (rangeEnd - rangeStart);
  const pointsNeeded = rangeEnd - points;

  return { current, next, progress: Math.min(progress, 1), pointsNeeded };
}
