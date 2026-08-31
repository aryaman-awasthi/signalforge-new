"use client";

export function Field({
  label,
  type = "text",
  value,
  onChange,
  placeholder,
  autoComplete,
}: {
  label: string;
  type?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  autoComplete?: string;
}) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block text-ink-muted">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        autoComplete={autoComplete}
        required
        className="w-full rounded-card border border-line bg-surface px-3 py-2 text-ink outline-none focus:border-accent"
      />
    </label>
  );
}

export function FormError({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
      {message}
    </p>
  );
}

export function SubmitButton({ label, busy }: { label: string; busy: boolean }) {
  return (
    <button
      type="submit"
      disabled={busy}
      className="w-full rounded-card bg-accent px-4 py-2 font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
    >
      {busy ? "Working..." : label}
    </button>
  );
}
