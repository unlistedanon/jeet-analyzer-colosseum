import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { InvestigationForm } from "../components/InvestigationForm";
import { InvestigationResults } from "../components/InvestigationResults";
import { SellerLeaderboard } from "../components/SellerLeaderboard";
import { definingFixture, fixtureWith, MINT, RELATED, SEED } from "./fixtures";

function verifiedWalletAuditFixture() {
  const reconstructedStart = 2_523_470_290_239;
  return fixtureWith({
    schema: "jeet-analyzer.wallet-ui.v1",
    historical_exit_status: "VERIFIED_OUT",
    current_wallet_status: "VERIFIED_OUT",
    wallet_status: "VERIFIED_OUT",
    cluster_status: null,
    current_inventory: { seed_target_token_balance_raw: 0 },
    seed_wallet_accounting: {
      wallet_status: "VERIFIED_OUT",
      current_target_token_balance_raw: 0,
      reconstructed_starting_inventory_raw: reconstructedStart,
      reconstructed_starting_target_inventory_raw: reconstructedStart,
      confirmed_sales: { count: 2, target_token_sold_raw: reconstructedStart, events: [{ token_amount_raw: reconstructedStart }] },
      inventory_increases: { count: 6, confirmed_buy_count: 6, reacquisition_count: 0, events: [] },
      lifecycle: {
        reconstructed_starting_inventory_raw: reconstructedStart,
        historical_exit_status: "VERIFIED_OUT",
        current_wallet_status: "VERIFIED_OUT",
        reacquisitions: { count: 0 },
      },
    },
    sales: { count: 2, target_token_sold_raw: reconstructedStart, events: [{ token_amount_raw: reconstructedStart }] },
    buys_reacquisitions: { count: 6, confirmed_buy_count: 6, reacquisition_count: 0, events: [] },
    wallet_graph: { nodes: [{ wallet: SEED }], edges: [], shared_funders: [] },
    wallet_relationships: [],
    shared_funders: [],
    related_target_token_inventory: [],
    excluded_infrastructure: [],
    provider_coverage: {
      complete: true,
      requests: [{ complete: true, scope: "synthetic complete wallet history and fresh current balances" }],
    },
  });
}

describe("forensic conclusion rendering", () => {
  it("keeps wallet VERIFIED_OUT separate from cluster NOT_OUT", () => {
    render(<InvestigationResults result={definingFixture} />);
    expect(screen.getByText("Wallet status")).toBeInTheDocument();
    expect(screen.getAllByText("VERIFIED OUT").length).toBeGreaterThan(0);
    expect(screen.getByText("Target cluster status")).toBeInTheDocument();
    expect(screen.getByText("NOT OUT")).toBeInTheDocument();
    expect(screen.getAllByText("NOT PROVEN").length).toBeGreaterThan(0);
    expect(screen.getByText(/Wallet out ≠ cluster out/)).toBeInTheDocument();
  });

  it("renders RE_ENTERED without converting it to a generic holding state", () => {
    render(<InvestigationResults result={fixtureWith({ historical_exit_status: "VERIFIED_OUT", current_wallet_status: "RE_ENTERED", wallet_status: "RE_ENTERED", cluster_status: null })} />);
    expect(screen.getAllByText("RE ENTERED").length).toBeGreaterThan(0);
    expect(screen.getAllByText("VERIFIED OUT").length).toBeGreaterThan(0);
  });

  it("renders NOT_OUT as its explicit lifecycle conclusion", () => {
    render(<InvestigationResults result={fixtureWith({ historical_exit_status: "NOT_OUT", current_wallet_status: "NOT_OUT", wallet_status: "NOT_OUT", cluster_status: null })} />);
    expect(screen.getAllByText("NOT OUT").length).toBeGreaterThan(0);
  });

  it("shows fail-closed status and budget exhaustion while preserving partial evidence", () => {
    const result = fixtureWith({
      historical_exit_status: "INSUFFICIENT_DATA",
      current_wallet_status: "INSUFFICIENT_DATA",
      wallet_status: "INSUFFICIENT_DATA",
      cluster_status: "INSUFFICIENT_DATA",
      provider_coverage: { complete: false, requests: [{ complete: false, limitation: "PROVIDER_BUDGET_EXHAUSTED", terminal_failure_reason: "transaction budget reached" }] },
      request_telemetry: { ...definingFixture.request_telemetry, terminated_by_budget: "max_transactions", termination_reason: "PROVIDER_BUDGET_EXHAUSTED" },
    });
    render(<InvestigationResults result={result} />);
    expect(screen.getAllByText("INSUFFICIENT DATA").length).toBeGreaterThan(0);
    expect(screen.getByText(/INCOMPLETE COVERAGE/)).toBeInTheDocument();
    expect(screen.getByText("INVESTIGATION INCOMPLETE")).toBeInTheDocument();
    expect(screen.getByText("max_transactions")).toBeInTheDocument();
    expect(screen.getAllByText(/PROVIDER_BUDGET_EXHAUSTED/).length).toBeGreaterThan(0);
  });

  it("preserves a verified seed wallet while explaining incomplete downstream cluster coverage", () => {
    const result = fixtureWith({
      historical_exit_status: "VERIFIED_OUT",
      current_wallet_status: "VERIFIED_OUT",
      wallet_status: "VERIFIED_OUT",
      cluster_status: "INSUFFICIENT_DATA",
      provider_coverage: {
        complete: false,
        seed_wallet_complete: true,
        downstream_complete: false,
        requests: [{ complete: true }, { complete: false, limitation: "PROVIDER_BUDGET_EXHAUSTED" }],
      },
      request_telemetry: { ...definingFixture.request_telemetry, terminated_by_budget: "max_transactions" },
    });
    render(<InvestigationResults result={result} />);
    expect(screen.getAllByText("VERIFIED OUT").length).toBeGreaterThan(0);
    expect(screen.getByText("INSUFFICIENT DATA")).toBeInTheDocument();
    expect(screen.getByText(/Seed wallet exit was established, but related-wallet coverage was incomplete/)).toBeInTheDocument();
  });

  it("does not render incomplete seed sale coverage as a proven zero", () => {
    const result = fixtureWith({
      sales: { count: 0, target_token_sold_raw: 0, amount_known: false, coverage_complete: false },
      seed_wallet_accounting: {
        ...definingFixture.seed_wallet_accounting,
        confirmed_sales: { count: 0, target_token_sold_raw: 0, amount_known: false, coverage_complete: false },
      },
    });
    render(<InvestigationResults result={result} />);
    expect(screen.getByText("UNKNOWN (0 observed)")).toBeInTheDocument();
  });

  it("renders reconstructed starting inventory and observed remaining from the wallet lifecycle", () => {
    render(<InvestigationResults result={verifiedWalletAuditFixture()} />);
    const reconstructed = screen.getByText("Reconstructed start").closest(".metric");
    const remaining = screen.getByText("Current inventory vs window start").closest(".metric");
    expect(reconstructed).toHaveTextContent("2,523,470.290239");
    expect(reconstructed).not.toHaveTextContent("UNKNOWN");
    expect(remaining).toHaveTextContent("0.00%");
  });

  it("labels intentionally withheld public-case identity and proceeds without implying failed resolution", () => {
    const result = fixtureWith({
      token: { decimals: 6 },
      mint: "[REDACTED TOKEN]",
      proceeds: {},
      seed_wallet_accounting: { ...definingFixture.seed_wallet_accounting, proceeds: {} },
      historical_exit_status: "VERIFIED_OUT",
      current_wallet_status: "RE_ENTERED",
      wallet_status: "RE_ENTERED",
      cluster_status: "NOT_OUT",
      provider_coverage: { complete: false, seed_wallet_complete: true, downstream_complete: false, requests: [] },
    });
    render(<InvestigationResults result={result} publicCase />);
    expect(screen.getByText("REDACTED FOR PUBLIC DEMO")).toBeInTheDocument();
    expect(screen.getAllByText("WITHHELD IN PUBLIC CASE").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText("NOT INCLUDED IN PUBLIC CASE")).toBeInTheDocument();
    expect(screen.getByText("Current inventory vs window start")).toBeInTheDocument();
    expect(screen.getByText("DOWNSTREAM RELATIONSHIP GRAPH: INCOMPLETE.")).toBeInTheDocument();
    expect(screen.getByText(/SEED WALLET LIFECYCLE: COMPLETE UNDER INDEXED-PROVIDER CONTRACT/)).toBeInTheDocument();
  });

  it("marks cluster scope as not evaluated and explains the wallet-audit graph empty state", () => {
    render(<InvestigationResults result={verifiedWalletAuditFixture()} />);
    expect(screen.getByText("NOT EVALUATED")).toBeInTheDocument();
    expect(screen.getByText(/Wallet Audit evaluates one wallet/)).toBeInTheDocument();
    expect(screen.getByText(/Relationship expansion was not performed for this Wallet Audit/)).toBeInTheDocument();
    expect(screen.getByText(/Run Cluster Audit to map evidence-backed related wallets/)).toBeInTheDocument();
  });

  it("explains history versus fresh balance coverage and buys versus reacquisitions", () => {
    render(<InvestigationResults result={verifiedWalletAuditFixture()} />);
    expect(screen.getByText(/reports whether the requested historical evidence scope completed/)).toBeInTheDocument();
    expect(screen.getByText(/current target-token inventory was directly observed for this run/)).toBeInTheDocument();
    expect(screen.getByText(/count all observed market buys in the requested window/)).toBeInTheDocument();
    expect(screen.getByText(/pre-exit buys are not reacquisitions/)).toBeInTheDocument();
    expect(screen.getByText("OBSERVED")).toBeInTheDocument();
  });
});

describe("seller and evidence views", () => {
  it("labels an incomplete seller table as partial coverage", () => {
    render(<SellerLeaderboard result={{
      schema: "jeet-analyzer.scan.v1",
      token: { mint: MINT, symbol: "TST", decimals: 6 },
      provider_coverage: { complete: false },
      sellers: [{ rank: 1, wallet: SEED, sells: 2, tokens_sold_raw: 2_000_000, status: "PARTIAL_EXIT" }],
    }} />);
    expect(screen.getByText("PARTIAL COVERAGE")).toBeInTheDocument();
    expect(screen.getByText(/based only on evidence retrieved/)).toBeInTheDocument();
  });

  it("explains a failed-closed empty leaderboard without claiming no sellers", () => {
    render(<SellerLeaderboard result={{ schema: "jeet-analyzer.scan.v1", status: "INSUFFICIENT_DATA", token: { decimals: 6 }, provider_coverage: { complete: false }, sellers: [] }} />);
    expect(screen.getByText("No complete leaderboard was emitted.")).toBeInTheDocument();
    expect(screen.getByText(/does not mean no sellers exist/)).toBeInTheDocument();
  });

  it("shows explicit direct funding direction and underlying evidence on edge selection", async () => {
    const user = userEvent.setup();
    render(<InvestigationResults result={definingFixture} />);
    await user.click(screen.getByRole("button", { name: /Inspect edge.*PLAIN_DIRECT_SOL_TRANSFER/ }));
    expect(screen.getAllByText("DIRECT SOL FUNDING").length).toBeGreaterThan(0);
    expect(screen.getByText("PLAIN_DIRECT_SOL_TRANSFER")).toBeInTheDocument();
    expect(screen.getByText("MULTI_RECIPIENT_FUNDER")).toBeInTheDocument();
    expect(screen.getByText("DIRECT_SIGNED_SOL_FUNDING")).toBeInTheDocument();
    expect(screen.getByText("2 SOL")).toBeInTheDocument();
    expect(screen.getAllByText("NOT_PROVEN").length).toBeGreaterThan(0);
    expect(screen.queryByText("SAME OWNER")).not.toBeInTheDocument();
    expect(screen.getAllByTitle(SEED).length).toBeGreaterThan(0);
    expect(screen.getAllByTitle(RELATED).length).toBeGreaterThan(0);
  });
});

describe("investigation controls", () => {
  it("keeps advanced limits collapsed and labels wallet input by mode", async () => {
    const user = userEvent.setup();
    render(<InvestigationForm busy={false} onSubmit={() => undefined} />);
    const advanced = screen.getByText("Advanced bounded controls").closest("details");
    expect(advanced).not.toHaveAttribute("open");
    expect(screen.getByText("Default bounded investigation limits")).toBeInTheDocument();
    expect(screen.getByLabelText(/Seller wallet optional/i)).not.toBeRequired();

    await user.click(screen.getByText("wallet audit"));
    expect(screen.getByLabelText(/Audited wallet required/i)).toBeRequired();

    await user.click(screen.getByText("cluster audit"));
    expect(screen.getByLabelText(/Seed wallet required/i)).toBeRequired();
    await user.click(screen.getByText("Advanced bounded controls"));
    expect(screen.getByLabelText("Days")).toHaveValue(14);
    await user.type(screen.getByPlaceholderText("Solana mint address"), MINT);
    await user.click(screen.getByRole("button", { name: "INVESTIGATE" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Cluster Audit requires a seed wallet.");
  });
});
