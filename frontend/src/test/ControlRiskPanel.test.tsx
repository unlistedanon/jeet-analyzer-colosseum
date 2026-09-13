import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ControlRiskPanel } from "../components/ControlRiskPanel";
import { definingFixture, fixtureWith, RELATED, SEED } from "./fixtures";

describe("linked wallet control risk", () => {
  it("surfaces one defensibly linked wallet even when common control is not established", () => {
    render(<ControlRiskPanel result={definingFixture} />);

    const linkedMetric = screen.getByText("Evidence-linked wallets").closest("article");
    expect(linkedMetric).toHaveTextContent("1");
    expect(screen.getByText(/One defensible on-chain relationship is enough/)).toBeInTheDocument();
    expect(screen.getByText("Linked wallets were found, but no pair-level control assessment was emitted.")).toBeInTheDocument();
    expect(screen.getByText(/does not erase the underlying relationship evidence/)).toBeInTheDocument();
    expect(screen.queryByText("LIKELY COMMON CONTROL")).not.toBeInTheDocument();
  });

  it("shows likely-common-control risk and visible related inventory when independent evidence stacks", () => {
    const result = fixtureWith({
      wallet_graph: {
        ...definingFixture.wallet_graph,
        control_assessments: [{
          wallet_a: SEED,
          wallet_b: RELATED,
          link_status: "STRONGLY_LINKED",
          control_assessment: "LIKELY_COMMON_CONTROL",
          common_control: "NOT_PROVEN",
          legal_identity: "NOT_ESTABLISHED",
          independent_evidence_classes: ["CO_SIGN", "TOKEN_FLOW"],
          supporting_signatures: ["sig-a", "sig-b"],
          distinct_transaction_count: 2,
          reason: "co-signing and direct token flow were observed across separate non-market transactions",
          risk_interpretation: "Treat LIKELY_COMMON_CONTROL as same-controller risk for exposure analysis, not proof of legal identity or beneficial ownership.",
        }],
      },
      control_assessments: [{
        wallet_a: SEED,
        wallet_b: RELATED,
        link_status: "STRONGLY_LINKED",
        control_assessment: "LIKELY_COMMON_CONTROL",
        common_control: "NOT_PROVEN",
        legal_identity: "NOT_ESTABLISHED",
        independent_evidence_classes: ["CO_SIGN", "TOKEN_FLOW"],
        supporting_signatures: ["sig-a", "sig-b"],
        distinct_transaction_count: 2,
        reason: "co-signing and direct token flow were observed across separate non-market transactions",
        risk_interpretation: "Treat LIKELY_COMMON_CONTROL as same-controller risk for exposure analysis, not proof of legal identity or beneficial ownership.",
      }],
    });

    render(<ControlRiskPanel result={result} />);

    expect(screen.getByText("PROOF: NOT PROVEN")).toBeInTheDocument();
    expect(screen.getByText("STRONGLY LINKED")).toBeInTheDocument();
    expect(screen.getByText("LIKELY COMMON CONTROL")).toBeInTheDocument();
    expect(screen.getByText("CO SIGN")).toBeInTheDocument();
    expect(screen.getByText("TOKEN FLOW")).toBeInTheDocument();
    expect(screen.getByText("2 TX")).toBeInTheDocument();

    const likelyPairs = screen.getByText("Likely common-control pairs").closest("article");
    expect(likelyPairs).toHaveTextContent("1");

    const likelyInventory = screen.getByText("Visible inventory on likely-control wallets").closest("article");
    expect(likelyInventory).toHaveTextContent("48,300,000");
    expect(likelyInventory).toHaveTextContent("1/1");
    expect(screen.getByText(/Legal identity and beneficial ownership remain unproven/)).toBeInTheDocument();
  });
});
