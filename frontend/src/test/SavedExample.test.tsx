import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { SavedExample } from "../components/SavedExample";
import { definingFixture } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());

it("opens and closes a labeled saved report without submitting a scan", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(String(input).includes('/demo/') ? definingFixture : { stored: true }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<SavedExample />);
  await user.click(screen.getByRole("button", { name: "TRY AN EXAMPLE" }));
  expect(await screen.findByText("SAVED EXAMPLE — NOT A LIVE SCAN")).toBeInTheDocument();
  expect(screen.getByLabelText("Your answer")).toBeInTheDocument();
  expect(fetchMock.mock.calls.every(([url]) => !String(url).includes('/investigations'))).toBe(true);
  await user.click(screen.getByRole("button", { name: "CLOSE EXAMPLE" }));
  expect(screen.queryByLabelText("Your answer")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "TRY AN EXAMPLE" }));
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/demo/'))).toHaveLength(1);
});

it("offers a retry when the example cannot load", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response('{}', { status: 503 })));
  render(<SavedExample />);
  await userEvent.click(screen.getByRole("button", { name: "TRY AN EXAMPLE" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("could not load");
  expect(screen.getByRole("button", { name: "TRY AN EXAMPLE" })).toBeEnabled();
});
