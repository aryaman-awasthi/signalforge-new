"use client";

import type { ConfigSchema } from "@/features/integrations/api";

/**
 * Config inputs rendered from the adapter's own `config_schema`.
 *
 * Nothing about any specific integration is written here, which is the whole
 * point of the manifest: a new adapter dropped into the adapters tree gets its
 * fields on this screen with no cockpit change. The schema is also what the API
 * validates against at write time, so an operator sees the same rules the
 * platform enforces rather than a hand-kept copy of them.
 */
export function ConfigFields({
  schema,
  values,
  onChange,
}: {
  schema: ConfigSchema;
  values: Record<string, string>;
  onChange: (field: string, value: string) => void;
}) {
  const properties = schema.properties ?? {};
  const required = new Set(schema.required ?? []);
  const names = Object.keys(properties);

  if (names.length === 0) {
    return (
      <p className="text-xs text-ink-muted">
        This adapter takes no configuration.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {names.map((name) => {
        const property = properties[name];
        const isEnum = Array.isArray(property.enum) && property.enum.length > 0;
        return (
          <label key={name} className="block space-y-1">
            <span className="text-xs font-medium">
              {name}
              {required.has(name) && <span className="text-danger"> *</span>}
              {property.type && (
                <span className="ml-1 font-normal text-ink-muted">
                  ({property.type})
                </span>
              )}
            </span>
            {isEnum ? (
              <select
                value={values[name] ?? ""}
                required={required.has(name)}
                onChange={(event) => onChange(name, event.target.value)}
                className="w-full rounded-card border border-line bg-surface px-2 py-1.5 text-sm"
              >
                <option value="">-</option>
                {property.enum!.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={values[name] ?? ""}
                required={required.has(name)}
                type={property.type === "integer" || property.type === "number"
                  ? "number"
                  : "text"}
                onChange={(event) => onChange(name, event.target.value)}
                className="w-full rounded-card border border-line bg-surface px-2 py-1.5 text-sm"
              />
            )}
            {property.description && (
              <span className="block text-[11px] text-ink-muted">
                {property.description}
              </span>
            )}
          </label>
        );
      })}
    </div>
  );
}

/**
 * The typed value a schema field expects. Every input is a string in the DOM,
 * and posting "60" where the schema says integer is a 422 the operator did not
 * cause, so the coercion happens here rather than being pushed onto them.
 */
export function coerceConfig(
  schema: ConfigSchema,
  values: Record<string, string>,
): Record<string, unknown> {
  const properties = schema.properties ?? {};
  const out: Record<string, unknown> = {};
  for (const [name, raw] of Object.entries(values)) {
    if (raw === "") continue;
    const type = properties[name]?.type;
    if (type === "integer") out[name] = Number.parseInt(raw, 10);
    else if (type === "number") out[name] = Number.parseFloat(raw);
    else if (type === "boolean") out[name] = raw === "true";
    else out[name] = raw;
  }
  return out;
}
