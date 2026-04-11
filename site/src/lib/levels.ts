import levelsConfig from "../../../config/arena_levels.json";

export interface CategoryCounts {
  gfi: number;
  bug: number;
  hard: number;
}

export interface ArenaLevel {
  level: number;
  threshold: number;
  bonus: CategoryCounts;
}

export interface LevelsConfig {
  version: number;
  baseline: CategoryCounts;
  levels: ArenaLevel[];
}

const CONFIG = levelsConfig as LevelsConfig;

export function getLevels(): ArenaLevel[] {
  return CONFIG.levels;
}

export function getBaseline(): CategoryCounts {
  return CONFIG.baseline;
}

export function getArenaLevel(arenaPoints: number): ArenaLevel {
  const reached = CONFIG.levels.filter((lv) => arenaPoints >= lv.threshold);
  return reached.length > 0 ? reached[reached.length - 1] : CONFIG.levels[0];
}

export function getNextLevel(arenaPoints: number): ArenaLevel | null {
  const current = getArenaLevel(arenaPoints);
  const idx = CONFIG.levels.findIndex((lv) => lv.level === current.level);
  return idx >= 0 && idx < CONFIG.levels.length - 1
    ? CONFIG.levels[idx + 1]
    : null;
}

export function effectiveLimits(level: number): CategoryCounts {
  const entry =
    CONFIG.levels.find((lv) => lv.level === level) ??
    CONFIG.levels[CONFIG.levels.length - 1];
  return {
    gfi: CONFIG.baseline.gfi + entry.bonus.gfi,
    bug: CONFIG.baseline.bug + entry.bonus.bug,
    hard: CONFIG.baseline.hard + entry.bonus.hard,
  };
}

export function totalIssuesAtLevel(level: number): number {
  const limits = effectiveLimits(level);
  return limits.gfi + limits.bug + limits.hard;
}

export interface ArenaLevelProgress {
  current: ArenaLevel;
  next: ArenaLevel | null;
  arenaPoints: number;
  progress: number;
  pointsToNext: number;
  totalIssuesAtCurrent: number;
  totalIssuesAtNext: number | null;
  isMaxLevel: boolean;
}

export function getArenaProgress(arenaPoints: number): ArenaLevelProgress {
  const current = getArenaLevel(arenaPoints);
  const next = getNextLevel(arenaPoints);
  const isMaxLevel = next === null;

  let progress = 1;
  let pointsToNext = 0;

  if (next) {
    const span = Math.max(next.threshold - current.threshold, 1);
    progress = Math.max(
      0,
      Math.min(1, (arenaPoints - current.threshold) / span),
    );
    pointsToNext = Math.max(next.threshold - arenaPoints, 0);
  }

  return {
    current,
    next,
    arenaPoints,
    progress,
    pointsToNext,
    totalIssuesAtCurrent: totalIssuesAtLevel(current.level),
    totalIssuesAtNext: next ? totalIssuesAtLevel(next.level) : null,
    isMaxLevel,
  };
}
