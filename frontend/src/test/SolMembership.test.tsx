import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { SolMembership } from "../components/SolMembership";

vi.mock("qrcode", () => ({ toDataURL: vi.fn(async () => "data:image/png;base64,test") }));
afterEach(() => vi.unstubAllGlobals());

it("requires saved recovery, shows daily terms and QR, and checks without a pasted signature", async () => {
  const plan = { enabled: true, network: "mainnet-beta", days: 30, price_lamports: 500000000, credits: 250000, daily_credits: 250000, runs_daily: 0, membership: null };
  const invoice = { id: "test-invoice", amount_sol: "0.500000000", recipient: "test-recipient", reference: "test-reference", network: "mainnet-beta", days: 30, credits: 250000, daily_credits: 250000, runs_daily: 0, expires: Math.floor(Date.now()/1000)+86400, payment_url: "solana:test-recipient?amount=0.5&reference=test-reference", signature: null };
  let paid = false;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    let value;
    if (path.endsWith('/recovery-code')) value = { recovery_code: 'x'.repeat(43) };
    else if (path.endsWith('/verify')) { expect(JSON.parse(String(init?.body))).toEqual({}); paid = true; value = { ...invoice, signature: 'test-signature' }; }
    else if (path.endsWith('/invoices')) value = invoice;
    else value = { ...plan, membership: paid ? { ...plan, expires: Math.floor(Date.now()/1000)+2592000 } : null };
    return new Response(JSON.stringify(value), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  const onActivated = vi.fn(async () => {});
  render(<SolMembership onActivated={onActivated} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'VIEW MEMBERSHIP OPTIONS' }));
  expect(await screen.findByText(/250,000 estimated credits per day/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'CREATE SOL PAYMENT REQUEST' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'GENERATE RECOVERY CODE' }));
  await user.click(await screen.findByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: 'CREATE SOL PAYMENT REQUEST' }));
  expect(await screen.findByAltText(/Scan this Solana Pay QR/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'OPEN PAYMENT IN WALLET' })).toHaveAttribute('href', invoice.payment_url);
  await user.click(screen.getByRole('button', { name: 'CHECK FOR PAYMENT' }));
  expect(await screen.findByText(/Membership active until/)).toBeInTheDocument();
  expect(onActivated).toHaveBeenCalledTimes(1);
});

it("shows a remaining daily balance and no daily scan-count limit for members", async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ enabled: true, membership: { expires: 1900000000, daily_credits: 250000, credits: 250000, runs_daily: 0 }, daily_usage: { limit: 250000, committed: 75000, remaining: 175000, resets_at: '2026-09-09T00:00:00Z' } }))));
  render(<SolMembership onActivated={async () => {}} />);
  await userEvent.click(screen.getByRole('button', { name: 'VIEW MEMBERSHIP OPTIONS' }));
  expect(await screen.findByText('175,000 credits available today')).toBeInTheDocument();
  expect(screen.getByText(/No daily scan-count limit/)).toBeInTheDocument();
});
