import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BetaAdmin } from "../components/BetaAdmin";
import { BetaExperience } from "../components/BetaExperience";
import type { BetaInvestigationView } from "../types";
import { definingFixture, MINT, SEED } from "./fixtures";

function json(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
}

const authenticatedSession = {
  authenticated: true,
  beta_enabled: true,
  beta_configured: true,
  code_id: "tester-01",
  limits: { runs_per_code_per_day: 3, estimated_credits_per_run: 650 },
};

function investigation(overrides: Partial<BetaInvestigationView> = {}): BetaInvestigationView {
  return {
    investigation_id: "beta-investigation-12345678",
    status: "complete",
    stage: "COMPLETE",
    created_at: "2027-01-15T12:00:00Z",
    updated_at: "2027-01-15T12:01:00Z",
    request: { mint: MINT, wallet: SEED, days: 14, max_pages: 2, max_rpc_requests: 100, max_signatures: 1000, max_transactions: 600, request_timeout: 45, provider_retries: 1 },
    progress: { stage: "COMPLETE", events: [{ stage: "VALIDATING", state: "complete", timestamp: "2027-01-15T12:00:00Z", message: "Invite and budgets validated." }, { stage: "COMPLETE", state: "complete", timestamp: "2027-01-15T12:01:00Z", message: "Coverage limitations remain part of the result." }] },
    result: definingFixture,
    safe_report_available: true,
    estimated_provider_credits_reserved: 650,
    estimated_provider_credits_used: 650,
    credit_cost_is_estimated: true,
    deduplicated: false,
    ...overrides,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("invite-only beta experience", () => {
  it("opens public investigations without requesting an access code", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input) === "/api/beta/session") return json({ ...authenticatedSession, public_access: true, code_id: "visitor-example" });
      throw new Error(`unexpected fetch ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<BetaExperience />);
    expect(await screen.findByLabelText("Token mint")).toBeInTheDocument();
    expect(screen.queryByLabelText("Access code")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Leave beta" })).not.toBeInTheDocument();
  });

  it("gates access and authenticates the invite through the server", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/beta/session" && !init?.method) return json({ authenticated: false, beta_enabled: true, beta_configured: true });
      if (url === "/api/beta/session" && init?.method === "POST") return json(authenticatedSession);
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<BetaExperience />);
    expect(await screen.findByText("Enter access code")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Access code"), "jeet-beta-test-code-0001");
    await user.click(screen.getByRole("button", { name: "ENTER BETA" }));
    expect(await screen.findByText("Trace the token trail")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/beta/session", expect.objectContaining({ method: "POST", credentials: "same-origin" }));
  });

  it("submits only mint and wallet, shows real stages, and renders the forensic answer", async () => {
    const complete = investigation();
    const queued = investigation({ status: "queued", stage: "QUEUED", result: null, safe_report_available: false });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/beta/session") return json(authenticatedSession);
      if (url === "/api/beta/investigations" && init?.method === "POST") return json(queued, 202);
      if (url.includes("/api/beta/investigations/beta-investigation")) return json(complete);
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<BetaExperience />);
    await user.type(await screen.findByLabelText("Token mint"), MINT);
    await user.type(screen.getByLabelText("Wallet address"), SEED);
    await user.click(screen.getByRole("button", { name: "INVESTIGATE" }));
    expect(await screen.findByRole("heading", { name: "COMPLETE" })).toBeInTheDocument();
    expect(await screen.findByText("Wallet status")).toBeInTheDocument();
    expect(screen.getByText("COMMON CONTROL requires proof.")).toBeInTheDocument();
    const submit = fetchMock.mock.calls.find((call) => String(call[0]) === "/api/beta/investigations");
    expect(JSON.parse(String(submit?.[1]?.body))).toEqual({ mint: MINT, wallet: SEED });
  });

  it("restores the latest durable result after a browser refresh", async () => {
    const complete = investigation();
    window.localStorage.setItem("jeet_beta_last_investigation", complete.investigation_id);
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/beta/session") return json(authenticatedSession);
      if (url.endsWith(`/api/beta/investigations/${complete.investigation_id}`)) return json(complete);
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<BetaExperience />);
    expect(await screen.findByText("FINAL RESULT AVAILABLE")).toBeInTheDocument();
    expect(screen.getByText("Wallet status")).toBeInTheDocument();
  });

  it("distinguishes incomplete coverage and provider-budget exhaustion from a system error", async () => {
    const incomplete = investigation({
      termination_reason: "PROVIDER_BUDGET_EXHAUSTED",
      result: {
        ...definingFixture,
        provider_coverage: { complete: false, seed_wallet_complete: true, downstream_complete: false, requests: [] },
        cluster_status: "INSUFFICIENT_DATA",
        common_control: "NOT_PROVEN",
      },
    });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => String(input) === "/api/beta/session" ? json(authenticatedSession) : json(incomplete)));
    render(<BetaExperience />);
    expect(await screen.findByText("Trace the token trail")).toBeInTheDocument();
    // Render a stored result by driving the standard form.
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Token mint"), MINT);
    await user.type(screen.getByLabelText("Wallet address"), SEED);
    await user.click(screen.getByRole("button", { name: "INVESTIGATE" }));
    expect(await screen.findByText("INVESTIGATION LIMIT REACHED")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Paid membership" })).not.toBeInTheDocument();
    expect(screen.getByText(/Seed wallet exit was established/)).toBeInTheDocument();
    expect(screen.queryByText("SYSTEM ERROR")).not.toBeInTheDocument();
    expect(screen.getAllByText("NOT PROVEN").length).toBeGreaterThan(0);
  });

  it("offers membership interest only for a genuine credit stop and preserves the result", async () => {
    const stopped = investigation({ termination_reason: "PROVIDER_BUDGET_EXHAUSTED budget=max_estimated_provider_credits limit=650" });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/beta/session") return json(authenticatedSession);
      if (url.endsWith("/feedback")) return json({ stored: true }, 201);
      return json(stopped);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<BetaExperience />);
    await user.type(await screen.findByLabelText("Token mint"), MINT);
    await user.type(screen.getByLabelText("Wallet address"), SEED);
    await user.click(screen.getByRole("button", { name: "INVESTIGATE" }));
    expect(await screen.findByText("INVESTIGATION CREDITS USED UP")).toBeInTheDocument();
    expect(screen.getByText("Wallet status")).toBeInTheDocument();
    expect(screen.getByText(/Check membership options above for SOL checkout availability/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "I’M INTERESTED IN PAID MEMBERSHIP" }));
    expect(await screen.findByText(/Membership interest saved/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/feedback"), expect.objectContaining({
      method: "POST", body: JSON.stringify({ useful: null, comment: "[PAID_MEMBERSHIP_INTEREST] Interested in a larger investigation credit allowance." }),
    }));
    expect(screen.queryByRole("button", { name: "I’M INTERESTED IN PAID MEMBERSHIP" })).not.toBeInTheDocument();
  });

  it("does not sell a credit upgrade for the transaction cap that stopped the real beta case", async () => {
    const stopped = investigation({ termination_reason: "PROVIDER_BUDGET_EXHAUSTED budget=max_transactions limit=20000", estimated_provider_credits_used: 20898, estimated_provider_credits_reserved: 60000 });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => String(input) === "/api/beta/session" ? json(authenticatedSession) : json(stopped)));
    const user = userEvent.setup();
    render(<BetaExperience />);
    await user.type(await screen.findByLabelText("Token mint"), MINT);
    await user.type(screen.getByLabelText("Wallet address"), SEED);
    await user.click(screen.getByRole("button", { name: "INVESTIGATE" }));
    expect(await screen.findByText("INVESTIGATION LIMIT REACHED")).toBeInTheDocument();
    expect(screen.queryByText("INVESTIGATION CREDITS USED UP")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Paid membership" })).not.toBeInTheDocument();
  });

  it("shows safe provider failures without exposing a forensic conclusion", async () => {
    const failed = investigation({ status: "failed", stage: "FAILED", result: null, safe_report_available: false, error: "Provider timed out safely. No conclusion was strengthened.", termination_reason: "ENGINE_EXECUTION_FAILED" });
    let submitted = false;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/beta/session") return json(authenticatedSession);
      if (url === "/api/beta/investigations") { submitted = true; return json(failed, 202); }
      if (submitted) return json(failed);
      throw new Error("unexpected request");
    }));
    const user = userEvent.setup();
    render(<BetaExperience />);
    await user.type(await screen.findByLabelText("Token mint"), MINT);
    await user.type(screen.getByLabelText("Wallet address"), SEED);
    await user.click(screen.getByRole("button", { name: "INVESTIGATE" }));
    expect(await screen.findByText("SYSTEM ERROR")).toBeInTheDocument();
    expect(screen.getByText(/No conclusion was strengthened/)).toBeInTheDocument();
    expect(screen.queryByText("Wallet status")).not.toBeInTheDocument();
  });

  it("copies only the explicit safe report and records completed-run feedback", async () => {
    const complete = investigation();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/beta/session") return json(authenticatedSession);
      if (url === "/api/beta/investigations") return json(complete, 202);
      if (url.endsWith("/share")) return json({ schema: "jeet-analyzer.public-case.v1", seed_wallet: "Wallet A", common_control: "NOT_PROVEN" });
      if (url.endsWith("/feedback") && init?.method === "POST") return json({ feedback_id: "feedback-1", stored: true }, 201);
      return json(complete);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const clipboardSpy = vi.spyOn(navigator.clipboard, "writeText");
    render(<BetaExperience />);
    await user.type(await screen.findByLabelText("Token mint"), MINT);
    await user.type(screen.getByLabelText("Wallet address"), SEED);
    await user.click(screen.getByRole("button", { name: "INVESTIGATE" }));
    await user.click(await screen.findByRole("button", { name: /COPY SAFE REPORT/ }));
    expect(await screen.findByText("Public-safe report copied.")).toBeInTheDocument();
    const copied = String(clipboardSpy.mock.calls[0]?.[0]);
    expect(copied).toContain("Wallet A");
    expect(copied).not.toContain(SEED);
    await user.click(screen.getByRole("button", { name: "YES" }));
    await user.type(screen.getByLabelText("What did Jeet miss?"), "Nothing obvious.");
    await user.click(screen.getByRole("button", { name: "SEND FEEDBACK" }));
    expect(await screen.findByText(/Feedback recorded/)).toBeInTheDocument();
    const feedback = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/feedback"));
    expect(JSON.parse(String(feedback?.[1]?.body))).toEqual({ useful: true, comment: "Nothing obvious." });
  });

  it("stores feedback and keeps admin metrics behind a separate credential", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/beta/admin/session" && !init?.method) return json({ authenticated: false });
      if (url === "/api/beta/admin/session" && init?.method === "POST") return json({ authenticated: true });
      if (url === "/api/beta/admin/metrics") return json({ day: "2027-01-15", investigations: 1, running: 0, queued: 0, complete: 1, failed: 0, estimated_credits: 650, budget_exhausted: 0, average_duration_seconds: 42, feedback_count: 1, by_code: [{ code_id: "tester-01", investigations: 1, estimated_credits: 650 }], recent_feedback: [], estimated_credit_note: "Conservative estimates." });
      throw new Error(`unexpected fetch ${url}`);
    }));
    const user = userEvent.setup();
    render(<BetaAdmin />);
    expect(await screen.findByText("JEET BETA ADMIN")).toBeInTheDocument();
    expect(screen.queryByText("Investigations today")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Admin credential"), "jeet-beta-admin-code-0001");
    await user.click(screen.getByRole("button", { name: "OPEN ADMIN" }));
    expect(await screen.findByText("Investigations today")).toBeInTheDocument();
    expect(screen.getAllByText("Estimated credits")).toHaveLength(2);
  });

  it("clearly exposes the server kill-switch state", async () => {
    vi.stubGlobal("fetch", vi.fn(() => json({ ...authenticatedSession, beta_enabled: false })));
    render(<BetaExperience />);
    expect(await screen.findByText("INVESTIGATIONS PAUSED")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "INVESTIGATE" })).toBeDisabled();
  });
});
