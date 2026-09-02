"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

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
import { BridgePanel } from "@/features/warroom/components/BridgePanel";
import { IncidentHeader } from "@/features/warroom/components/IncidentHeader";
import {
  ExpandedHeader,
  ExpandLink,
  IncidentShell,
} from "@/features/warroom/components/IncidentShell";
import { Panel } from "@/features/warroom/components/Panel";
import { TaskBoard } from "@/features/warroom/components/TaskBoard";
import { joinWarRoom } from "@/features/warroom/api";
import { ROOM_POLL_MS, useWarRoom } from "@/features/warroom/hooks/useWarRoom";
import { getIncident, IncidentDetail } from "@/features/incidents/api";
import {
  layoutFor,
  roleLabel,
  ROLE_VIEWS,
  type PanelKey,
} from "@/features/warroom/roleLayouts";
import { Avatar, Badge, EmptyState, StatePill } from "@/lib/ui/primitives";
import { Button } from "@/lib/ui/patterns";

/**
 * Screen 9 - the war-room cockpit.
 *
 * The ten panel titles are a fixed vocabulary and appear here verbatim:
 * Incident Overview, Live Bridge Transcript, Team Chat, AI Incident Brain,
 * Next Best Action, AI Copilot, Incident Timeline, Warning and Guidance,
 * Health Score and Metrics, Affected Services - plus Who is in the room.
 *
 * S8-T05 replaced the vertical stack this was built as with the prototype's
 * three-column grid. Nothing about what a panel contains changed; where it sits
 * did. The columns are testimony, decision and measurement, in that order,
 * because that is the order a commander walking in needs them.
 *
 * The deep screens - the brain, the approval, the ledger, the blast radius,
 * guidance, communications, the layered verdict, the copilot - are not separate
 * pages. Each is this same screen with one panel expanded into a document, which
 * is what the prototype does, and it means nobody ever loses the incident by
 * reading part of it.
 *
 * The brain shares this screen's socket rather than opening a second one: the
 * server's cockpit consumer subscribes to `brain.*` alongside `incident.*` and
 * `warroom.*`. There is one WebSocket store in this application and it stays
 * that way.
 *
 * "Who is in the room" shows presence, not invitations. Somebody who was paged
 * and has not answered is not in the room, and the panel says which is which -
 * that difference is the whole reason a commander looks at it.
 */

type Deep =
  | null
  | "brain"
  | "action"
  | "ledger"
  | "radius"
  | "guidance"
  | "comms"
  | "verdict"
  | "copilot";

export function WarRoomShell({
  incidentId,
  role,
}: {
  incidentId: string;
  /** S17. Absent means the full cockpit; a role means that role's view of it. */
  role?: string;
}) {
  const layout = layoutFor(role);
  const visible = new Set<PanelKey>(layout.panels);
  /* Presence, not position. A panel this role does not need is absent; every
     panel that stays is exactly where the full cockpit puts it, so a screenshot
     in a runbook stays true for everybody. */
  const show = (key: PanelKey, node: ReactNode) =>
    visible.has(key) ? node : null;
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
  const [deep, setDeep] = useState<Deep>(null);

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
      <p className="border border-accent bg-brand-100 px-3 py-2 text-accent-strong">
        {state.message}
      </p>
    );
  }

  const { room, brief, bridge } = state;
  const mine = me
    ? room.members.find((member) => member.user_id === me.user_id)
    : undefined;
  // A read-only role view removes the controls and keeps the information. The
  // server enforces permissions either way; this is about what a view is *for*,
  // not about what a person is allowed to do - an executive pressing "run this
  // action" during a P0 is a failure mode rather than a feature.
  const canCommand =
    !layout.readOnly && Boolean(me?.permissions.includes("declare_incident"));
  const canApprove =
    !layout.readOnly && Boolean(me?.permissions.includes("approve_high_risk"));
  const canAudit = Boolean(me?.permissions.includes("read_audit"));
  const governance = governanceState.kind === "ready" ? governanceState : null;
  // Approvals waiting for somebody. Rendered above the grid rather than inside
  // the Next Best Action panel: an unanswered high-risk approval is the most
  // time-critical thing on this screen, and it must not be a scroll away.
  const awaitingApproval =
    governance?.ledger.executions.filter(
      (execution) => execution.state === "approval_required",
    ) ?? [];
  const radius = room.blast_radius;
  const commander = room.members.find(
    (member) => member.role === "incident_commander",
  );
  const modelDegraded = brief?.source === "degraded";
  const closed = room.state === "closed" || Boolean(room.closed_at);

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

  const header = (
    <IncidentHeader
      reference={room.reference}
      title={room.title}
      severity={room.severity}
      state={room.state}
      appId={room.app_id}
      openedAt={room.opened_at}
      connected={connected}
      pollSeconds={ROOM_POLL_MS / 1000}
      presentCount={room.present_count}
      radiusCount={room.members.length}
      commander={commander?.display_name ?? null}
      degraded={modelDegraded}
      actions={
        <>
          {(closed || canAudit) && (
            <Link
              href={`/incidents/${encodeURIComponent(incidentId)}/replay`}
              className="text-small font-semibold text-accent hover:underline"
            >
              Audit replay
            </Link>
          )}
          {!mine?.present && (
            <Button
              variant="primary"
              size="small"
              disabled={joining}
              onClick={() => void join()}
            >
              Join the room
            </Button>
          )}
        </>
      }
    />
  );

  const banner = (
    <>
      {role && (
        <section
          aria-label="Role view"
          className="flex flex-wrap items-baseline gap-3 border border-line bg-surface-raised px-4 py-2.5"
        >
          <span className="text-tiny uppercase tracking-panel text-accent">
            {roleLabel(role)} view
          </span>
          <span className="text-dense text-ink-muted">{layout.purpose}</span>
          <span className="ml-auto flex flex-wrap items-baseline gap-2 text-small">
            {ROLE_VIEWS.filter((one) => one !== role).map((one) => (
              <Link
                key={one}
                href={`/incidents/${encodeURIComponent(incidentId)}/as/${one}`}
                className="text-accent hover:underline"
              >
                {roleLabel(one)}
              </Link>
            ))}
            <Link
              href={`/incidents/${encodeURIComponent(incidentId)}`}
              className="font-semibold text-accent hover:underline"
            >
              full cockpit
            </Link>
          </span>
        </section>
      )}
      {error && (
        <p className="border border-accent bg-brand-100 px-3 py-2 text-dense text-accent-strong">
          {error}
        </p>
      )}
      {room.gaps.length > 0 && (
        <section
          aria-label="Membership gaps"
          className="border border-warn bg-warn-bg px-3 py-2 text-dense text-warn-ink"
        >
          <strong className="font-semibold">
            {room.gaps.length} role{room.gaps.length === 1 ? "" : "s"} nobody holds
            for this application.
          </strong>{" "}
          {room.gaps.map((gap) => `${gap.role} (${gap.reason})`).join("; ")}.
        </section>
      )}
    </>
  );

  const approvals =
    awaitingApproval.length > 0 ? (
      <section aria-label="Approvals" className="flex flex-col gap-2">
        <h2 className="text-h6 text-ink-muted">Waiting for a person</h2>
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
    ) : null;

  /* ── left: what people said ───────────────────────────────────────────── */

  const left = (
    <>
      {show("bridge", (
        <Panel
          title="Live Bridge Transcript"
          subtitle={room.bridge_channel ?? undefined}
          actions={room.has_bridge ? <Badge label="SIMULATED" size="nano" /> : undefined}
        >
          <BridgePanel incidentId={incidentId} bridge={bridge} onPosted={reload} />
        </Panel>
      ))}

      {show("chat", (
        <Panel title="Team Chat" pending="Not built, and not scheduled. Room chat separate from the bridge transcript was reserved here in Sprint 4 and Sprint 6 did not fill it: the bridge is the record of what was said, and a second channel that is not part of that record is a place decisions go missing. It stays visible rather than removed so the layout an incident commander learned does not move." />
      ))}

      {show("room", (
        <Panel title="Who is in the room" subtitle={`${room.present_count} present`}>
          <ul className="flex flex-col gap-1.5 text-small">
            {room.members.map((member) => (
              <li key={member.user_id} className="flex items-center gap-2">
                <Avatar
                  name={member.display_name || member.user_id}
                  present={member.present}
                  lead={member.role === "incident_commander"}
                />
                <span className="min-w-0 truncate">
                  {member.display_name || member.user_id}
                  {member.role && (
                    <span className="text-ink-muted"> · {member.role.replace(/_/g, " ")}</span>
                  )}
                </span>
                {/* Three separate facts, never collapsed into one status. */}
                <span className="ml-auto shrink-0 text-tiny font-semibold uppercase tracking-label">
                  {member.present ? (
                    <span className="text-ok">joined</span>
                  ) : member.paged ? (
                    <span className="text-warn-ink">paged</span>
                  ) : (
                    <span className="text-neutral-500">in the radius</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      ))}

      {show("copilot", (
        <Panel
          title="AI Copilot"
          subtitle="answers only from this incident's record"
        >
          <CopilotPanel
            incidentId={incidentId}
            nodes={brainState.kind === "ready" ? brainState.brain.nodes : []}
          />
          <ExpandLink onClick={() => setDeep("copilot")}>Open copilot</ExpandLink>
        </Panel>
      ))}
    </>
  );

  /* ── centre: what to do ───────────────────────────────────────────────── */

  const centre = (
    <>
      {show("brain", (
        <Panel
          title="AI Incident Brain"
          subtitle={
            brainState.kind === "ready"
              ? `${brainState.brain.hypotheses.length} hypotheses`
              : undefined
          }
          elevated
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
              <div className="flex items-baseline gap-2">
                <h3 className="text-h6 text-ink-muted">Join brief</h3>
                <span className="text-nano uppercase tracking-label text-neutral-600">
                  written by {brief.source}
                </span>
              </div>
              <p className="mt-1 text-small font-semibold">{brief.headline}</p>
              {brief.what_we_know.slice(0, 4).map((line) => (
                <p key={line} className="text-micro text-ink-muted">
                  {line}
                </p>
              ))}
              {/* The caveats are rendered, not buried behind a toggle. A brief
                  that reads confidently from thin evidence is the failure mode
                  this section exists to prevent - and the model writer is
                  required to keep every one of them. */}
              <ul className="mt-1 flex flex-col gap-0.5 text-micro text-warn-ink">
                {brief.caveats.map((caveat) => (
                  <li key={caveat}>{caveat}</li>
                ))}
              </ul>
            </div>
          )}
          <ExpandLink onClick={() => setDeep("brain")}>Expand the brain</ExpandLink>
        </Panel>
      ))}

      {show("action", (
        <Panel
          title="Next Best Action"
          subtitle={
            governance?.nba
              ? `${governance.nba.degraded ? "deterministic" : "model-explained"} · policy v${governance.nba.policy_version}`
              : undefined
          }
          accent="accent"
          elevated
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
          <ExpandLink onClick={() => setDeep("action")}>
            Plan, risk and approval
          </ExpandLink>
        </Panel>
      ))}

      {show("timeline", (
        <Panel
          title="Incident Timeline"
          subtitle={`${incident?.transitions.length ?? 0} moves`}
        >
          {incident && incident.transitions.length > 0 ? (
            <ul className="flex flex-col text-small">
              {incident.transitions.map((move, index) => (
                <li
                  key={`${move.at}-${index}`}
                  className="grid grid-cols-[48px_1fr] gap-2.5 border-t border-line py-1.5"
                >
                  <span className="font-mono text-ink-muted">
                    {move.at.slice(11, 16)}
                  </span>
                  <span>
                    {move.kind === "severity"
                      ? `severity ${move.severity_before ?? "?"} → ${move.severity_after ?? "?"}`
                      : `${move.from_state} → ${move.to_state}`}
                    <span className="text-ink-muted"> by {move.actor_id}</span>
                    {move.reason && (
                      <span className="text-ink-muted"> — {move.reason}</span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>Nothing has moved yet - this incident is new.</EmptyState>
          )}
        </Panel>
      ))}
    </>
  );

  /* ── right: what is true ──────────────────────────────────────────────── */

  const right = (
    <>
      {show("overview", (
        <Panel title="Incident Overview" subtitle={`opened ${room.opened_at.slice(11, 16)}`}>
          <p className="text-dense font-semibold">{room.title}</p>
          <dl className="mt-2 flex flex-col gap-1 text-small text-ink-muted">
            <div>
              <dt className="inline font-semibold">Blast radius: </dt>
              <dd className="inline">{radius.summary}</dd>
            </div>
            <div>
              <dt className="inline font-semibold">Evidence: </dt>
              <dd className="inline">
                {radius.source === "proposal"
                  ? "a detection verdict at declare time"
                  : "the severity a person typed; the detector had nothing to say"}
              </dd>
            </div>
            {incident?.ticket && (
              <div>
                <dt className="inline font-semibold">ITSM: </dt>
                <dd className="inline">
                  {incident.ticket.reference
                    ? `${incident.ticket.reference} (${incident.ticket.state})`
                    : incident.ticket.detail || incident.ticket.state}
                </dd>
              </div>
            )}
            {incident?.change_windows.length ? (
              <div>
                <dt className="inline font-semibold">Changing: </dt>
                <dd className="inline">
                  {incident.change_windows
                    .map((change) => `${change.service} ${change.version ?? ""}`.trim())
                    .join(", ")}
                </dd>
              </div>
            ) : null}
          </dl>
        </Panel>
      ))}

      {show("health", (
        <Panel
          title="Health Score and Metrics"
          subtitle="as declared"
          actions={
            <Link
              href={`/apps/${encodeURIComponent(room.app_id)}`}
              className="text-small text-accent hover:underline"
            >
              live view
            </Link>
          }
        >
          {incident?.proposal ? (
            <div className="flex flex-col gap-1 text-small">
              <p className="flex items-baseline gap-2">
                <span className="font-display text-hero font-extrabold leading-none">
                  {incident.proposal.health_score === null
                    ? "--"
                    : incident.proposal.health_score.toFixed(0)}
                </span>
                <StatePill state={incident.proposal.health_band || "unknown"} />
              </p>
              {/* A snapshot, and it says so: the live number has moved since. */}
              <p className="text-ink-muted">
                What the detector was reporting at the moment Declare was pressed.
                It has not been recomputed since.
              </p>
              {incident.proposal.success_rate_pct !== null && (
                <p className="font-mono text-ink-muted">
                  success {incident.proposal.success_rate_pct.toFixed(1)}% ·{" "}
                  {incident.proposal.regions_affected} of{" "}
                  {incident.proposal.regions_total} regions
                </p>
              )}
            </div>
          ) : (
            <EmptyState>
              No detection verdict was attached to this declaration.
            </EmptyState>
          )}
        </Panel>
      ))}

      {show("radius", (
        <Panel
          title="Affected Services"
          subtitle={radius.customer_facing ? "customers are feeling this" : undefined}
        >
          {/* S13. The blast radius the room was assembled from is the header's
              summary above; this panel is the map it came from, with CONFIRMED
              kept apart from PREDICTED. */}
          <TopologyPanel incidentId={incidentId} />
          {radius.regions.length > 0 && (
            <p className="mt-2 text-small text-ink-muted">
              Regions declared: {radius.regions.join(", ")}
              {radius.regions_total ? ` of ${radius.regions_total}` : ""}
            </p>
          )}
          <ExpandLink onClick={() => setDeep("radius")}>Open blast radius</ExpandLink>
        </Panel>
      ))}

      {show("guidance", (
        <Panel title="Warning and Guidance" subtitle="advisory, not measured">
          <GuidancePanel
            guidance={brainState.kind === "ready" ? brainState.guidance : null}
          />
          <ExpandLink onClick={() => setDeep("guidance")}>
            History and relevance
          </ExpandLink>
        </Panel>
      ))}
    </>
  );

  /* ── below: the wide panels ───────────────────────────────────────────── */

  const below = (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 lg:grid-cols-2">
        {show("ledger", (
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
            <ExpandLink onClick={() => setDeep("ledger")}>Open the ledger</ExpandLink>
          </Panel>
        ))}

        {show("validation", (
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
            <ExpandLink onClick={() => setDeep("verdict")}>
              Layered verdict
            </ExpandLink>
          </Panel>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {show("plan", (
          <Panel
            title="Response Plan"
            subtitle={
              governance?.nba?.plan ? `version ${governance.nba.plan.version}` : undefined
            }
          >
            <PlanDiff
              incidentId={incidentId}
              nba={governance?.nba ?? null}
              canCommand={canCommand}
              onChanged={reloadGovernance}
            />
          </Panel>
        ))}

        {show("comms", (
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
            <ExpandLink onClick={() => setDeep("comms")}>
              Audiences and redlines
            </ExpandLink>
          </Panel>
        ))}
      </div>

      {show("paging", (
        <section aria-label="Acknowledgement board" className="flex flex-col gap-2">
          <h2 className="text-h6 text-ink-muted">Paging</h2>
          <AckBoard
            incidentId={incidentId}
            pages={room.pages}
            currentUserId={me?.user_id ?? null}
            canEscalate={canCommand}
            onChanged={reload}
          />
        </section>
      ))}

      {show("tasks", (
        <TaskBoard
          incidentId={incidentId}
          tasks={room.tasks}
          currentUserId={me?.user_id ?? null}
          canCommand={canCommand}
          onChanged={reload}
        />
      ))}
    </div>
  );

  /* ── the deep views: S10 to S18, one at a time ────────────────────────── */

  const close = () => setDeep(null);
  const expanded = deep ? (
    <div className="mx-auto flex w-full max-w-[1240px] flex-col gap-5">
      {deep === "brain" && (
        <>
          <ExpandedHeader
            eyebrow="Incident brain"
            title="Competing hypotheses and the ledger they were computed from"
            onClose={close}
          />
          <BrainPanel
            incidentId={incidentId}
            state={brainState}
            canRefresh={canCommand}
            onRefreshed={() => {
              reloadBrain();
              reload();
            }}
          />
        </>
      )}
      {deep === "action" && (
        <>
          <ExpandedHeader
            eyebrow="Next best action"
            title="The plan, its risk, and who has to approve it"
            onClose={close}
          />
          <div className="grid items-start gap-6 lg:grid-cols-[1.2fr_1fr]">
            <NextBestAction
              incidentId={incidentId}
              nba={governance?.nba ?? null}
              canCommand={canCommand}
              onChanged={() => {
                reloadGovernance();
                reload();
              }}
            />
            <PlanDiff
              incidentId={incidentId}
              nba={governance?.nba ?? null}
              canCommand={canCommand}
              onChanged={reloadGovernance}
            />
          </div>
        </>
      )}
      {deep === "ledger" && (
        <>
          <ExpandedHeader
            eyebrow="Execution ledger"
            title="Every attempt, and what is known about its outcome"
            onClose={close}
          />
          <ExecutionLedger
            incidentId={incidentId}
            ledger={governance?.ledger ?? { executions: [], approvals: [] }}
            canCommand={canCommand}
            onChanged={() => {
              reloadGovernance();
              reload();
            }}
          />
        </>
      )}
      {deep === "radius" && (
        <>
          <ExpandedHeader
            eyebrow="Blast radius"
            title="What was measured, and what only depends on it"
            onClose={close}
          />
          <TopologyPanel incidentId={incidentId} />
        </>
      )}
      {deep === "guidance" && (
        <>
          <ExpandedHeader
            eyebrow="Warning and guidance"
            title="What went wrong last time, and how relevant it is now"
            onClose={close}
          />
          <GuidancePanel
            guidance={brainState.kind === "ready" ? brainState.guidance : null}
          />
        </>
      )}
      {deep === "comms" && (
        <>
          <ExpandedHeader
            eyebrow="Communications"
            title="Every audience, and nothing that sends itself"
            onClose={close}
          />
          <CommsPanel
            incidentId={incidentId}
            comms={governance?.comms ?? null}
            permissions={me?.permissions ?? []}
            onChanged={reloadGovernance}
          />
        </>
      )}
      {deep === "verdict" && (
        <>
          <ExpandedHeader
            eyebrow="Layered recovery verdict"
            title="Technical, service, customer and business - all four, or not recovered"
            onClose={close}
          />
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
        </>
      )}
      {deep === "copilot" && (
        <>
          <ExpandedHeader
            eyebrow="IC copilot"
            title="Answers, with the record they came from"
            onClose={close}
          />
          <CopilotPanel
            incidentId={incidentId}
            nodes={brainState.kind === "ready" ? brainState.brain.nodes : []}
          />
        </>
      )}
    </div>
  ) : undefined;

  return (
    <IncidentShell
      header={header}
      banner={banner}
      approvals={approvals}
      left={left}
      centre={centre}
      right={right}
      below={below}
      expanded={expanded}
    />
  );
}
