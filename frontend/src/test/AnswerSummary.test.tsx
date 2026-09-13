import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AnswerSummary } from "../components/AnswerSummary";
import { definingFixture, MINT, RELATED, SEED } from "./fixtures";

describe("plain-language answers", () => {
  it("separates sales, zero balance and SOL funding from target transfers", () => {
    render(<AnswerSummary result={definingFixture} />);
    expect(screen.getByText("Yes — sales observed")).toBeInTheDocument();
    expect(screen.getByText("Zero balance observed")).toBeInTheDocument();
    expect(screen.getByText("Not established")).toBeInTheDocument();
    expect(screen.queryByText("Yes — transfers observed")).not.toBeInTheDocument();
  });
  it("does not turn missing evidence into zero sales or holdings", () => {
    render(<AnswerSummary result={{ seed_wallet: SEED }} />);
    expect(screen.getAllByText("Not established")).toHaveLength(3);
    expect(screen.getByText("Coverage is incomplete or unconfirmed.")).toBeInTheDocument();
  });
  it("uses explicit complete sales scope for a negative finding", () => {
    render(<AnswerSummary result={{ seed_wallet: SEED, sales: { count: 0, coverage_complete: true, count_known: true } }} />);
    expect(screen.getByText("No confirmed sales in scope")).toBeInTheDocument();
  });
  it("shows direct target-token transfers and never equates recipient with owner", () => {
    render(<AnswerSummary result={{ ...definingFixture, wallet_relationships: [{ source: SEED, destination: RELATED, asset: MINT, amount_raw: 20, relationship_type: "TARGET_TOKEN_TRANSFER", signature: "receipt-signature" }] }} />);
    expect(screen.getByText("Yes — transfers observed")).toBeInTheDocument();
    expect(screen.getByText(/recipient is not proof of the same owner/)).toBeInTheDocument();
    expect(within(screen.getByLabelText("Your answer")).getByText(/receipt-signature/)).toBeInTheDocument();
  });
});
