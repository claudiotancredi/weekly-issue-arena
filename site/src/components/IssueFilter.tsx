import { useState, useMemo } from "preact/hooks";

interface Issue {
  number: number;
  title: string;
  url: string;
  owner: string;
  repo: string;
  category: string;
  points: number;
  closed: boolean;
  has_pr: boolean;
  language: string | null;
}

interface Props {
  issues: Issue[];
  base: string;
}

const CATEGORY_LABELS: Record<string, string> = {
  gfi: "Good First Issue",
  bug: "Bug Fix",
  hard: "Hard Issue",
};

const CATEGORY_COLORS: Record<string, string> = {
  gfi: "#22c55e",
  bug: "#f59e0b",
  hard: "#ef4444",
};

const STATUS_LABELS: Record<string, string> = {
  all: "All",
  open: "Open",
  pr_proposed: "PR Proposed",
  closed: "Closed",
};

export default function IssueFilter({ issues, base }: Props) {
  const [activeCategory, setActiveCategory] = useState("all");
  const [activeStatus, setActiveStatus] = useState("all");
  const [search, setSearch] = useState("");

  const counts = useMemo(() => {
    const pool = activeStatus === "open" ? issues.filter((i) => !i.closed && !i.has_pr)
      : activeStatus === "pr_proposed" ? issues.filter((i) => !i.closed && i.has_pr)
      : activeStatus === "closed" ? issues.filter((i) => i.closed)
      : issues;
    const c: Record<string, number> = { all: pool.length };
    for (const issue of pool) {
      c[issue.category] = (c[issue.category] || 0) + 1;
    }
    return c;
  }, [issues, activeStatus]);

  const statusCounts = useMemo(() => {
    const pool = activeCategory !== "all"
      ? issues.filter((i) => i.category === activeCategory)
      : issues;
    const open = pool.filter((i) => !i.closed && !i.has_pr).length;
    const pr_proposed = pool.filter((i) => !i.closed && i.has_pr).length;
    const closed = pool.filter((i) => i.closed).length;
    return { all: pool.length, open, pr_proposed, closed };
  }, [issues, activeCategory]);

  const filtered = useMemo(() => {
    let result = issues;
    if (activeCategory !== "all") {
      result = result.filter((i) => i.category === activeCategory);
    }
    if (activeStatus === "open") {
      result = result.filter((i) => !i.closed && !i.has_pr);
    } else if (activeStatus === "pr_proposed") {
      result = result.filter((i) => !i.closed && i.has_pr);
    } else if (activeStatus === "closed") {
      result = result.filter((i) => i.closed);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (i) =>
          i.title.toLowerCase().includes(q) ||
          `${i.owner}/${i.repo}`.toLowerCase().includes(q),
      );
    }
    return result;
  }, [issues, activeCategory, activeStatus, search]);

  const categories = ["all", "gfi", "bug", "hard"];
  const statuses = ["all", "open", "pr_proposed", "closed"];

  const tabStyle = (active: boolean) => ({
    padding: "0.375rem 0.75rem",
    borderRadius: "0.5rem",
    border: `1px solid ${active ? "#6366f1" : "#2a2a3e"}`,
    background: active ? "rgba(99, 102, 241, 0.15)" : "#12121a",
    color: active ? "#6366f1" : "#a1a1aa",
    fontSize: "0.75rem",
    fontWeight: 600,
    fontFamily: "inherit",
    cursor: "pointer",
    transition: "all 0.15s",
  });

  return (
    <div>
      {/* Filters */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", alignItems: "center", marginBottom: "1rem" }}>
        {categories.map((cat) => (
          <button
            key={cat}
            onClick={() => setActiveCategory(cat)}
            style={tabStyle(activeCategory === cat)}
          >
            {cat === "all" ? "All" : CATEGORY_LABELS[cat]} ({counts[cat] || 0})
          </button>
        ))}

        <span style={{ width: "1px", height: "1.25rem", background: "#2a2a3e" }} />

        {statuses.map((status) => (
          <button
            key={status}
            onClick={() => setActiveStatus(status)}
            style={tabStyle(activeStatus === status)}
          >
            {STATUS_LABELS[status]} ({statusCounts[status as keyof typeof statusCounts]})
          </button>
        ))}

        <input
          type="text"
          placeholder="Search issues..."
          value={search}
          onInput={(e) => setSearch((e.target as HTMLInputElement).value)}
          style={{
            marginLeft: "auto",
            padding: "0.375rem 0.75rem",
            borderRadius: "0.5rem",
            border: "1px solid #2a2a3e",
            background: "#12121a",
            color: "#e4e4e7",
            fontSize: "0.75rem",
            fontFamily: "inherit",
            outline: "none",
            width: "100%",
            maxWidth: "16rem",
          }}
        />
      </div>

      {/* Results */}
      <div style={{ display: "grid", gap: "0.75rem", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}>
        {filtered.map((issue) => (
          <a
            key={`${issue.owner}/${issue.repo}#${issue.number}`}
            href={issue.url}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: "block",
              padding: "1rem",
              borderRadius: "0.5rem",
              border: "1px solid #2a2a3e",
              background: "#12121a",
              textDecoration: "none",
              transition: "border-color 0.15s, background 0.15s, opacity 0.15s",
              opacity: issue.closed ? 0.6 : 1,
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLElement).style.borderColor = "rgba(99, 102, 241, 0.5)";
              (e.currentTarget as HTMLElement).style.background = "#1a1a2e";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.borderColor = "#2a2a3e";
              (e.currentTarget as HTMLElement).style.background = "#12121a";
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem" }}>
              <span
                style={{
                  display: "inline-block",
                  padding: "0.125rem 0.5rem",
                  borderRadius: "9999px",
                  border: `1px solid ${CATEGORY_COLORS[issue.category]}30`,
                  background: `${CATEGORY_COLORS[issue.category]}15`,
                  color: CATEGORY_COLORS[issue.category],
                  fontSize: "0.625rem",
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                {CATEGORY_LABELS[issue.category]}
              </span>
              <span style={{ fontSize: "0.625rem", color: "#71717a", fontWeight: 600 }}>
                +{issue.points} pt{issue.points !== 1 ? "s" : ""}
              </span>
              <span style={{ marginLeft: "auto", fontSize: "0.625rem", fontWeight: 600, color: issue.closed ? "#f87171" : issue.has_pr ? "#facc15" : "#4ade80" }}>
                {issue.closed ? "\uD83D\uDD34 Closed" : issue.has_pr ? "\uD83D\uDFE1 PR Proposed" : "\uD83D\uDFE2 Open"}
              </span>
            </div>
            <div style={{ fontSize: "0.875rem", fontWeight: 500, color: "#e4e4e7", lineHeight: 1.4 }}>
              {issue.title.length > 80 ? issue.title.slice(0, 77) + "..." : issue.title}
            </div>
            <div style={{ marginTop: "0.5rem", fontSize: "0.75rem", color: "#71717a", display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <span>{issue.owner}/{issue.repo}</span>
              {issue.language && (
                <span style={{ padding: "0.1rem 0.375rem", borderRadius: "0.25rem", background: "#1a1a2e", fontSize: "0.625rem", color: "#a1a1aa" }}>
                  {issue.language}
                </span>
              )}
            </div>
          </a>
        ))}
      </div>

      {filtered.length === 0 && (
        <div style={{ textAlign: "center", padding: "3rem 0", color: "#71717a", fontSize: "0.875rem" }}>
          No issues match your filters.
        </div>
      )}
    </div>
  );
}
