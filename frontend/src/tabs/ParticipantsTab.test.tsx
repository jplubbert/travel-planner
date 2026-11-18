import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createApi } from "../api";
import { withTrip } from "../test/fixtures";
import type { Member } from "../types";
import { ParticipantsTab } from "./ParticipantsTab";

const MEMBERS: Member[] = [
  { userId: "ana", displayName: "Ana", role: "organizer", addedAt: "2030-01-01T00:00:00Z" },
  { userId: "beto", displayName: "Beto", role: "editor", addedAt: "2030-01-01T00:00:00Z" },
  { userId: "carla", displayName: "Carla", role: "viewer", addedAt: "2030-01-01T00:00:00Z" },
];

const api = createApi("ana", ((url: string) => {
  const body = url.endsWith("/members") ? MEMBERS : [{ id: "dani", displayName: "Dani" }];
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
}) as typeof fetch);

const optionsOf = (select: HTMLElement) =>
  within(select).getAllByRole("option").map((o) => [o.textContent, (o as HTMLOptionElement).value]);

describe("ParticipantsTab roles", () => {
  it("labels roles in Spanish in the selects while sending the API values", async () => {
    render(withTrip(<ParticipantsTab />, "organizer", { api }));
    expect(optionsOf(screen.getByLabelText("rol nuevo"))).toEqual([["editor", "editor"], ["lector", "viewer"]]);
    const carla = await screen.findByLabelText("rol de carla") as HTMLSelectElement;
    expect(carla.value).toBe("viewer");
    expect(carla.selectedOptions[0].textContent).toBe("lector");
    expect(screen.getByRole("cell", { name: "organizador" })).toBeInTheDocument();
  });

  it("shows read-only roles in Spanish to viewers", async () => {
    render(withTrip(<ParticipantsTab />, "viewer", { api }));
    expect(await screen.findByRole("cell", { name: "lector" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "organizador" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByText("viewer")).toBeNull();
  });
});
