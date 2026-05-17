import { useState, useEffect } from 'react';
import type { ItineraryDay } from '../types';

interface MapStop {
  lat: number;
  lng: number;
  type: string;
  name: string;
}

function collectStops(days: ItineraryDay[], activeDayIndex: number): MapStop[] {
  const day = days[activeDayIndex];
  if (!day) return [];
  return day.stops
    .filter(s => s.location?.lat && s.location?.lng)
    .map(s => ({
      lat: s.location!.lat!,
      lng: s.location!.lng!,
      type: s.type,
      name: s.name,
    }));
}

interface Props {
  days: ItineraryDay[];
  activeDayIndex: number;
}

export default function ItineraryMap({ days, activeDayIndex }: Props) {
  const [imgSrc, setImgSrc] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const stops = collectStops(days, activeDayIndex);
    if (!stops.length) {
      setImgSrc(null);
      setFailed(false);
      return;
    }
    setLoading(true);
    setFailed(false);
    const params = new URLSearchParams({
      stops: JSON.stringify(stops),
      size: '580x240',
    });
    setImgSrc(`/api/v1/static-map?${params.toString()}`);
  }, [days, activeDayIndex]);

  const stops = collectStops(days, activeDayIndex);
  if (!stops.length) return null;

  return (
    <div className="relative rounded-xl overflow-hidden bg-gray-100 border border-gray-200">
      {/* Legend */}
      <div className="absolute top-2 left-2 z-10 flex flex-col gap-1">
        {stops.slice(0, 8).map((s, i) => (
          <div
            key={i}
            className="flex items-center gap-1.5 bg-white/90 backdrop-blur-sm px-2 py-0.5 rounded-full shadow-sm text-[11px] text-gray-700"
          >
            <span className="font-bold text-beijing-red">{String.fromCharCode(65 + i)}</span>
            <span className="max-w-[140px] truncate">{s.name}</span>
          </div>
        ))}
      </div>

      {loading && !failed && (
        <div className="h-[240px] flex items-center justify-center">
          <span className="text-sm text-gray-400 animate-pulse">Loading map…</span>
        </div>
      )}

      {imgSrc && !failed && (
        <img
          src={imgSrc}
          alt={`Day ${activeDayIndex + 1} route map`}
          className={`w-full object-cover transition-opacity duration-300 ${loading ? 'opacity-0 h-0' : 'opacity-100'}`}
          style={loading ? {} : { height: '240px' }}
          onLoad={() => setLoading(false)}
          onError={() => { setLoading(false); setFailed(true); }}
        />
      )}

      {failed && (
        <div className="h-16 flex items-center justify-center text-sm text-gray-400">
          🗺️ Map unavailable
        </div>
      )}
    </div>
  );
}
