import type { ReactNode } from "react";

interface JsonDisplayProps {
  value: unknown;
  title?: string;
}

export function JsonDisplay({ value, title }: JsonDisplayProps) {
  return (
    <section className="panel">
      {title ? <h2>{title}</h2> : null}
      <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>
    </section>
  );
}

interface StatusBannerProps {
  tone: "info" | "success" | "warning" | "error";
  children: ReactNode;
}

export function StatusBanner({ tone, children }: StatusBannerProps) {
  return <div className={`banner banner-${tone}`}>{children}</div>;
}

interface FieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

export function Field({ label, value, onChange, placeholder }: FieldProps) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

interface AsyncStateProps {
  loading: boolean;
  error: string | null;
}

export function AsyncState({ loading, error }: AsyncStateProps) {
  if (loading) {
    return <p className="muted">Loading…</p>;
  }
  if (error) {
    return <StatusBanner tone="error">{error}</StatusBanner>;
  }
  return null;
}
