"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  OrgUser,
  Rotation,
  deleteRotation,
  saveRotation,
} from "@/features/org-setup/api";
import { FormError } from "@/features/auth/components/Field";

/** On-call rotations: the member list IS the on-call order (REQ-014). */
export function OnCallPanel({
  users,
  rotations,
  onChanged,
}: {
  users: OrgUser[];
  rotations: Rotation[];
  onChanged: () => void;
}) {
  const [name, setName] = useState("");
  const [order, setOrder] = useState<string[]>([]);
  const [hours, setHours] = useState(168);
  const [error, setError] = useState<string>();

  const byId = new Map(users.map((u) => [u.id, u]));
  const available = users.filter((u) => !order.includes(u.id));

  function move(index: number, delta: number) {
    setOrder((current) => {
      const next = [...current];
      const target = index + delta;
      if (target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  async function submit() {
    setError(undefined);
    try {
      await saveRotation({
        name,
        user_ids: order,
        app_id: null,
        handoff_interval_hours: hours,
      });
      setName("");
      setOrder([]);
      setHours(168);
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong");
    }
  }

  return (
    <section className="space-y-4">
      <form
        className="space-y-3 rounded-card border border-line bg-surface-raised p-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <h3 className="font-semibold">New rotation</h3>
        <div className="flex flex-wrap gap-3">
          <input
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Rotation name, e.g. primary"
            className="min-w-56 flex-1 rounded-card border border-line bg-surface px-3 py-2 text-sm"
          />
          <label className="flex items-center gap-2 text-sm text-ink-muted">
            Handoff every
            <input
              type="number"
              min={1}
              max={744}
              value={hours}
              onChange={(event) => setHours(Number(event.target.value))}
              className="w-20 rounded-card border border-line bg-surface px-2 py-2"
            />
            hours
          </label>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <p className="mb-1 text-xs text-ink-muted">On-call order (top pages first)</p>
            <ol className="space-y-1">
              {order.map((userId, index) => (
                <li
                  key={userId}
                  className="flex items-center gap-2 rounded-card border border-line px-2 py-1 text-sm"
                >
                  <span className="w-5 text-ink-muted">{index + 1}.</span>
                  <span className="flex-1">{byId.get(userId)?.display_name ?? userId}</span>
                  <button type="button" onClick={() => move(index, -1)} aria-label="Move up"
                    className="px-1 text-ink-muted hover:text-ink">↑</button>
                  <button type="button" onClick={() => move(index, 1)} aria-label="Move down"
                    className="px-1 text-ink-muted hover:text-ink">↓</button>
                  <button
                    type="button"
                    onClick={() => setOrder((c) => c.filter((id) => id !== userId))}
                    className="px-1 text-danger hover:underline"
                  >
                    remove
                  </button>
                </li>
              ))}
              {order.length === 0 && (
                <li className="text-sm text-ink-muted">Add members from the right.</li>
              )}
            </ol>
          </div>
          <div>
            <p className="mb-1 text-xs text-ink-muted">Members</p>
            <ul className="space-y-1">
              {available.map((user) => (
                <li key={user.id}>
                  <button
                    type="button"
                    onClick={() => setOrder((c) => [...c, user.id])}
                    className="w-full rounded-card border border-line px-2 py-1 text-left text-sm hover:border-accent"
                  >
                    + {user.display_name}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <FormError message={error} />
        <button
          type="submit"
          disabled={order.length === 0}
          className="rounded-card bg-accent px-3 py-2 text-sm font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
        >
          Save rotation
        </button>
      </form>

      <ul className="space-y-2">
        {rotations.map((rotation) => (
          <li
            key={rotation.id}
            className="flex items-center justify-between rounded-card border border-line bg-surface-raised px-4 py-3 text-sm"
          >
            <div>
              <p className="font-medium">{rotation.name}</p>
              <p className="text-ink-muted">
                {rotation.user_ids
                  .map((id) => byId.get(id)?.display_name ?? id)
                  .join(" → ")}{" "}
                · handoff every {rotation.handoff_interval_hours}h
              </p>
            </div>
            <button
              onClick={() => void deleteRotation(rotation.id).then(onChanged)}
              className="text-xs text-danger hover:underline"
            >
              Delete
            </button>
          </li>
        ))}
        {rotations.length === 0 && (
          <li className="text-sm text-ink-muted">No rotations yet.</li>
        )}
      </ul>
    </section>
  );
}
