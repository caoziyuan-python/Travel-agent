export interface Location {
  name: string;
  address?: string;
  lat?: number;
  lng?: number;
}

export type StopType = 'attraction' | 'restaurant' | 'hotel' | 'transit' | 'other';

export interface ItineraryStop {
  stop_id: string;
  type: StopType;
  name: string;
  start_time?: string;
  duration_min?: number;
  location?: Location;
  cost?: number;
  currency: string;
  notes?: string;
}

export interface ItineraryDay {
  day_index: number;
  date?: string;
  stops: ItineraryStop[];
  daily_total?: number;
}

export interface Budget {
  transport: number;
  accommodation: number;
  food: number;
  tickets: number;
  other: number;
  total: number;
  currency: string;
}

export interface Itinerary {
  itinerary_id: string;
  destination: string;
  duration_days: number;
  themes: string[];
  days: ItineraryDay[];
  budget: Budget;
  summary?: string;
  created_at: string;
}

export interface ChatResponse {
  reply: string;
  tools_called: string[];
  session_id: string;
  intent?: string;
  out_of_scope?: boolean;
  escalation?: string;
  needs_clarification?: boolean;
  clarification_question?: string;
  missing_fields?: string[];
  itinerary?: Itinerary;
  itinerary_id?: string;
  context_summary?: string;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  tools_called?: string[];
  itinerary?: Itinerary;
  intent?: string;
  needs_clarification?: boolean;
  out_of_scope?: boolean;
  timestamp: Date;
  isLoading?: boolean;
  isError?: boolean;
}
