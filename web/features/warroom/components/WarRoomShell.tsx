"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { fetchMe } from "@/features/auth/api";
import { AckBoard } from "@/features/warroom/components/AckBoard";
import { BridgePanel } from "@/features/warroom/components/BridgePanel";
import { Panel } from "@/features/warroom/components/Panel";
import { TaskBoard } from "@/features/warroom/components/TaskBoard";
import { joinWarRoom } from "@/features/warroom/api";
import { useWarRoom } from "@/features/warroom/hooks/useWarRoom";
import { getIncident, IncidentDetail } from "@/features/incidents/api";

/**
 * Screen 9 - the war-room shell.
 *
 * The ten panel titles are a fixed vocabulary and appear here verbatim:
 * Incident Overview, Live Bridge Transcript, Team Chat, AI Incident Brain,
 * Next Best Action, AI Copilot, Incident Timeline, Warning and Guidance,
 * Health Score and Metrics, Affected Services - plus Who is in the room.
 *
 * Four of them describe agent features that Sprint 5 builds. They are rendered
 * *now*, with their real titles and a sentence saying what will fill them,
 * rather than hidden until they work. Two reasons: the layout an incident
 * commander learns should not move under them when the agents land, and a panel
 * that quietly does not exist is indistinguishable from one that is broken.
 *
 * "Who is in the room" shows presence, not invitations. Somebody who was paged
 * and has not answered is not in the room, and the panel says which is which -
 * that difference is the whole reason a commander looks at it.
 */
export function WarRoomShell({ incidentId }: { incidentId: string }) {
  const { state, reload } = useWarRoom(incidentId);
  const [me, setMe] = useState<{ user_id: string; permissions: string[] } | null>(
    null,
  );
  const [incident, setIncident] = useState<IncidentDetail | null>(null);
  const [joining, setJoining] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void fetchMe()
      .then((who) => setMe({ user_id: who.user_id, permissions: who.permissions }))
      .catch(() => setMe(null));
  }, []);

  useEffect(() => {
    void getIncident(incidentId)
      .then(setIncident)
      .catch(() => setIncident(null));
  }, [incidentId, state]);

  if (state.kind === "loading") {
    return <p className="text-ink-muted">Opening the war room…</p>;
  }
  if (state.kind === "error") {
    return (
      <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-danger">
        {state.message}
      </p>
    );
  }

  const { room, brief, bridge } = state;
  const mine = me
    ? room.members.find((member) => member.user_id === me.user_id)
    : undefined;
  const canCommand = Boolean(me?.permissions.includes("declare_incident"));
  const radius = room.blast_radius;

  async function join() {
    setJoining(true);
    setError(null);
    try {
      await joinWarRoom(incidentId);
      reload();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not join");
    } finally {
      setJoining(false);
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{room.reference}</h1>
        <span className="rounded-full border border-danger/40 bg-danger/10 px-2.5 py-1 font-mono text-sm font-semibold text-danger">
          {room.severity}
        </span>
        <span className="rounded-full border border-line px-2.5 py-1 text-xs">
          {room.state}
        </span>
        <Link
          href={`/apps/${encodeURIComponent(room.app_id)}`}
          className="text-xs text-ink-muted underline decoration-line hover:text-accent-strong"
        >
          {room.app_id}
        </Link>
        <span className="flex-1" />
        <span className="text-xs text-ink-muted">
          {room.present_count} in the room · {room.members.length} in the radius
        </span>
        {!mine?.present && (
          <button
            type="button"
            disabled={joining}
            onClick={() => void join()}
            className="rounded-card bg-accent px-3 py-1.5 text-sm font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
          >
            Join the room
          </button>
        )}
      </header>

      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      {room.gaps.length > 0 && (
        <section
          aria-label="Membership gaps"
          className="rounded-card border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn"
        >
          <strong className="font-medium">
            {room.gaps.length} role{room.gaps.length === 1 ? "" : "s"} nobody holds
            for this application.
          </strong>{" "}
          {room.gaps.map((gap) => `${gap.role} (${gap.reason})`).join("; ")}.
        </section>
      )}

      <div className="grid gap-3 lg:grid-cols-3">
        <Panel
          title="Incident Overview"
          subtitle={`opened ${room.opened_at.slice(11, 16)}`}
        >
          <p className="text-sm font-medium">{room.title}</p>
          <dl className="mt-2 space-y-1 text-xs text-ink-muted">
            <div>
              <dt className="inline font-medium">Blast radius: </dt>
              <dd className="inline">{radius.summary}</dd>
            </div>
            <div>
              <dt className="inline font-medium">Evidence: </dt>
              <dd className="inline">
                {radius.source === "proposal"
                  ? "a detection verdict at declare time"
                  : "the severity a person typed; the detector had nothing to say"}
              </dd>
            </div>
            {incident?.ticket && (
              <div>
                <dt className="inline font-medium">ITSM: </dt>
                <dd className="inline">
                  {incident.ticket.reference
                    ? `${incident.ticket.reference} (${incident.ticket.state})`
                    : incident.ticket.detail || incident.ticket.state}
                </dd>
              </div>
            )}
            {incident?.change_windows.length ? (
              <div>
                <dt className="inline font-medium">Changing: </dt>
                <dd className="inline">
                  {incident.change_windows
                    .map((change) => `${change.service} ${change.version ?? ""}`.trim())
                    .join(", ")}
                </dd>
              </div>
            ) : null}
          </dl>
        </Panel>

        <Panel
          title="Who is in the room"
          subtitle={`${room.present_count} present`}
        >
          <ul className="space-y-1.5 text-xs">
            {room.members.map((member) => (
              <li key={member.user_id} className="flex flex-wrap items-center gap-2">
                <span
                  aria-hidden
                  className={`h-1.5 w-1.5 rounded-full ${member.present ? "bg-ok" : "bg-line"}`}
                />
                <span className="font-medium">
                  {member.display_name || member.user_id}
                </span>
                {member.role && (
                  <span className="rounded-full border border-line px-1.5 py-0.5 text-[10px] text-ink-muted">
                    {member.role}
                  </span>
                )}
                {/* Three separate facts, never collapsed into one status. */}
                <span className="text-ink-muted">
                  {member.present
                    ? "here"
                    : member.paged
                      ? "paged, not answered"
                      : "in the radius"}
                </span>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel
          title="Health Score and Metrics"
          subtitle="as declared"
          actions={
            <Link
              href={`/apps/${encodeURIComponent(room.app_id)}`}
              className="text-xs text-ink-muted hover:text-accent-strong"
            >
              live view
            </Link>
          }
        >
          {incident?.proposal ? (
            <div className="space-y-1 text-xs">
              <p className="font-mono text-2xl font-semibold">
                {incident.proposal.health_score === null
                  ? "--"
                  : incident.proposal.health_score.toFixed(0)}
                <span className="ml-2 text-sm font-normal text-ink-muted">
                  {incident.proposal.health_band || "unknown"}
                </span>
              </p>
              {/* A snapshot, and it says so: the live number has moved since. */}
              <p className="text-ink-muted">
                What the detector was reporting at the moment Declare was pressed.
                It has not been recomputed since.
              </p>
              {incident.proposal.success_rate_pct !== null && (
                <p className="text-ink-muted">
                  Success rate {incident.proposal.success_rate_pct.toFixed(1)}% ·{" "}
                  {incident.proposal.regions_affected} of{" "}
                  {incident.proposal.regions_total} regions
                </p>
              )}
            </div>
          ) : (
            <p className="text-xs text-ink-muted">
              No detection verdict was attached to this declaration.
            </p>
          )}
        </Panel>

        <Panel
          title="Affected Services"
          subtitle={radius.truncated ? "truncated" : undefined}
        >
          {radius.services.length === 0 ? (
            <p className="text-xs text-ink-muted">
              No topology is mapped for this application, so nothing here says
              what is downstream of the fault.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {radius.services.map((service) => (
                <li
                  key={service}
                  className="rounded-full border border-line px-2 py-0.5 text-xs"
                >
                  {service}
                </li>
              ))}
            </ul>
          )}
          {radius.regions.length > 0 && (
            <p className="mt-2 text-xs text-ink-muted">
              Regions: {radius.regions.join(", ")}
              {radius.regions_total ? ` of ${radius.regions_total}` : ""}
            </p>
          )}
          {radius.customer_facing && (
            <p className="mt-1 text-xs text-danger">Customers are feeling this.</p>
          )}
        </Panel>

        <Panel
          title="AI Incident Brain"
          subtitle={brief ? `brief: ${brief.source}` : undefined}
        >
          {brief ? (
            <div className="space-y-2 text-xs">
              <p className="font-medium">{brief.headline}</p>
              {brief.what_we_know.slice(0, 4).map((line) => (
                <p key={line} className="text-ink-muted">
                  {line}
                </p>
              ))}
              {/* The caveats are rendered, not buried behind a toggle. A brief
                  that reads confidently from thin evidence is the failure mode
                  this section exists to prevent. */}
              <ul className="space-y-0.5 border-t border-line pt-1.5 text-warn">
                {brief.caveats.map((caveat) => (
                  <li key={caveat}>{caveat}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-xs text-ink-muted">No brief could be built.</p>
          )}
        </Panel>

        <Panel
          title="Next Best Action"
          pending="Sprint 5 fills this: a ranked next action with its evidence, its risk class and who must approve it. Until then, high-risk actions can never auto-execute - that floor is already enforced in the domain, not by this panel."
        />

        <Panel
          title="AI Copilot"
          pending="Sprint 5 fills this: ask a question about this incident and get an answer with citations. The llm.provider adapter, its cache and its degraded fallback are configured; nothing is wired to this panel yet."
        />

        <Panel
          title="Warning and Guidance"
          pending="Sprint 5 fills this: what the platform is unsure about, what it refused to do and why. The refusals already exist as events on the timeline below."
        />

        <Panel title="Team Chat" pending="Sprint 5 fills this: room chat separate from the bridge transcript, so notes to each other do not become part of the record of what was said on the call." />

        <Panel
          title="Live Bridge Transcript"
          subtitle={room.bridge_channel ?? undefined}
        >
          <BridgePanel
            incidentId={incidentId}
            bridge={bridge}
            onPosted={reload}
          />
        </Panel>

        <Panel
          title="Incident Timeline"
          subtitle={`${incident?.transitions.length ?? 0} moves`}
        >
          {incident && incident.transitions.length > 0 ? (
            <ul className="space-y-1 text-xs">
              {incident.transitions.map((move, index) => (
                <li key={`${move.at}-${index}`} className="flex flex-wrap gap-2">
                  <span className="font-mono text-ink-muted">
                    {move.at.slice(11, 16)}
                  </span>
                  <span>
                    {move.kind === "severity"
                      ? `severity ${move.severity_before ?? "?"} → ${move.severity_after ?? "?"}`
                      : `${move.from_state} → ${move.to_state}`}
                  </span>
                  <span className="text-ink-muted">by {move.actor_id}</span>
                  {move.reason && (
                    <span className="text-ink-muted">— {move.reason}</span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-ink-muted">
              Nothing has moved yet - this incident is new.
            </p>
          )}
        </Panel>
      </div>

      <section aria-label="Acknowledgement board" className="space-y-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
          Paging
        </h2>
        <AckBoard
          incidentId={incidentId}
          pages={room.pages}
          currentUserId={me?.user_id ?? null}
          canEscalate={canCommand}
          onChanged={reload}
        />
      </section>

      <TaskBoard
        incidentId={incidentId}
        tasks={room.tasks}
        currentUserId={me?.user_id ?? null}
        canCommand={canCommand}
        onChanged={reload}
      />
    </div>
  );
}
