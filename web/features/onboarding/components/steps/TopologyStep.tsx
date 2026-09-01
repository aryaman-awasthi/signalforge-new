"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  Profile,
  TopologyUpload,
  TopologyVersion,
  getTopology,
  putTopology,
} from "@/features/apps/api";
import { relativeTime } from "@/lib/format";

/**
 * Step 4 - Topology.
 *
 * Upload the dependency map. Two behaviours the screen has to make visible
 * because they are surprising if it does not:
 *
 * - the same content uploaded twice is **one** version. Versioning is by
 *   content, so a re-upload with a fresh capture time and reordered nodes
 *   answers `unchanged` rather than making a second version nobody asked for.
 *   Without saying so, an operator retries and thinks nothing happened.
 * - a dangling edge rejects the **whole** document, naming the offending edges,
 *   and the previous map stays intact. A half-imported topology would give
 *   blast radius a map with holes in it.
 */
export function TopologyStep({
  appId,
  profile,
  onChanged,
}: {
  appId: string;
  profile: Profile;
  onChanged: () => void;
}) {
  const [document, setDocument] = useState("");
  const [versions, setVersions] = useState<TopologyVersion[]>([]);
  const [result, setResult] = useState<TopologyUpload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getTopology(appId)
      .then((view) => setVersions(view.versions))
      .catch(() => setVersions([]));
  }, [appId, result]);

  async function upload() {
    setBusy(true);
    setError(null);
    setResult(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(document);
    } catch {
      setError("That is not valid JSON.");
      setBusy(false);
      return;
    }
    try {
      setResult(await putTopology(appId, parsed));
      onChanged();
    } catch (failure) {
      setError(
        failure instanceof ApiError ? failure.message : "Could not import that map",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {profile.topology ? (
        <div className="rounded-card border border-line bg-surface-raised p-3 text-sm">
          <p className="font-medium">Current map</p>
          <p className="text-xs text-ink-muted">
            <span className="font-mono">{profile.topology.version}</span> ·{" "}
            {profile.topology.node_count} nodes · {profile.topology.edge_count} edges ·
            normalised by {profile.topology.normalized_by} · imported{" "}
            {relativeTime(profile.topology.imported_at)}
          </p>
        </div>
      ) : (
        <p className="rounded-card border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
          No topology yet. Blast radius and impact prediction have nothing to walk
          until there is one.
        </p>
      )}

      <label className="block space-y-1">
        <span className="text-xs font-medium">Topology document (JSON)</span>
        <textarea
          value={document}
          rows={12}
          spellCheck={false}
          onChange={(event) => setDocument(event.target.value)}
          placeholder={
            '{"captured_at": "...", "nodes": [{"node_id": "web", "name": "Web", ' +
            '"node_type": "service"}], "edges": [{"source": "web", "target": "db", ' +
            '"edge_type": "depends_on"}]}'
          }
          className="w-full rounded-card border border-line bg-surface px-3 py-2 font-mono text-xs"
        />
        <span className="block text-[11px] text-ink-muted">
          Declare a `version` and it is used as-is; leave it out and the version is
          a content fingerprint that ignores capture time and node order.
        </span>
      </label>

      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {result && (
        <p
          className={`rounded-card border px-3 py-2 text-sm ${
            result.unchanged
              ? "border-warn/40 bg-warn/10 text-warn"
              : "border-ok/40 bg-ok/10 text-ok"
          }`}
        >
          {result.unchanged
            ? `Identical to the map already held (${result.version}). One version, no new event - nothing changed.`
            : `Imported ${result.version}: ${result.node_count} nodes, ${result.edge_count} edges.`}
        </p>
      )}

      <button
        onClick={() => void upload()}
        disabled={busy || document.trim() === ""}
        className="rounded-card bg-accent px-3 py-2 text-sm font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
      >
        {busy ? "Importing…" : "Upload topology"}
      </button>

      {versions.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-sm font-medium">Versions</h3>
          <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised text-xs">
            {versions.map((version) => (
              <li key={version.version} className="flex flex-wrap gap-3 px-3 py-2">
                <span className="font-mono">{version.version}</span>
                <span className="text-ink-muted">
                  {version.node_count} nodes · {version.edge_count} edges ·{" "}
                  {version.source} · {relativeTime(version.imported_at)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
