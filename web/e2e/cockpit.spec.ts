import { expect, test, type Page } from "@playwright/test";

/**
 * The Sprint 4 acceptance journey, in a real browser.
 *
 * Sprint 4 drove every clause of its acceptance over HTTP and shipped with the
 * qualification that "nobody clicked". That qualification is what this file
 * removes, and it earned its keep on the first run: the walkthrough found that
 * the incident commander - the only role allowed to declare - could not open
 * the screen the Declare button is on, because every read behind it demanded
 * `manage_apps`, which the frozen roles matrix gives to the org owner alone. No
 * combination of API tests would have caught it, because each half worked.
 *
 * Deliberately one test rather than nine. The journey *is* the assertion: a
 * declaration that works only when a proposal exists, a page that only exists
 * because a declaration opened a room, an acknowledgement that only exists
 * because somebody was paged. Splitting it into independent tests would mean
 * either nine setups of a live incident or nine tests pretending to be
 * independent while sharing one.
 *
 * It reads the live stack rather than mocking it, and skips when there is not
 * one - the same discipline the reference-harness suite uses for the jar.
 */

const WEB = process.env.SF_WEB_URL ?? "http://127.0.0.1:3000";
const ORG = process.env.SF_E2E_ORG ?? "drill-co";
const PASSWORD = process.env.SF_E2E_PASSWORD ?? "drill-strong-password";
const APP_ID = process.env.SF_E2E_APP ?? "payments";
const IC = process.env.SF_E2E_IC ?? "ic@drill.test";
const SME = process.env.SF_E2E_SME ?? "sme1@drill.test";

async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByRole("textbox", { name: "Organisation slug" }).fill(ORG);
  await page.getByRole("textbox", { name: "Email" }).fill(email);
  await page.getByRole("textbox", { name: "Password" }).fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  // Wait for the session, not for the network to go quiet: the form posts to
  // the cookie proxy and only then navigates, and a `networkidle` here returns
  // before the cookie exists - which then fails one screen later, somewhere
  // that has nothing to do with signing in.
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  await expect
    .poll(async () =>
      (await page.context().cookies()).some((cookie) => cookie.name === "sf_access"),
    )
    .toBe(true);
}

test.beforeAll(async () => {
  let reachable = false;
  try {
    const response = await fetch(`${WEB}/login`);
    reachable = response.ok;
  } catch {
    reachable = false;
  }
  test.skip(
    !reachable,
    `no cockpit at ${WEB}. Run: bash run.sh, then ` +
      "python scripts/drill_run.py drills/s4-acceptance.yaml",
  );
});

test("an IC declares a corroborated P0 and the SME it pages acknowledges", async ({
  page,
  context,
}) => {
  // --- the incident commander -------------------------------------------
  await signIn(page, IC);

  // The screen the Declare button is on must open for the person who declares.
  await page.goto(`/apps/${APP_ID}`);
  await expect(page).toHaveURL(new RegExp(`/apps/${APP_ID}$`));
  const main = page.locator("main");
  await expect(main).toContainText("a detection stance");

  // The proposal sheet, with its corroboration checklist. The severity is read
  // off the page rather than assumed, so this test does not require the drill
  // to have reached any particular one - only that whatever it proposes is
  // shown with the evidence behind it and is not a declaration.
  const proposal = page.getByRole("region", { name: "Severity proposal" });
  await expect(proposal).toBeVisible();
  await expect(proposal).toContainText("Proposed, not declared");
  await expect(proposal).toContainText("Two or more independent signal families");
  await expect(proposal).toContainText("At least one is customer-facing");

  // The change-window chip, lit by a real deploy-hook call over the ingest path.
  await expect(main).toContainText("Change window open");

  // Declare. One click, and the record says a person made it.
  const declare = page.getByRole("button", { name: /^Declare P\d as proposed$/ });
  await expect(declare).toBeEnabled();
  await declare.click();

  // --- the war room opened itself ---------------------------------------
  await page.waitForURL(/\/incidents\/[0-9a-f-]{36}$/);
  const incidentId = page.url().split("/").pop()!;
  await expect(page.getByRole("heading", { level: 1 })).toContainText("INC-");
  for (const panel of [
    "Incident Overview",
    "Live Bridge Transcript",
    "Team Chat",
    "AI Incident Brain",
    "Next Best Action",
    "AI Copilot",
    "Incident Timeline",
    "Warning and Guidance",
    "Health Score and Metrics",
    "Affected Services",
    "Who is in the room",
  ]) {
    await expect(page.getByRole("region", { name: panel })).toBeVisible();
  }
  // Paging, with the server's own countdown on every row.
  const board = page.getByRole("region", { name: "Acknowledgement board" });
  await expect(board).toBeVisible();
  await expect(board).toContainText(/\d:\d\d/);

  // The socket, not the poll. The badge is the only place the cockpit says
  // which mode it is in, and a responder who thinks a screen is live when it is
  // not is the failure this badge exists to prevent.
  await expect(page.getByTestId("live-badge")).toHaveAttribute("data-live", "true");

  // --- a second session, a different person ------------------------------
  const second = await context.browser()!.newContext();
  const sme = await second.newPage();
  try {
    await signIn(sme, SME);

    // The page reached them, on a route that has no recipient parameter.
    await sme.goto("/my/pages");
    const mine = sme.locator("main");
    await expect(mine).toContainText("Your pages");
    await expect(sme.getByRole("button", { name: "Acknowledge" }).first()).toBeVisible();

    // They cannot answer anybody else's, and the refusal is the server's - the
    // control is the entity refusing, not the button being absent.
    const refusal = await sme.evaluate(async (id: string) => {
      const room = await (await fetch(`/api/proxy/incidents/${id}/war-room`)).json();
      const me = await (await fetch("/api/proxy/auth/me")).json();
      const someoneElse = room.pages.find(
        (p: { recipient: string }) => p.recipient !== me.user_id,
      );
      const response = await fetch(
        `/api/proxy/incidents/${id}/pages/${someoneElse.id}/ack`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
      );
      return { status: response.status, permissions: me.permissions };
    }, incidentId);
    expect(refusal.status).toBe(403);
    expect(refusal.permissions).toEqual([]);

    // Their own, they may - holding no permission at all.
    await sme.getByRole("button", { name: "Acknowledge" }).first().click();
    await expect(mine).toContainText("Nothing is waiting for you.");
  } finally {
    await second.close();
  }
});
