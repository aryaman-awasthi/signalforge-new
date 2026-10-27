"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, type ReactNode } from "react";

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
import { TelemetryPanel } from "@/features/warroom/components/TelemetryPanel";
import { joinWarRoom } from "@/features/warroom/api";
import { ROOM_POLL_MS, useWarRoom } from "@/features/warroom/hooks/useWarRoom";
import { getIncident, IncidentDetail } from "@/features/incidents/api";
import {
  layoutFor,
  roleLabel,
  ROLE_VIEWS,
  type PanelKey,
} from "@/features/warroom/roleLayouts";
import { Avatar, Badge, EmptyState } from "@/lib/ui/primitives";
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
 * three-column grid. S10 made the grid *read* like the prototype: every panel
 * in the cockpit is its compact form - the brain's counters and ranked rows,
 * the one action with its safety verdict, the live telemetry strip, the
 * affected-services list - and the full form of each is one click away as a
 * deep view. A commander walking in sees one screen; a commander who wants the
 * evidence opens it. Nothing about what a panel contains changed; how much of
 * it is open at once did.
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

const DEEP_VIEWS = [
  "brain",
  "action",
  "ledger",
  "radius",
  "guidance",
  "comms",
  "verdict",
  "copilot",
] as const;

type Deep = null | (typeof DEEP_VIEWS)[number];

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
  const [deep, setDeepState] = useState<Deep>(null);

  /* The expanded view is in the URL, not only in this component's head.
     `?view=ledger` means an incident commander can send somebody a link to the
     thing they are talking about instead of "open the war room and click the
     ledger", and a screenshot in a runbook has an address. Written with
     history.replaceState rather than a router push so it never adds a history
     entry: closing a panel with the back button would be a surprise. */
  useEffect(() => {
    const view = new URLSearchParams(window.location.search).get("view");
    if (view && DEEP_VIEWS.includes(view as Exclude<Deep, null>)) {
      setDeepState(view as Deep);
    }
  }, [incidentId]);

  const setDeep = (next: Deep) => {
    setDeepState(next);
    const url = new URL(window.location.href);
    if (next) url.searchParams.set("view", next);
    else url.searchParams.delete("view");
    window.history.replaceState(null, "", url.toString());
  };

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

  /* user id -> display name, from the members the room already lists. The
     ledger and the approvals carry ids, and "requested by 4f1c8a2b" is a
     sentence nobody can act on. */
  const names = useMemo(() => {
    const out: Record<string, string> = {};
    if (state.kind === "ready") {
      for (const member of state.room.members) {
        if (member.display_name) out[member.user_id] = member.display_name;
      }
    }
    return out;
  }, [state]);

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
  const guidance = brainState.kind === "ready" ? brainState.guidance : null;
  // Approvals waiting for somebody, and approvals granted but not yet sent.
  // Rendered above the grid rather than inside the Next Best Action panel: an
  // unanswered high-risk approval is the most time-critical thing on this
  // screen, and a granted one that nobody sends is one that expires.
  const awaitingApproval =
    governance?.ledger.executions.filter(
      (execution) =>
        execution.state === "approval_required" || execution.state === "approved",
    ) ?? [];
  const radius = room.blast_radius;
  const commander = room.members.find(
    (member) => member.role === "incident_commander",
  );
  const modelDegraded = brief?.source === "degraded";
  const closed = room.state === "closed" || Boolean(room.closed_at);
  const refreshAll = () => {
    reloadGovernance();
    reload();
  };

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
            canCommand={canCommand}
            names={names}
            onChanged={refreshAll}
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
        <Panel
          title="Team Chat"
          pending="Not built. The bridge above is the record of what was said; a second channel that is not part of that record is where decisions go missing."
        />
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
          subtitle="situation model · versioned"
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
            compact
            contradictions={guidance?.contradictions.length ?? 0}
          />
          {brief && (
            <details className="mt-3 border-t border-line pt-2">
              <summary className="cursor-pointer text-small">
                <span className="font-semibold">Join brief</span>
                <span className="text-ink-muted"> · {brief.headline}</span>
                <span className="ml-1 text-nano uppercase tracking-label text-neutral-600">
                  written by {brief.source}
                </span>
              </summary>
              <div className="mt-1 flex flex-col gap-0.5">
                {brief.what_we_know.slice(0, 4).map((line) => (
                  <p key={line} className="text-micro text-ink-muted">
                    {line}
                  </p>
                ))}
                {/* The caveats are rendered, not buried behind a toggle. A brief
                    that reads confidently from thin evidence is the failure
                    mode this section exists to prevent. */}
                <ul className="mt-1 flex flex-col gap-0.5 text-micro text-warn-ink">
                  {brief.caveats.map((caveat) => (
                    <li key={caveat}>{caveat}</li>
                  ))}
                </ul>
              </div>
            </details>
          )}
          <div className="mt-2">
            <ExpandLink onClick={() => setDeep("brain")}>Expand the brain</ExpandLink>
          </div>
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
            onChanged={refreshAll}
            compact
            ledger={governance?.ledger ?? null}
            names={names}
          />
          <div className="mt-2">
            <ExpandLink onClick={() => setDeep("action")}>
              Plan, risk and approval
            </ExpandLink>
          </div>
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
                    <span className="text-ink-muted">
                      {" "}
                      by {names[move.actor_id] ?? move.actor_id}
                    </span>
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
      {show("health", (
        <Panel title="Health Score and Metrics" subtitle="live, from the detector" accent="accent">
          <TelemetryPanel
            appId={room.app_id}
            declaredScore={incident?.proposal?.health_score ?? null}
          />
        </Panel>
      ))}

      {show("overview", (
        <Panel title="Incident Overview" subtitle={`opened ${room.opened_at.slice(11, 16)}`}>
          <p className="text-dense font-semibold">{room.title}</p>
          <dl className="mt-2 flex flex-col gap-1 text-small text-ink-muted">
            <div>
              <dt className="inline font-semibold">Blast radius: </dt>
              <dd className="inline">{radius.summary}</dd>
            </div>
            <div>
              <dt className="inline font-semibold">Declared on: </dt>
              <dd className="inline">
                {radius.source === "proposal"
                  ? "the detector's verdict at the time"
                  : "a severity a person chose; the detector had nothing to say"}
              </dd>
            </div>
            {incident?.ticket && incident.ticket.state !== "not_configured" && (
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

      {show("radius", (
        <Panel
          title="Affected Services"
          subtitle={radius.customer_facing ? "customers are feeling this" : undefined}
        >
          {/* S13. The blast radius the room was assembled from is the header's
              summary above; this panel is the list it came from, with CONFIRMED
              kept apart from PREDICTED. The map is the deep view. */}
          <TopologyPanel incidentId={incidentId} compact />
          {radius.journeys.length > 0 && (
            <p className="mt-2 text-small text-ink-muted">
              Journeys: {radius.journeys.join(", ")}
              {radius.regions.length > 0 && ` · regions ${radius.regions.join(", ")}`}
              {radius.regions_total ? ` of ${radius.regions_total}` : ""}
            </p>
          )}
          <div className="mt-2">
            <ExpandLink onClick={() => setDeep("radius")}>Open blast radius</ExpandLink>
          </div>
        </Panel>
      ))}

      {show("guidance", (
        <Panel title="Warning and Guidance" subtitle="advisory, not measured">
          <GuidancePanel guidance={guidance} compact />
          <div className="mt-2">
            <ExpandLink onClick={() => setDeep("guidance")}>
              History and relevance
            </ExpandLink>
          </div>
        </Panel>
      ))}
    </>
  );

  /* ── below: the wide panels ───────────────────────────────────────────── */

  const below = (
    <div className="flex flex-col gap-4">
      {show("paging", (
        <section aria-label="Acknowledgement board" className="flex flex-col gap-2">
          <h2 className="text-h6 text-ink-muted">Paging</h2>
          <AckBoard
            incidentId={incidentId}
            pages={room.pages}
            currentUserId={me?.user_id ?? null}
            canEscalate={canCommand}
            onChanged={reload}
            names={names}
          />
        </section>
      ))}

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
              onChanged={refreshAll}
            />
            <div className="mt-2">
              <ExpandLink onClick={() => setDeep("ledger")}>Open the ledger</ExpandLink>
            </div>
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
                refreshAll();
                reloadBrain();
              }}
            />
            <div className="mt-2">
              <ExpandLink onClick={() => setDeep("verdict")}>
                Layered verdict
              </ExpandLink>
            </div>
          </Panel>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {show("plan", (
          <Panel
            title="Response Plan"
            subtitle={
              governance?.nba?.plan ? `version ${governance.nba.plan.version}` : "not written"
            }
          >
            <details>
              <summary className="cursor-pointer text-small text-ink-muted">
                {governance?.nba?.plan
                  ? `${governance.nba.plan.kept.length} kept · ${governance.nba.plan.paused.length} paused · ${governance.nba.plan.added.length} added - show the plan`
                  : "No plan has been written yet - open to write one"}
              </summary>
              <div className="mt-2">
                <PlanDiff
                  incidentId={incidentId}
                  nba={governance?.nba ?? null}
                  canCommand={canCommand}
                  onChanged={reloadGovernance}
                />
              </div>
            </details>
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
            <details open={Boolean(governance?.comms && governance.comms.contradicted.length > 0)}>
              <summary className="cursor-pointer text-small text-ink-muted">
                {governance?.comms?.communications.length
                  ? `${governance.comms.communications.length} statement${governance.comms.communications.length === 1 ? "" : "s"} - show the board`
                  : "Nothing drafted yet - open to draft from the record"}
              </summary>
              <div className="mt-2">
                <CommsPanel
                  incidentId={incidentId}
                  comms={governance?.comms ?? null}
                  permissions={me?.permissions ?? []}
                  onChanged={reloadGovernance}
                />
              </div>
            </details>
            <div className="mt-2">
              <ExpandLink onClick={() => setDeep("comms")}>
                Audiences and redlines
              </ExpandLink>
            </div>
          </Panel>
        ))}
      </div>

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
              onChanged={refreshAll}
              ledger={governance?.ledger ?? null}
              names={names}
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
            onChanged={refreshAll}
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
          <GuidancePanel guidance={guidance} />
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
              refreshAll();
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
