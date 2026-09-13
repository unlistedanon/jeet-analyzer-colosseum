import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { WillTheyJeet } from "../components/WillTheyJeet";

const state = { player: { points: 100 }, active_round: { mint: "test-mint", creator: "test-creator", completed_at: 1, completion_signature: "test-signature", status: "ACTIVE" }, prediction: null };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

it("announces the current bonded coin and the last ended coin separately", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...state, last_ended_round: { mint: "previous-mint", jeet_at: 10, jeet_signature: "sell-receipt" } }) }));
  render(<WillTheyJeet />);
  expect(await screen.findByText("COIN BONDED — ROUND STARTED")).toBeInTheDocument();
  expect(screen.getByText("FOUNDER JEETED — ROUND OVER")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "View sell transaction" })).toHaveAttribute("href", "https://solscan.io/tx/sell-receipt");
});

it("explains the live paper contest instead of the retired quiz", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => state }));
  render(<WillTheyJeet />);
  expect(await screen.findByRole("heading", { name: "THE RULES" })).toBeInTheDocument();
  expect(screen.getByText(/A Pump.fun coin reaches bonding completion/)).toBeInTheDocument();
  expect(screen.getByText(/paper points only/)).toBeInTheDocument();
  expect(screen.queryByText("Read the clue. Pick the exit. Find out if the bag really left the building.")).not.toBeInTheDocument();
});

it("submits a timing pick without an old quiz answer and confirms the saved balance", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => state });
  let finish!: (value: unknown) => void;
  fetcher.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  vi.stubGlobal("fetch", fetcher);
  render(<WillTheyJeet />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /UNDER 5 MIN/ }));
  await user.click(screen.getByRole("button", { name: /LOCK THE PICK/ }));
  expect(screen.getByRole("button", { name: /SAVING PICK/ })).toBeDisabled();
  expect(fetcher).toHaveBeenLastCalledWith("/api/game/predict", expect.objectContaining({ method: "POST", body: JSON.stringify({ mint: "test-mint", bucket: "under-5m" }) }));
  finish({ ok: true, json: async () => ({ ok: true, player: { points: 75 }, state: { ...state, prediction: { bucket: "under-5m", result: null, delta: null } } }) });
  expect(await screen.findByRole("status")).toHaveTextContent("Balance: 75 points");
});

it("shows server rejection without claiming the pick saved", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce({ ok: true, json: async () => state }).mockResolvedValueOnce({ ok: false, json: async () => ({ detail: "round is not active" }) }));
  render(<WillTheyJeet />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /UNDER 5 MIN/ }));
  await user.click(screen.getByRole("button", { name: /LOCK THE PICK/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("round is not active");
  expect(screen.queryByText("PREDICTION LOCKED")).not.toBeInTheDocument();
});

it("restores a saved pick after reload", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...state, player: { points: 75 }, prediction: { bucket: "1-6h", result: null, delta: null } }) }));
  render(<WillTheyJeet />);
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved: 1-6h"));
  expect(screen.queryByRole("button", { name: /LOCK THE PICK/ })).not.toBeInTheDocument();
});
