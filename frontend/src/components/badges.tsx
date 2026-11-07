import type { ArtReview, QACheck, QAStatus } from "../types/api";

const QA_TEXT: Record<string, string> = { passed: "QA ✓", flagged: "QA ⚠", failed: "QA ✕", not_run: "QA –" };

export function QABadge({ status, title }: { status: QAStatus | undefined; title?: string }) {
  const s = status ?? "not_run";
  return <span className={`qa qa-${s}`} title={title ?? `Technical QA: ${s.replace("_", " ")}`}>{QA_TEXT[s]}</span>;
}

export function ReviewBadge({ review }: { review: ArtReview | null | undefined }) {
  if (!review) return null;
  const t = { pending: "review pending", approved: "approved", rejected: "rejected" }[review];
  return <span className={`rv rv-${review}`}>{t}</span>;
}

export function CheckList({ checks }: { checks: QACheck[] }) {
  return (
    <ul className="checks">
      {checks.map((c) => (
        <li key={c.id} className={`ck-${c.status}`}>
          <span className="ic">{c.status === "pass" ? "✓" : c.status === "warn" ? "⚠" : "✕"}</span>
          <strong>{c.name}</strong>
          <span>{c.detail}</span>
        </li>
      ))}
    </ul>
  );
}
