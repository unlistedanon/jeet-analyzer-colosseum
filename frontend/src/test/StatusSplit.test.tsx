import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InvestigationResults } from "../components/InvestigationResults";
import { definingFixture, fixtureWith } from "./fixtures";

describe("target versus relationship status split", () => {
  it("shows target inventory truth separately from unresolved linkage", () => {
    const result = fixtureWith({
      historical_exit_status: "VERIFIED_OUT",
      current_wallet_status: "VERIFIED_OUT",
      wallet_status: "VERIFIED_OUT",
      target_cluster_status: "VERIFIED_OUT",
      relationship_status: "UNRESOLVED",
      cluster_status: "UNRESOLVED",
      current_inventory: {
        ...definingFixture.current_inventory,
        visible_cluster_target_inventory_raw: 0,
        visible_related_target_inventory_raw: 0,
      },
      provider_coverage: { complete: true, seed_wallet_complete: true, downstream_complete: true, requests: [] },
    });
    render(<InvestigationResults result={result} />);
    expect(screen.getByText("Target cluster status")).toBeInTheDocument();
    expect(screen.getByText("Relationship status")).toBeInTheDocument();
    expect(screen.getAllByText("VERIFIED OUT").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("UNRESOLVED")).toBeInTheDocument();
    expect(screen.getAllByText("NOT PROVEN").length).toBeGreaterThan(0);
  });
});
