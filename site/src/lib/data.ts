import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { POINTS, getRank } from "./ranks";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../../..");
const ISSUES_PATH = path.join(ROOT, ".arena_state/issues.json");
const SCORES_PATH = path.join(ROOT, ".arena_state/scores.json");
const REPO_POOL_PATH = path.join(ROOT, ".arena_state/repo_pool.json");
const ANCHOR_REPOS_PATH = path.join(ROOT, "config/anchor_repos.yml");
const MILESTONES_PATH = path.join(ROOT, ".arena_state/milestones.json");

// --- Types ---

export interface Issue {
  number: number;
  title: string;
  url: string;
  owner: string;
  repo: string;
  repo_url: string;
  created_at: string;
  updated_at: string;
  author: string;
  listed_at: string;
  closed?: boolean;
  has_pr?: boolean;
}

export interface CategorizedIssues {
  gfi: Issue[];
  bug: Issue[];
  hard: Issue[];
}

interface WeekData {
  fetched_at: string;
  issues: CategorizedIssues;
}

export interface Contribution {
  issue: string;
  points: number;
  pr_url: string;
  week: string;
  credited_at: string;
}

export interface PlayerData {
  total_points: number;
  avatar_url: string;
  contributions: Contribution[];
  discussion_node_id?: string;
}

interface ScoresData {
  players: Record<string, PlayerData>;
  credited_issues: string[];
  weekly: Record<string, string[]>;
}

export interface LeaderboardEntry {
  username: string;
  total_points: number;
  avatar_url: string;
  contributions: Contribution[];
  rank: ReturnType<typeof getRank>;
  position: number;
}

export interface IssueWithCategory extends Issue {
  category: string;
  points: number;
  closed: boolean;
  has_pr: boolean;
}

// --- Loaders ---

function readJSON<T>(filepath: string): T | null {
  try {
    const raw = fs.readFileSync(filepath, "utf-8");
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function getAllWeeks(): Record<string, WeekData> {
  return readJSON<Record<string, WeekData>>(ISSUES_PATH) ?? {};
}

function getScores(): ScoresData {
  return (
    readJSON<ScoresData>(SCORES_PATH) ?? {
      players: {},
      credited_issues: [],
      weekly: {},
    }
  );
}

export function getLatestWeekId(): string | null {
  const weeks = Object.keys(getAllWeeks()).sort();
  return weeks.length > 0 ? weeks[weeks.length - 1] : null;
}

export function getCurrentWeekIssues(): CategorizedIssues {
  const weeks = getAllWeeks();
  const weekIds = Object.keys(weeks).sort();
  if (weekIds.length === 0) return { gfi: [], bug: [], hard: [] };
  return weeks[weekIds[weekIds.length - 1]].issues;
}

export function getAllCurrentIssues(): IssueWithCategory[] {
  const issues = getCurrentWeekIssues();
  const scores = getScores();
  const creditedSet = new Set(scores.credited_issues);
  const result: IssueWithCategory[] = [];

  for (const [category, items] of Object.entries(issues)) {
    const points = POINTS[category] ?? 1;
    for (const issue of items) {
      const key = `${issue.owner}/${issue.repo}#${issue.number}`;
      result.push({
        ...issue,
        category,
        points,
        closed: creditedSet.has(key) || issue.closed === true,
        has_pr: issue.has_pr === true,
      });
    }
  }

  const statusOrder = (i: IssueWithCategory) =>
    i.closed ? 2 : i.has_pr ? 1 : 0;
  result.sort((a, b) => statusOrder(a) - statusOrder(b));
  return result;
}

export function getLeaderboard(): LeaderboardEntry[] {
  const scores = getScores();
  const entries = Object.entries(scores.players)
    .map(([username, data]) => ({
      username,
      total_points: data.total_points,
      avatar_url: data.avatar_url,
      contributions: data.contributions,
      rank: getRank(data.total_points),
      position: 0,
    }))
    .sort((a, b) => {
      if (b.total_points !== a.total_points)
        return b.total_points - a.total_points;
      return a.username.localeCompare(b.username);
    });

  entries.forEach((e, i) => (e.position = i + 1));
  return entries;
}

export function getPlayer(username: string): PlayerData | null {
  const scores = getScores();
  return scores.players[username] ?? null;
}

export function getAllPlayerUsernames(): string[] {
  const scores = getScores();
  return Object.keys(scores.players);
}

export function getWeeklyContributors(): string[] {
  const scores = getScores();
  const weekId = getLatestWeekId();
  if (!weekId) return [];
  return scores.weekly[weekId] ?? [];
}

export function getRepoCount(): number {
  // Prefer the dynamic weekly pool (always 250 when healthy).
  try {
    const raw = fs.readFileSync(REPO_POOL_PATH, "utf-8");
    const pool = JSON.parse(raw);
    return pool.total_count ?? pool.repos?.length ?? 0;
  } catch {
    /* fall through to anchor list */
  }

  // Fallback: count anchor repos from the curated YAML.
  try {
    const raw = fs.readFileSync(ANCHOR_REPOS_PATH, "utf-8");
    return (raw.match(/- owner:/g) || []).length;
  } catch {
    return 0;
  }
}

export function getTotalContributors(): number {
  const scores = getScores();
  return Object.keys(scores.players).length;
}

export function getPlayerWeeklyActivity(
  contributions: Contribution[],
): { week: string; count: number; points: number }[] {
  const weekMap = new Map<string, { count: number; points: number }>();

  for (const c of contributions) {
    const existing = weekMap.get(c.week) ?? { count: 0, points: 0 };
    existing.count++;
    existing.points += c.points;
    weekMap.set(c.week, existing);
  }

  return Array.from(weekMap.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([week, data]) => ({ week, ...data }));
}

export function getTotalIssuesThisWeek(): number {
  const issues = getCurrentWeekIssues();
  return issues.gfi.length + issues.bug.length + issues.hard.length;
}

export function getTotalPointsAwarded(): number {
  const scores = getScores();
  return Object.values(scores.players).reduce(
    (total, player) => total + player.total_points,
    0,
  );
}

export function getTotalIssuesCredited(): number {
  const scores = getScores();
  return scores.credited_issues.length;
}

interface ArenaMilestonesState {
  current_level: number;
  current_arena_points: number;
  discussion_node_id?: string | null;
  history?: Array<{
    level: number;
    reached_at: string;
    arena_points_at_reach: number;
    threshold?: number;
    announced?: boolean;
  }>;
}

export interface ArenaLevelState {
  level: number;
  arenaPoints: number;
}

export function getArenaLevelState(): ArenaLevelState {
  const liveTotal = getTotalPointsAwarded();
  const state = readJSON<ArenaMilestonesState>(MILESTONES_PATH);
  if (!state) {
    return { level: 0, arenaPoints: liveTotal };
  }
  return {
    level: state.current_level ?? 0,
    // Prefer the live sum from scores.json so the bar reflects the
    // freshest leaderboard tick even if milestones.json is one cron behind.
    arenaPoints: Math.max(state.current_arena_points ?? 0, liveTotal),
  };
}
