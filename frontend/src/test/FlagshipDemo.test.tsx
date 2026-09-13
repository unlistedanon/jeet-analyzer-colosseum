import { cleanup, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FlagshipDemo } from "../components/FlagshipDemo";
import { ProgressPipeline } from "../components/ProgressPipeline";
import { Week1Video } from "../components/Week1Video";
import type { AnalyzerResult } from "../types";


describe("FlagshipDemo", () => {
  it("renders lifecycle, evidence honesty, cost, and offline-call facts from the result", () => {
    const result: AnalyzerResult = {
      token: { decimals: 6 },
      seed_wallet: "Wallet A",
      historical_exit_status: "VERIFIED_OUT",
      current_wallet_status: "RE_ENTERED",
      cluster_status: "NOT_OUT",
      common_control: "NOT_PROVEN",
      case_question: "The jeet sold, but is he really out?",
      provider_coverage: { complete: false, seed_wallet_complete: true, downstream_complete: false },
      demo_summary: {
        confirmed_sold_raw: 7_666_267_453_718,
        exit_sale_raw: 7_521_892_705_710,
        exit_balance_after_raw: 0,
        post_exit_reacquired_raw: 5_974_478_422_911,
        current_inventory_raw: 5_974_478_422_911,
        genuine_target_token_transfer_count: 0,
        secondary_wallet_target_increase_count: 0,
        estimated_original_provider_credits: 520,
        offline_replay_provider_calls: 0,
      },
    };
    render(<FlagshipDemo result={result} />);
    expect(screen.getByText("The jeet sold, but is he really out?")).toBeInTheDocument();
    const lifecycle = screen.getByLabelText("Wallet lifecycle");
    expect(within(lifecycle).getByText("7.52M")).toBeInTheDocument();
    expect(within(lifecycle).getByText("0")).toBeInTheDocument();
    expect(within(lifecycle).getByText("5.97M")).toBeInTheDocument();
    expect(within(lifecycle).getByText("Sold")).toBeInTheDocument();
    expect(within(lifecycle).getByText("Balance hit")).toBeInTheDocument();
    expect(within(lifecycle).getByText("Bought back")).toBeInTheDocument();
    const transition = screen.getByLabelText("Lifecycle status transition");
    expect(within(transition).getByText("VERIFIED_OUT")).toBeInTheDocument();
    expect(within(transition).getByText("RE_ENTERED")).toBeInTheDocument();
    expect(screen.getByText("5,974,478.422911")).toBeInTheDocument();
    expect(screen.getByText("NOT_OUT")).toBeInTheDocument();
    expect(screen.getByText("NOT_PROVEN")).toBeInTheDocument();
    expect(screen.getByText("COMPLETE under indexed-provider contract")).toBeInTheDocument();
    expect(screen.getByText("520 estimated credits")).toBeInTheDocument();
  });

  it("wraps the judge pipeline and renders a frozen alias-only video scene", () => {
    render(<ProgressPipeline wrap progress={{ phases: {}, events: [] }} />);
    expect(screen.getByRole("list")).toHaveClass("pipeline-list-wrap");
    expect(screen.getAllByRole("listitem")).toHaveLength(9);
    cleanup();
    render(<Week1Video capture={{ scene: 1, caption: 2 }} result={{
      token: { decimals: 6 },
      historical_exit_status: "VERIFIED_OUT",
      current_wallet_status: "RE_ENTERED",
      cluster_status: "NOT_OUT",
      common_control: "NOT_PROVEN",
      demo_summary: {
        exit_sale_raw: 7_521_892_705_710,
        exit_balance_after_raw: 0,
        post_exit_reacquired_raw: 5_974_478_422_911,
        current_inventory_raw: 5_974_478_422_911,
        estimated_original_provider_credits: 520,
        offline_replay_provider_calls: 0,
      },
    }} />);
    expect(screen.getByText("7.52M")).toBeInTheDocument();
    expect(screen.getByText("5.97M")).toBeInTheDocument();
    expect(screen.getByText("VERIFIED_OUT")).toBeInTheDocument();
    expect(screen.getByText("RE_ENTERED")).toBeInTheDocument();
    expect(screen.getByText(/Its balance actually hit zero/)).toBeInTheDocument();
  });
});
