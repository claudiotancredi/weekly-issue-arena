import { useState, useMemo } from "preact/hooks";

interface Issue {
  number: number;
  title: string;
  url: string;
  owner: string;
  repo: string;
  category: string;
  points: number;
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

export default function IssueFilter({ issues, base }: Props) {
  const [activeCategory, setActiveCategory] = useState("all");
  const [search, setSearch] = useState("");

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: issues.length };
    for (const issue of issues) {
      c[issue.category] = (c[issue.category] || 0) + 1;
    }
    return c;
  }, [issues]);

  const filtered = useMemo(() => {
    let result = issues;
    if (activeCategory !== "all") {
      result = result.filter((i) => i.category === activeCategory);
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
  }, [issues, activeCategory, search]);

  const categories = ["all", "gfi", "bug", "hard"];

  return (
    <div>
      {/* Filters */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", alignItems: "center", marginBottom: "1rem" }}>
        {categories.map((cat) => (
          <button
            key={cat}
            onClick={() => setActiveCategory(cat)}
            style={{
              padding: "0.375rem 0.75rem",
              borderRadius: "0.5rem",
              border: `1px solid ${activeCategory === cat ? "#6366f1" : "#2a2a3e"}`,
              background: activeCategory === cat ? "rgba(99, 102, 241, 0.15)" : "#12121a",
              color: activeCategory === cat ? "#6366f1" : "#a1a1aa",
              fontSize: "0.75rem",
              fontWeight: 600,
              fontFamily: "inherit",
              cursor: "pointer",
              transition: "all 0.15s",
            }}
          >
            {cat === "all" ? "All" : CATEGORY_LABELS[cat]} ({counts[cat] || 0})
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
              transition: "border-color 0.15s, background 0.15s",
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
            </div>
            <div style={{ fontSize: "0.875rem", fontWeight: 500, color: "#e4e4e7", lineHeight: 1.4 }}>
              {issue.title.length > 80 ? issue.title.slice(0, 77) + "..." : issue.title}
            </div>
            <div style={{ marginTop: "0.5rem", fontSize: "0.75rem", color: "#71717a" }}>
              {issue.owner}/{issue.repo}
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
