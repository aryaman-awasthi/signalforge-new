"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { fetchMe } from "@/features/auth/api";
import { BrainPanel } from "@/features/brain/components/BrainPanel";
import { CopilotPanel } from "@/features/brain/components/CopilotPanel";
import { GuidancePanel } from "@/features/brain/components/GuidancePanel";
import { TopologyPanel } from "@/features/brain/components/TopologyPanel";
import { useBrain } from "@/features/brain/hooks/useBrain";
import { ApprovalSheet } from "@/features/governance/components/ApprovalSheet";
import { CommsPanel } from "@/features/governance/components/CommsPanel";
import { ExecutionLedger } from "@/features/governance/components/ExecutionLedger";
import { NextBestAction } from "@/features/governance/components/NextBestAction";
import { PlanDiff } from "@/features/governance/components/PlanDiff";
import { ValidationPanel } from "@/features/governance/components/ValidationPanel";
import { useGovernance } from "@/features/governance/hooks/useGovernance";
import { AckBoard } from "@/features/warroom/components/AckBoard";
import { LiveBadge } from "@/features/warroom/components/LiveBadge";
import { BridgePanel } from "@/features/warroom/components/BridgePanel";
import { Panel } from "@/features/warroom/components/Panel";
import { TaskBoard } from "@/features/warroom/components/TaskBoard";
import { joinWarRoom } from "@/features/warroom/api";
import { ROOM_POLL_MS, useWarRoom } from "@/features/warroom/hooks/useWarRoom";
import { getIncident, IncidentDetail } from "@/features/incidents/api";

/**
 * Screen 9 - the war-room shell.
 *
 * The ten panel titles are a fixed vocabulary and appear here verbatim:
 * Incident Overview, Live Bridge Transcript, Team Chat, AI Incident Brain,
 * Next Best Action, AI Copilot, Incident Timeline, Warning and Guidance,
 * Health Score and Metrics, Affected Services - plus Who is in the room.
 *
 * Sprint 5 fills four of them - AI Incident Brain, Affected Services, Warning
 * and Guidance, AI Copilot - in the places Sprint 4 reserved. The layout an
 * incident commander learned does not move: the titles, the order and the grid
 * are unchanged, and only the contents of those four panels are new. That was
 * the reason for rendering them as placeholders rather than hiding them.
 *
 * The brain is read through its own hook and its own routes, and it shares this
 * screen's socket rather than opening a second one: the server's cockpit
 * consumer subscribes to `brain.*` alongside `incident.*` and `warroom.*`, so a
 * hypothesis that moved arrives on the frame the platform was already sending.
 * There is one WebSocket store in this application and it stays that way.
 *
 * "Who is in the room" shows presence, not invitations. Somebody who was paged
 * and has not answered is not in the room, and the panel says which is which -
 * that difference is the whole reason a commander looks at it.
 */
export function WarRoomShell({ incidentId }: { incidentId: string }) {
  const { state, reload, connected } = useWarRoom(incidentId);
  const { state: brainState, reload: reloadBrain } = useBrain(incidentId);
  const { state: governanceState, reload: reloadGovernance } =
    useGovernance(incidentId);
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
  const canApprove = Boolean(me?.permissions.includes("approve_high_risk"));
  const governance =
    governanceState.kind === "ready" ? governanceState : null;
  // Approvals waiting for somebody. Rendered above the grid rather than inside
  // the Next Best Action panel: an unanswered high-risk approval is the most
  // time-critical thing on this screen, and it must not be a scroll away.
  const awaitingApproval =
    governance?.ledger.executions.filter(
      (execution) => execution.state === "approval_required",
    ) ?? [];
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
        <LiveBadge connected={connected} pollSeconds={ROOM_POLL_MS / 1000} />
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

      {/* S10 sits full width above the grid: three hypothesis cards, their
          confidence history and the ledger they were computed from do not fit
          in a third of a row, and it is the first thing an incident commander
          opens this screen for. */}
      <Panel
        title="AI Incident Brain"
        subtitle={
          brainState.kind === "ready"
            ? `${brainState.brain.hypotheses.length} hypotheses${
                brief ? ` · brief written by: ${brief.source}` : ""
              }`
            : undefined
        }
      >
        <BrainPanel
          incidentId={incidentId}
          state={brainState}
          canRefresh={canCommand}
          onRefreshed={() => {
            reloadBrain();
            reload();
          }}
        />
        {brief && (
          <div className="mt-3 border-t border-line pt-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
              Join brief
            </h3>
            <p className="mt-1 text-xs font-medium">{brief.headline}</p>
            {brief.what_we_know.slice(0, 4).map((line) => (
              <p key={line} className="text-[11px] text-ink-muted">
                {line}
              </p>
            ))}
            {/* The caveats are rendered, not buried behind a toggle. A brief
                that reads confidently from thin evidence is the failure mode
                this section exists to prevent - and the model writer is
                required to keep every one of them. */}
            <ul className="mt-1 space-y-0.5 text-[11px] text-warn">
              {brief.caveats.map((caveat) => (
                <li key={caveat}>{caveat}</li>
              ))}
            </ul>
          </div>
        )}
      </Panel>

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
          subtitle={
            radius.customer_facing ? "customers are feeling this" : undefined
          }
        >
          {/* S13. The blast radius the room was assembled from is still the
              header's summary above; this panel is the map it came from, with
              CONFIRMED kept apart from PREDICTED. */}
          <TopologyPanel incidentId={incidentId} />
          {radius.regions.length > 0 && (
            <p className="mt-2 text-xs text-ink-muted">
              Regions declared: {radius.regions.join(", ")}
              {radius.regions_total ? ` of ${radius.regions_total}` : ""}
            </p>
          )}
        </Panel>

        <Panel
          title="Next Best Action"
          subtitle={
            governance?.nba
              ? `${governance.nba.degraded ? "deterministic" : "model-explained"} · policy v${governance.nba.policy_version}`
              : undefined
          }
        >
          {/* There is no auto-execute control here for any action, at any risk
              class. A high-risk action's only path is Request approval, and the
              backend refuses `intent: auto` on the floor whether or not this
              panel offers it. */}
          <NextBestAction
            incidentId={incidentId}
            nba={governance?.nba ?? null}
            canCommand={canCommand}
            onChanged={() => {
              reloadGovernance();
              reload();
            }}
          />
        </Panel>

        <Panel
          title="AI Copilot"
          subtitle="answers only from this incident's record"
        >
          <CopilotPanel
            incidentId={incidentId}
            nodes={brainState.kind === "ready" ? brainState.brain.nodes : []}
          />
        </Panel>

        <Panel title="Warning and Guidance" subtitle="advisory, not measured">
          <GuidancePanel
            guidance={brainState.kind === "ready" ? brainState.guidance : null}
          />
        </Panel>

        <Panel title="Team Chat" pending="Not built, and not scheduled. Room chat separate from the bridge transcript was reserved here in Sprint 4 and Sprint 6 did not fill it: the bridge is the record of what was said, and a second channel that is not part of that record is a place decisions go missing. It stays visible rather than removed so the layout an incident commander learned does not move." />

        <Panel
          title="Response Plan"
          subtitle={
            governance?.nba?.plan
              ? `version ${governance.nba.plan.version}`
              : undefined
          }
        >
          <PlanDiff
            incidentId={incidentId}
            nba={governance?.nba ?? null}
            canCommand={canCommand}
            onChanged={reloadGovernance}
          />
        </Panel>

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

      {awaitingApproval.length > 0 && (
        <section aria-label="Approvals" className="space-y-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
            Waiting for a person
          </h2>
          {awaitingApproval.map((execution) => (
            <ApprovalSheet
              key={execution.execution_id}
              incidentId={incidentId}
              execution={execution}
              approval={
                governance?.ledger.approvals.find(
                  (approval) =>
                    approval.execution_id === execution.execution_id &&
                    approval.state !== "used",
                ) ?? null
              }
              canApprove={canApprove && execution.requested_by !== me?.user_id}
              onChanged={reloadGovernance}
            />
          ))}
        </section>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel
          title="Execution Ledger"
          subtitle={
            governance
              ? `${governance.ledger.executions.length} attempt${governance.ledger.executions.length === 1 ? "" : "s"}`
              : undefined
          }
        >
          <ExecutionLedger
            incidentId={incidentId}
            ledger={governance?.ledger ?? { executions: [], approvals: [] }}
            canCommand={canCommand}
            onChanged={() => {
              reloadGovernance();
              reload();
            }}
          />
        </Panel>

        <Panel
          title="Recovery Validation"
          subtitle={
            governance?.validation?.latest
              ? `cycle ${governance.validation.latest.cycle}`
              : "not validated"
          }
        >
          <ValidationPanel
            incidentId={incidentId}
            validation={governance?.validation ?? null}
            canCommand={canCommand}
            onChanged={() => {
              reloadGovernance();
              reload();
              reloadBrain();
            }}
          />
        </Panel>
      </div>

      <Panel
        title="Communications"
        subtitle={
          governance?.comms && governance.comms.contradicted.length > 0
            ? `${governance.comms.contradicted.length} contradicted`
            : "nothing sends itself"
        }
      >
        <CommsPanel
          incidentId={incidentId}
          comms={governance?.comms ?? null}
          permissions={me?.permissions ?? []}
          onChanged={reloadGovernance}
        />
      </Panel>

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
