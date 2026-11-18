import { useState } from "react";

import { ErrorBox, Field } from "../components";
import { ASSIGNABLE_ROLES, dateTime, ROLE_LABEL } from "../format";
import { useAction, useLoad, useTrip } from "../TripView";
import type { Member, Role, TripInput, User } from "../types";

export function ParticipantsTab() {
  const { api, trip, allows, ticks } = useTrip();
  const members = useLoad(() => api.get<Member[]>(`/trips/${trip.id}/members`), [trip.id, ticks.trip]);
  const users = useLoad(() => api.get<User[]>("/users"), [api]);
  const action = useAction();
  const [newUser, setNewUser] = useState("");
  const [newRole, setNewRole] = useState<Role>("viewer");

  const memberIds = new Set((members.data ?? []).map((m) => m.userId));
  const candidates = (users.data ?? []).filter((u) => !memberIds.has(u.id));

  async function setRole(userId: string, role: Role) {
    if (await action.run(() => api.put(`/trips/${trip.id}/members/${userId}`, { role })) !== undefined) {
      members.reload();
      setNewUser("");
    }
  }

  async function remove(userId: string) {
    await action.run(() => api.del(`/trips/${trip.id}/members/${userId}`));
    members.reload();
  }

  return (
    <div>
      <ErrorBox error={members.error} />
      <ErrorBox error={action.error} onClose={action.clear} />
      <table>
        <thead>
          <tr><th>User</th><th>Role</th><th>Since</th>{allows("organizer") && <th />}</tr>
        </thead>
        <tbody>
          {(members.data ?? []).map((m) => (
            <tr key={m.userId}>
              <td>{m.displayName} <span className="muted">({m.userId})</span></td>
              <td>
                {allows("organizer") && m.role !== "organizer" ? (
                  <select aria-label={`role of ${m.userId}`} value={m.role} disabled={action.busy}
                    onChange={(e) => void setRole(m.userId, e.target.value as Role)}>
                    {ASSIGNABLE_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                  </select>
                ) : ROLE_LABEL[m.role]}
              </td>
              <td>{dateTime(m.addedAt)}</td>
              {allows("organizer") && (
                <td>
                  {m.role !== "organizer" && (
                    <button className="secondary" disabled={action.busy}
                      onClick={() => void remove(m.userId)}>Remove</button>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {allows("organizer") && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (newUser) void setRole(newUser, newRole); }}>
          <Field label="Add participant">
            <select aria-label="new participant" value={newUser} onChange={(e) => setNewUser(e.target.value)}>
              <option value="">— choose —</option>
              {candidates.map((u) => <option key={u.id} value={u.id}>{u.displayName} ({u.id})</option>)}
            </select>
          </Field>
          <Field label="Role">
            <select aria-label="new role" value={newRole} onChange={(e) => setNewRole(e.target.value as Role)}>
              {ASSIGNABLE_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
            </select>
          </Field>
          <button type="submit" disabled={!newUser || action.busy}>Add</button>
        </form>
      )}
      {allows("editor") && <EditTrip />}
    </div>
  );
}

/** Trip details (PATCH, versioned): a stale version gets the 409 conflict panel. */
function EditTrip() {
  const { api, trip, versioned } = useTrip();
  const [form, setForm] = useState<TripInput | null>(null);
  if (!form) {
    return (
      <p>
        <button className="secondary" onClick={() => setForm({
          name: trip.name, destinations: trip.destinations, startsOn: trip.startsOn,
          endsOn: trip.endsOn, travelers: trip.travelers, budget: trip.budget.amount,
        })}>Edit trip details</button>
      </p>
    );
  }
  const loadedVersion = trip.version;
  return (
    <form className="card" onSubmit={async (e) => {
      e.preventDefault();
      const body = { ...form };
      const done = await versioned("edit trip details",
        (version) => api.patch(`/trips/${trip.id}`, { ...body, version }));
      if (done) setForm(null);
    }}>
      <h3>Edit trip <span className="muted">(on version {loadedVersion})</span></h3>
      <TripFields value={form} onChange={setForm} />
      <div className="row">
        <button type="submit">Save</button>
        <button type="button" className="secondary" onClick={() => setForm(null)}>Cancel</button>
      </div>
    </form>
  );
}

export function TripFields({ value, onChange }: { value: TripInput; onChange: (v: TripInput) => void }) {
  // Kept as typed text so commas and spaces can be entered; parsed on every change.
  const [destinations, setDestinations] = useState(value.destinations.join(", "));
  return (
    <div className="grid">
      <Field label="Name">
        <input value={value.name} onChange={(e) => onChange({ ...value, name: e.target.value })} />
      </Field>
      <Field label="Destinations (comma-separated)">
        <input value={destinations} onChange={(e) => {
          setDestinations(e.target.value);
          onChange({ ...value, destinations: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) });
        }} />
      </Field>
      <Field label="Start date">
        <input type="date" value={value.startsOn} onChange={(e) => onChange({ ...value, startsOn: e.target.value })} />
      </Field>
      <Field label="End date">
        <input type="date" value={value.endsOn} onChange={(e) => onChange({ ...value, endsOn: e.target.value })} />
      </Field>
      <Field label="Travelers">
        <input type="number" min={1} value={value.travelers}
          onChange={(e) => onChange({ ...value, travelers: Number(e.target.value) })} />
      </Field>
      <Field label="Budget (USD)">
        <input value={value.budget} inputMode="decimal" onChange={(e) => onChange({ ...value, budget: e.target.value })} />
      </Field>
    </div>
  );
}
