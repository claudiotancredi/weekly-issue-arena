import { useMemo, useState } from "preact/hooks";

export interface MatchableIssueForIsland {
  number: number;
  title: string;
  url: string;
  owner: string;
  repo: string;
  category: string;
  points: number;
  whyThisOne: string;
}

interface Props {
  languages: { language: string; count: number }[];
  issuesByLanguage: Record<string, MatchableIssueForIsland[]>;
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

export default function MatchMeFlow({ languages, issuesByLanguage }: Props) {
  const [language, setLanguage] = useState<string | null>(null);
  const [index, setIndex] = useState(0);

  const issues = useMemo(
    () => (language ? issuesByLanguage[language] ?? [] : []),
    [language, issuesByLanguage],
  );
  const issue = issues[index] ?? null;

  function pickLanguage(lang: string) {
    setLanguage(lang);
    setIndex(0);
  }

  function nextIssue() {
    if (issues.length === 0) return;
    setIndex((i) => (i + 1) % issues.length);
  }

  function reset() {
    setLanguage(null);
    setIndex(0);
  }

  if (languages.length === 0) {
    return null;
  }

  return (
    <div
      style={{
        borderRadius: "0.75rem",
        border: "1px solid #2a2a3e",
        background: "#12121a",
        padding: "1.5rem",
      }}
    >
      <div style={{ marginBottom: "1rem" }}>
        <div style={{ fontSize: "0.75rem", color: "#71717a", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
          Step {language ? 2 : 1} of 2
        </div>
        <h3 style={{ marginTop: "0.25rem", fontSize: "1.125rem", fontWeight: 700, color: "#e4e4e7" }}>
          {language ? `An issue for you in ${language}` : "Pick your language"}
        </h3>
      </div>

      {!language && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
          {languages.map(({ language: lang, count }) => (
            <button
              key={lang}
              onClick={() => pickLanguage(lang)}
              style={{
                padding: "0.5rem 0.875rem",
                borderRadius: "0.5rem",
                border: "1px solid #2a2a3e",
                background: "#0d0d14",
                color: "#e4e4e7",
                fontSize: "0.8125rem",
                fontWeight: 600,
                fontFamily: "inherit",
                cursor: "pointer",
                transition: "all 0.15s",
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.borderColor = "#6366f1";
                (e.currentTarget as HTMLElement).style.background = "rgba(99, 102, 241, 0.1)";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.borderColor = "#2a2a3e";
                (e.currentTarget as HTMLElement).style.background = "#0d0d14";
              }}
            >
              {lang}{" "}
              <span style={{ color: "#71717a", fontWeight: 500 }}>· {count}</span>
            </button>
          ))}
        </div>
      )}

      {language && !issue && (
        <div style={{ color: "#a1a1aa", fontSize: "0.875rem" }}>
          Nothing open in {language} right now.{" "}
          <button
            onClick={reset}
            style={{
              background: "none",
              border: "none",
              color: "#6366f1",
              cursor: "pointer",
              padding: 0,
              font: "inherit",
              textDecoration: "underline",
            }}
          >
            Try another language
          </button>
          .
        </div>
      )}

      {language && issue && (
        <div>
          <div
            style={{
              padding: "1rem",
              borderRadius: "0.5rem",
              border: "1px solid #2a2a3e",
              background: "#0d0d14",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem", flexWrap: "wrap" }}>
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
                {CATEGORY_LABELS[issue.category] ?? issue.category}
              </span>
              <span style={{ fontSize: "0.625rem", color: "#71717a", fontWeight: 600 }}>
                +{issue.points} pt{issue.points !== 1 ? "s" : ""}
              </span>
              <span style={{ marginLeft: "auto", fontSize: "0.6875rem", color: "#71717a" }}>
                {issues.length > 1 ? `${index + 1} / ${issues.length}` : ""}
              </span>
            </div>
            <div style={{ fontSize: "0.9375rem", fontWeight: 600, color: "#e4e4e7", lineHeight: 1.4, marginBottom: "0.375rem" }}>
              {issue.title.length > 110 ? issue.title.slice(0, 107) + "..." : issue.title}
            </div>
            <div style={{ fontSize: "0.75rem", color: "#71717a", marginBottom: "0.5rem" }}>
              {issue.owner}/{issue.repo}
            </div>
            <div style={{ fontSize: "0.75rem", color: "#a1a1aa", fontStyle: "italic", marginBottom: "0.875rem" }}>
              Why this one: {issue.whyThisOne}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
              <a
                href={issue.url}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  padding: "0.5rem 0.875rem",
                  borderRadius: "0.5rem",
                  background: "#6366f1",
                  color: "white",
                  fontSize: "0.8125rem",
                  fontWeight: 600,
                  textDecoration: "none",
                }}
              >
                Open on GitHub →
              </a>
              {issues.length > 1 && (
                <button
                  onClick={nextIssue}
                  style={{
                    padding: "0.5rem 0.875rem",
                    borderRadius: "0.5rem",
                    border: "1px solid #2a2a3e",
                    background: "transparent",
                    color: "#a1a1aa",
                    fontSize: "0.8125rem",
                    fontWeight: 600,
                    fontFamily: "inherit",
                    cursor: "pointer",
                  }}
                >
                  Show me another
                </button>
              )}
              <button
                onClick={reset}
                style={{
                  padding: "0.5rem 0.875rem",
                  borderRadius: "0.5rem",
                  border: "1px solid transparent",
                  background: "transparent",
                  color: "#71717a",
                  fontSize: "0.8125rem",
                  fontWeight: 500,
                  fontFamily: "inherit",
                  cursor: "pointer",
                }}
              >
                Change language
              </button>
            </div>
          </div>

          <div style={{ marginTop: "1.25rem" }}>
            <div style={{ fontSize: "0.75rem", color: "#71717a", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.625rem" }}>
              From here, in 4 steps
            </div>
            <ol style={{ margin: 0, paddingLeft: "1.25rem", color: "#a1a1aa", fontSize: "0.8125rem", lineHeight: 1.6 }}>
              <li>Fork the repo and create a branch.</li>
              <li>Implement your fix locally.</li>
              <li>
                Open a PR with <code style={{ background: "#1a1a2e", padding: "0 0.25rem", borderRadius: "0.25rem", color: "#e4e4e7" }}>Closes #{issue.number}</code> in the description.
              </li>
              <li>When the PR merges, the arena credits you within ~1 hour and posts a welcome Discussion.</li>
            </ol>
          </div>
        </div>
      )}
    </div>
  );
}
