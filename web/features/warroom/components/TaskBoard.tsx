"use client";

import { FormEvent, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { Task, completeTask, createTask } from "@/features/warroom/api";

/**
 * The incident task board: commitments with an owner and a due time.
 *
 * The unassigned rows are the interesting ones. A commitment harvested from the
 * bridge that named somebody the platform could not resolve to exactly one user
 * is shown unassigned, carrying the name as it was said - and the row says so.
 * That is the whole design: an unowned task on the board is fixable in one
 * click, and a task owned by the wrong Sam is how the wrong person gets paged.
 *
 * Anybody in the tenant can complete an unassigned task, and only its owner can
 * complete an owned one. The Done button follows that rule rather than being
 * shown everywhere and 403-ing, because a button that is guaranteed to fail
 * teaches people the product is unreliable.
 */
export function TaskBoard({
  incidentId,
  tasks,
  currentUserId,
  canCommand,
  onChanged,
}: {
  incidentId: string;
  tasks: Task[];
  currentUserId: string | null;
  canCommand: boolean;
  onChanged: () => void;
}) {
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const open = tasks.filter((task) => task.status === "open");
  const settled = tasks.filter((task) => task.status !== "open");

  async function run(key: string, work: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await work();
      onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "That did not work");
    } finally {
      setBusy(null);
    }
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    const value = title.trim();
    if (!value) return;
    await run("new", async () => {
      await createTask(incidentId, { title: value });
      setTitle("");
    });
  }

  function canComplete(task: Task): boolean {
    if (task.status !== "open") return false;
    if (!task.owner_user_id) return true; // unassigned: anybody may close it out
    return task.owner_user_id === currentUserId;
  }

  return (
    <section aria-label="Incident tasks" className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
          Commitments
        </h2>
        <span className="text-xs text-ink-muted">
          {open.length} open · {open.filter((task) => !task.assigned).length}{" "}
          unassigned
        </span>
      </div>

      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-2.5 py-1.5 text-sm text-danger">
          {error}
        </p>
      )}

      {tasks.length === 0 ? (
        <p className="text-xs text-ink-muted">
          Nothing has been committed to yet. Say what you are picking up on the
          bridge and it becomes a task here.
        </p>
      ) : (
        <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised">
          {[...open, ...settled].map((task) => (
            <li key={task.id} className="flex flex-wrap items-start gap-3 px-3 py-2">
              <div className="min-w-48 flex-1">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span
                    className={
                      task.status === "open" ? "font-medium" : "text-ink-muted line-through"
                    }
                  >
                    {task.title}
                  </span>
                  {task.source === "bridge" && (
                    <span className="border border-line px-2 py-0.5 text-[10px] uppercase text-ink-muted">
                      from the bridge
                    </span>
                  )}
                  {task.overdue && (
                    <span className="border border-danger/40 bg-danger/10 px-2 py-0.5 text-[10px] text-danger">
                      overdue
                    </span>
                  )}
                </div>
                <p className="mt-0.5 text-xs text-ink-muted">
                  {task.assigned ? (
                    <>owned by {task.owner_user_id}</>
                  ) : task.owner_label ? (
                    <span className="text-warn">
                      unassigned — committed as “{task.owner_label}”, which this
                      platform could not resolve to exactly one person
                    </span>
                  ) : (
                    <span className="text-warn">unassigned</span>
                  )}
                  {task.due_label && ` · due ${task.due_label}`}
                </p>
                {task.evidence_ref && (
                  <p className="mt-0.5 text-[11px] text-ink-muted">
                    said: “{task.evidence_ref}”
                  </p>
                )}
              </div>

              {canComplete(task) && (
                <button
                  type="button"
                  disabled={busy === task.id}
                  onClick={() => void run(task.id, () => completeTask(incidentId, task.id))}
                  className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-ok hover:text-ok disabled:opacity-60"
                >
                  Done
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {canCommand && (
        <form onSubmit={add} className="flex gap-2">
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Add a task"
            className="flex-1 rounded-card border border-line bg-surface-raised px-2 py-1 text-sm text-ink"
          />
          <button
            type="submit"
            disabled={busy === "new" || !title.trim()}
            className="rounded-card border border-line px-2.5 py-1 text-sm hover:border-accent hover:text-accent-strong disabled:opacity-60"
          >
            Add
          </button>
        </form>
      )}
    </section>
  );
}
