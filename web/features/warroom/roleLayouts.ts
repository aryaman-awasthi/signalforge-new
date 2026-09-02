/**
 * S17 - the role views (S8-T09, REQ-012, REQ-045).
 *
 * A role view is not a different screen. It is the same cockpit with fewer
 * panels in it, and every panel that remains is exactly where it was. That is
 * deliberate and it is the whole design: somebody who learns where the bridge
 * is as an SME finds it in the same place the day they are made commander, and
 * a screenshot in a runbook stays true for everybody. A layout that rearranged
 * itself per role would be six products.
 *
 * So `ROLE_LAYOUTS` is a map from a role to the set of panels worth that
 * person's attention, and there is not one component in this file.
 *
 * Two rules it encodes, both about honesty rather than tidiness:
 *
 * 1. **Filtering here hides nothing the server would have refused.** Permissions
 *    are enforced by the API; this decides only what is worth attention. A panel
 *    a role could read but does not need is *absent*, not forbidden, and any
 *    role can open the full cockpit from the same URL without the `/as/` suffix.
 * 2. **`readOnly` removes controls, not information.** The Executive view has no
 *    buttons, because an executive pressing "run this action" during a P0 is a
 *    failure mode rather than a feature - but it shows the same evidence
 *    everybody else sees. A view that hid the evidence to justify hiding the
 *    button would be a different product.
 */

export type PanelKey =
  | "overview"
  | "bridge"
  | "chat"
  | "room"
  | "copilot"
  | "brain"
  | "action"
  | "timeline"
  | "health"
  | "radius"
  | "guidance"
  | "ledger"
  | "validation"
  | "plan"
  | "comms"
  | "paging"
  | "tasks";

export const ALL_PANELS: PanelKey[] = [
  "overview",
  "bridge",
  "chat",
  "room",
  "copilot",
  "brain",
  "action",
  "timeline",
  "health",
  "radius",
  "guidance",
  "ledger",
  "validation",
  "plan",
  "comms",
  "paging",
  "tasks",
];

export type RoleLayout = {
  /** What this view is for. Shown at the top of it, because a person who cannot
   *  see why a panel is missing assumes the product is broken. */
  purpose: string;
  panels: PanelKey[];
  /** Removes every control. The information stays. */
  readOnly?: boolean;
};

export const FULL: RoleLayout = {
  purpose: "Everything this incident has.",
  panels: ALL_PANELS,
};

export const ROLE_LAYOUTS: Record<string, RoleLayout> = {
  /* Runs the incident. Sees everything, because deciding what not to look at is
     part of the job and cannot be done for them. */
  incident_commander: FULL,
  org_owner: FULL,

  /* Owns the technical response: the same as the commander without the paging
     board, which is the commander's instrument rather than theirs. */
  tech_lead: {
    purpose:
      "The technical response — what is believed, what is proposed, and what has been attempted.",
    panels: [
      "overview",
      "bridge",
      "room",
      "copilot",
      "brain",
      "action",
      "timeline",
      "health",
      "radius",
      "guidance",
      "ledger",
      "validation",
      "plan",
      "tasks",
    ],
  },

  /* Answers one question well: their tasks, the evidence they are being asked
     about, and the bridge to answer on. Not the comms lifecycle, not approvals. */
  sme: {
    purpose:
      "What you have been asked to find out, the evidence around it, and the bridge to answer on.",
    panels: [
      "overview",
      "bridge",
      "room",
      "brain",
      "timeline",
      "health",
      "radius",
      "guidance",
      "ledger",
      "tasks",
    ],
  },

  /* Owns what leaves the building, and the approvals that gate it. */
  comms_lead: {
    purpose:
      "What has been said, what is drafted and who it goes to — and nothing sends itself.",
    panels: [
      "overview",
      "bridge",
      "room",
      "timeline",
      "radius",
      "validation",
      "comms",
      "paging",
    ],
  },

  /* Impact, ETA and stance. No controls at all. */
  executive: {
    purpose:
      "How bad, who is affected, and when it will be over. This view cannot change anything.",
    panels: ["overview", "room", "timeline", "health", "radius", "validation", "comms"],
    readOnly: true,
  },

  /* Reads the record. Everything decided and done, with the replay one click
     away in the header - which is the artefact they actually came for. */
  auditor: {
    purpose:
      "What was decided, what was done, and what justified it. Read only; the audit replay is in the header.",
    panels: [
      "overview",
      "room",
      "brain",
      "timeline",
      "radius",
      "ledger",
      "validation",
      "comms",
      "paging",
    ],
    readOnly: true,
  },
};

export function layoutFor(role: string | undefined | null): RoleLayout {
  if (!role) return FULL;
  return ROLE_LAYOUTS[role.toLowerCase()] ?? FULL;
}

/** The roles this product publishes a view for, in seniority order. */
export const ROLE_VIEWS = [
  "incident_commander",
  "tech_lead",
  "sme",
  "comms_lead",
  "executive",
  "auditor",
] as const;

export function roleLabel(role: string): string {
  return role.replace(/_/g, " ");
}
