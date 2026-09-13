import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BetaOperatorCases } from "../components/BetaOperatorCases";
import { definingFixture, MINT, SEED } from "./fixtures";

function json(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
}

const identifier = "beta-investigation-12345678";
const request = { mint: MINT, wallet: SEED, days: 30, max_pages: 200, max_rpc_requests: 1500, max_signatures: 40000, max_transactions: 20000, request_timeout: 45, provider_retries: 1 };

const summary = {
  investigation_id: identifier,
  code_id: "beta-3",
  status: "complete",
  stage: "COMPLETE",
  created_at: "2026-09-05T18:00:00Z",
  updated_at: "2026-09-05T18:01:00Z",
  completed_at: "2026-09-05T18:01:00Z",
  request,
  error: null,
  termination_reason: null,
  estimated_provider_credits_reserved: 60000,
  estimated_provider_credits_used: 41280,
  historical_exit_status: "VERIFIED_OUT",
  current_wallet_status: "RE_ENTERED",
  target_cluster_status: "NOT_OUT",
  relationship_status: "RESOLVED_WITHIN_SCOPE",
  cluster_status: "NOT_OUT",
  common_control: "NOT_PROVEN",
  provider_complete: true,
  relationship_count: 4,
};

const detail = {
  ...summary,
  started_at: "2026-09-05T18:00:01Z",
  progress: { stage: "COMPLETE", events: [{ stage: "COMPLETE", state: "complete", timestamp: "2026-09-05T18:01:00Z", message: "Investigation complete." }] },
  result: definingFixture,
  safe_result: { schema: "jeet-analyzer.public-case.v1" },
  safe_report_available: true,
  artifact_names: ["receipt_json"],
  feedback: [{ id: "feedback-1", created_at: "2026-09-05T18:02:00Z", useful: 1, comment: "Caught the linked inventory." }],
};

afterEach(() => vi.unstubAllGlobals());

describe("beta operator case browser", () => {
  it("lists tester inputs and drills into the exact stored result", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/beta/admin/investigations?limit=100") return json({ investigations: [summary] });
      if (url === `/api/beta/admin/investigations/${identifier}`) return json(detail);
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<BetaOperatorCases />);

    expect(await screen.findByRole("heading", { name: "Tester investigations" })).toBeInTheDocument();
    expect(screen.getByText("beta-3")).toBeInTheDocument();
    expect(screen.getByText("41,280")).toBeInTheDocument();
    expect(screen.getByText("NOT_OUT")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /beta-i/i }));

    expect(await screen.findByText("Mint submitted")).toBeInTheDocument();
    expect(screen.getByText(MINT)).toBeInTheDocument();
    expect(screen.getByText(SEED)).toBeInTheDocument();
    expect(screen.getByText("Caught the linked inventory.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Forensic result returned by Jeet" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(`/api/beta/admin/investigations/${identifier}`, expect.objectContaining({ credentials: "same-origin", cache: "no-store" }));
  });
});
