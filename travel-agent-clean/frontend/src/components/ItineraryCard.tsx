import { useState } from 'react';
import type { Itinerary, ItineraryStop, StopType } from '../types';
import ItineraryMap from './ItineraryMap';

const STOP_META: Record<StopType, { icon: string; bg: string; border: string; dot: string }> = {
  attraction: { icon: '🏛️', bg: 'bg-amber-50',  border: 'border-amber-200',  dot: 'bg-amber-400' },
  restaurant: { icon: '🍜', bg: 'bg-orange-50', border: 'border-orange-200', dot: 'bg-orange-400' },
  hotel:      { icon: '🏨', bg: 'bg-blue-50',   border: 'border-blue-200',   dot: 'bg-blue-400' },
  transit:    { icon: '🚇', bg: 'bg-gray-50',   border: 'border-gray-200',   dot: 'bg-gray-400' },
  other:      { icon: '📍', bg: 'bg-purple-50', border: 'border-purple-200', dot: 'bg-purple-400' },
};

function formatCost(amount: number, currency: string): string {
  if (currency === 'CNY') return `¥${Math.round(amount).toLocaleString()}`;
  if (currency === 'USD') return `$${amount.toFixed(2)}`;
  return `${amount.toFixed(2)} ${currency}`;
}

function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function StopItem({ stop, isLast }: { stop: ItineraryStop; isLast: boolean }) {
  const meta = STOP_META[stop.type] ?? STOP_META.other;
  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center flex-none">
        <div className={`w-9 h-9 rounded-full flex items-center justify-center text-base border ${meta.bg} ${meta.border} shadow-sm flex-none`}>
          {meta.icon}
        </div>
        {!isLast && <div className="w-px flex-1 bg-gray-200 mt-1 mb-1 min-h-[12px]" />}
      </div>

      <div className="flex-1 pb-3 min-w-0">
        <div className="flex items-start justify-between gap-2 flex-wrap">
          <span className="text-sm font-semibold text-gray-900 leading-snug">{stop.name}</span>
          {stop.cost != null && stop.cost > 0 && (
            <span className="text-sm font-bold text-beijing-red whitespace-nowrap">
              {formatCost(stop.cost, stop.currency)}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-0.5">
          {stop.start_time && <span className="text-xs text-gray-500">⏰ {stop.start_time}</span>}
          {stop.duration_min != null && stop.duration_min > 0 && (
            <span className="text-xs text-gray-500">⌛ {formatDuration(stop.duration_min)}</span>
          )}
          {stop.location?.address && (
            <span className="text-xs text-gray-400 truncate max-w-[200px]">📍 {stop.location.address}</span>
          )}
        </div>
        {stop.notes && (
          <p className="mt-1 text-xs text-gray-600 leading-relaxed">{stop.notes}</p>
        )}
      </div>
    </div>
  );
}

function BudgetBars({ budget }: { budget: Itinerary['budget'] }) {
  const rows = [
    { label: 'Accommodation', value: budget.accommodation, color: 'bg-blue-400' },
    { label: 'Transport',     value: budget.transport,     color: 'bg-gray-400' },
    { label: 'Food',          value: budget.food,          color: 'bg-orange-400' },
    { label: 'Tickets',       value: budget.tickets,       color: 'bg-amber-400' },
    { label: 'Other',         value: budget.other,         color: 'bg-purple-400' },
  ].filter(r => r.value > 0);

  if (!rows.length && !budget.total) return null;
  const max = Math.max(...rows.map(r => r.value), 1);

  return (
    <div className="border-t border-gray-100 px-5 py-4 bg-gray-50 rounded-b-2xl space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">Budget Breakdown</p>
        {budget.total > 0 && (
          <span className="text-base font-bold text-beijing-red">
            {formatCost(budget.total, budget.currency)}
          </span>
        )}
      </div>
      <div className="space-y-2">
        {rows.map(r => (
          <div key={r.label} className="space-y-0.5">
            <div className="flex justify-between text-xs">
              <span className="text-gray-500">{r.label}</span>
              <span className="text-gray-700 font-medium">{formatCost(r.value, budget.currency)}</span>
            </div>
            <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${r.color} transition-all duration-500`}
                style={{ width: `${(r.value / max) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function buildCopyText(itinerary: Itinerary): string {
  const lines: string[] = [
    `${itinerary.destination} · ${itinerary.duration_days}-Day Itinerary`,
    itinerary.themes.length ? `Themes: ${itinerary.themes.join(', ')}` : '',
    '',
  ];
  for (const day of itinerary.days) {
    lines.push(`--- Day ${day.day_index}${day.date ? ` (${day.date})` : ''} ---`);
    for (const s of day.stops) {
      const time = s.start_time ? `[${s.start_time}] ` : '';
      const dur = s.duration_min ? ` (${formatDuration(s.duration_min)})` : '';
      const cost = s.cost ? ` · ${formatCost(s.cost, s.currency)}` : '';
      lines.push(`${time}${s.name}${dur}${cost}`);
      if (s.notes) lines.push(`  ${s.notes}`);
    }
    lines.push('');
  }
  if (itinerary.budget.total) {
    lines.push(`Total budget: ${formatCost(itinerary.budget.total, itinerary.budget.currency)}`);
  }
  return lines.filter(Boolean).join('\n');
}

export default function ItineraryCard({ itinerary }: { itinerary: Itinerary }) {
  const [collapsed, setCollapsed] = useState(false);
  const [activeDay, setActiveDay] = useState(0);
  const [copied, setCopied] = useState(false);

  const day = itinerary.days[activeDay];

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(buildCopyText(itinerary));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard unavailable */ }
  };

  return (
    <div className="rounded-2xl border border-gray-200 shadow-md overflow-hidden message-enter">
      {/* Header */}
      <div className="bg-gradient-to-r from-beijing-red to-beijing-dark px-5 py-4">
        <div className="flex items-start justify-between gap-3">
          <button
            onClick={() => setCollapsed(c => !c)}
            className="flex-1 text-left focus:outline-none"
          >
            <h3 className="text-white font-semibold text-sm leading-snug">
              🗺️ {itinerary.destination} · {itinerary.duration_days}-Day Itinerary
            </h3>
            {itinerary.themes.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {itinerary.themes.map(t => (
                  <span key={t} className="text-[11px] bg-white/20 text-white px-2 py-0.5 rounded-full">
                    {t}
                  </span>
                ))}
              </div>
            )}
          </button>

          <div className="flex items-center gap-2 flex-none mt-0.5">
            <button
              onClick={handleCopy}
              title="Copy itinerary"
              className="text-white/70 hover:text-white text-xs px-2 py-1 rounded-lg hover:bg-white/10 transition-colors"
            >
              {copied ? '✓ Copied' : '⎘ Copy'}
            </button>
            <button
              onClick={() => setCollapsed(c => !c)}
              className="text-white/60 hover:text-white text-xs transition-colors"
            >
              {collapsed ? '▼' : '▲'}
            </button>
          </div>
        </div>

        {/* Summary */}
        {!collapsed && itinerary.summary && (
          <p className="mt-2 text-xs text-white/80 leading-relaxed border-t border-white/10 pt-2">
            {itinerary.summary}
          </p>
        )}
      </div>

      {!collapsed && (
        <div className="bg-white">
          {/* Day tabs */}
          {itinerary.days.length > 1 && (
            <div className="flex border-b border-gray-100 bg-gray-50 overflow-x-auto">
              {itinerary.days.map((d, i) => {
                const attractions = d.stops.filter(s => s.type === 'attraction').length;
                return (
                  <button
                    key={d.day_index}
                    onClick={() => setActiveDay(i)}
                    className={`flex-1 min-w-[80px] py-2.5 px-3 text-xs font-medium transition-colors whitespace-nowrap ${
                      i === activeDay
                        ? 'text-beijing-red border-b-2 border-beijing-red bg-white'
                        : 'text-gray-500 hover:text-gray-700'
                    }`}
                  >
                    <span>Day {d.day_index}</span>
                    {d.date && <span className="block text-[10px] font-normal text-gray-400">{d.date}</span>}
                    {attractions > 0 && (
                      <span className="block text-[10px] font-normal text-gray-400 mt-0.5">
                        {attractions} attraction{attractions > 1 ? 's' : ''}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}

          {/* Route map */}
          {day && (
            <div className="px-4 pt-3">
              <ItineraryMap days={itinerary.days} activeDayIndex={activeDay} />
            </div>
          )}

          {/* Stops list */}
          {day ? (
            <div className="px-4 pt-3 pb-2">
              {day.stops.length === 0 ? (
                <p className="text-sm text-gray-400 text-center py-4">No stops for this day.</p>
              ) : (
                day.stops.map((stop, idx) => (
                  <StopItem key={stop.stop_id} stop={stop} isLast={idx === day.stops.length - 1} />
                ))
              )}
              {day.daily_total != null && day.daily_total > 0 && (
                <div className="flex justify-between text-sm pt-2 border-t border-gray-100 mt-1 pb-3">
                  <span className="text-gray-500">Day {day.day_index} total</span>
                  <span className="font-semibold text-gray-800">
                    {formatCost(day.daily_total, day.stops[0]?.currency ?? 'CNY')}
                  </span>
                </div>
              )}
            </div>
          ) : null}

          <BudgetBars budget={itinerary.budget} />
        </div>
      )}
    </div>
  );
}
