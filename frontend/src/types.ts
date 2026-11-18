// HTTP contract of the backend (backend/app/api). Amounts are USD decimal strings.

export type Role = "organizer" | "editor" | "viewer";
export type Money = { amount: string; currency: "USD" };

export type User = { id: string; displayName: string };

export type Trip = {
  id: string;
  name: string;
  destinations: string[];
  startsOn: string;
  endsOn: string;
  travelers: number;
  budget: Money;
  version: number;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
  myRole: Role | null;
};

export type TripInput = {
  name: string;
  destinations: string[];
  startsOn: string;
  endsOn: string;
  travelers: number;
  budget: string;
};

export type Member = { userId: string; displayName: string; role: Role; addedAt: string };

export type SearchOutcome = "complete" | "partial" | "empty" | "failed";

export type Offer = {
  offerId: string;
  providerId: string;
  category: string;
  title: string;
  description: string;
  destination: string;
  origin: string | null;
  startsAt: string;
  endsAt: string;
  travelers: number;
  price: Money;
  priceType: "estimate";
  revision: string;
  available: boolean;
  validUntil: string;
  quoteExpired: boolean;
  restrictions: string[];
};

export type SearchResult = {
  outcome: SearchOutcome;
  message: string;
  disclaimer: string;
  query: {
    category: string;
    destination: string;
    origin: string | null;
    startsOn: string;
    endsOn: string;
    travelers: number;
    providerId: string | null;
  };
  searchedAt: string | null;
  receivedAt: string;
  offers: Offer[];
  failedSuppliers: { providerId: string; code: string; message: string }[];
  error: {
    code: string;
    message: string;
    retryAfterSeconds: number | null;
    providerRequestId: string | null;
  } | null;
  requestId: string | null;
};

export type SearchInput = {
  category: string;
  destination: string;
  origin?: string;
  startsOn?: string;
  endsOn?: string;
};

export type BookingStatus =
  | "draft"
  | "submitted"
  | "unknown"
  | "pending"
  | "confirmed"
  | "failed"
  | "rejected";

export type Flag = { code: string; message: string; relatedItemIds: string[] };

export type Quote = {
  price: Money;
  priceType: "estimate";
  revision: string;
  available: boolean;
  validUntil: string;
  quotedAt: string;
  expired: boolean;
};

export type Item = {
  itemId: string;
  position: number;
  offerId: string;
  providerId: string;
  category: string;
  title: string;
  description: string;
  destination: string;
  origin: string | null;
  startsAt: string;
  endsAt: string;
  travelers: number;
  restrictions: string[];
  quote: Quote;
  booking: { bookingId: string; status: BookingStatus } | null;
  flags: Flag[];
  addedBy: string;
  addedAt: string;
};

export type Itinerary = {
  tripId: string;
  tripVersion: number;
  items: Item[];
  missing: { code: string; message: string }[];
  rules: string[];
  generatedAt: string;
};

export type Refreshed = Itinerary & { changed: boolean; summary: string };

export type Change = { version: number; by: string; at: string; kind: string; summary: string };

export type Costs = {
  tripId: string;
  tripVersion: number;
  currency: "USD";
  lines: {
    itemId: string;
    title: string;
    category: string;
    amount: Money;
    basis: "estimate" | "confirmed";
    bookingId: string | null;
    bookingStatus: BookingStatus | null;
    note: string;
  }[];
  estimatedTotal: Money;
  confirmedTotal: Money;
  total: Money;
  budget: { amount: Money; remaining: Money; overBudget: boolean; usedPercent: string | null };
  rounding: string;
};

export type Booking = {
  bookingId: string;
  itemId: string | null;
  itemTitle: string | null;
  offerId: string;
  status: BookingStatus;
  booked: boolean;
  nextStep: string;
  canRetry: boolean;
  expectedPrice: Money;
  expectedRevision: string;
  acceptedPrice: Money | null;
  acceptedOffer: Record<string, unknown> | null;
  providerBookingId: string | null;
  clientReference: string;
  failure: { code: string | null; message: string | null } | null;
  lastError: {
    code: string;
    message: string | null;
    providerRequestId: string | null;
    retryAfterSeconds: number | null;
    at: string | null;
  } | null;
  currentTerms: Terms | null;
  requestedBy: string;
  createdAt: string;
  submittedAt: string | null;
  updatedAt: string;
};

// Offer terms as the backend snapshots them (current terms in a 409 TERMS_CHANGED).
export type Terms = {
  price?: Money;
  revision?: string;
  available?: boolean;
  validUntil?: string;
  [key: string]: unknown;
};

export type DocumentStatus = "waiting" | "in_progress" | "complete" | "failed";

export type TripDocument = {
  documentId: string;
  tripId: string;
  tripVersion: number;
  snapshotAt: string;
  status: DocumentStatus;
  ready: boolean;
  requestedBy: string;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
  sizeBytes: number | null;
  sha256: string | null;
  error: string | null;
  downloadUrl: string | null;
};

export type ToolCall = { name: string; arguments: Record<string, unknown>; isError: boolean; result: unknown };

export type ChatReply = {
  answer: string;
  stopReason: "answered" | "iteration_limit" | "time_limit";
  toolCalls: ToolCall[];
  iterations: number;
  model: string;
  readOnly: true;
};

export type ChatTurn = { role: "user" | "assistant"; text: string };

export type VersionConflictDetails = {
  yourVersion: number;
  currentVersion: number;
  changesSinceYourVersion: Change[];
  current: Trip;
  currentItinerary: Itinerary;
};
