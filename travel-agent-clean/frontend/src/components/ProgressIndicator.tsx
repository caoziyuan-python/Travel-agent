import { useState, useEffect } from 'react';

const STEPS = [
  { icon: '🔍', text: 'Analyzing your request...' },
  { icon: '🏛️', text: 'Searching Beijing attractions database...' },
  { icon: '🗺️', text: 'Planning optimal routes between stops...' },
  { icon: '🍜', text: 'Finding restaurants and cafes nearby...' },
  { icon: '🚇', text: 'Checking real-time transit options...' },
  { icon: '🏨', text: 'Looking up hotel options...' },
  { icon: '💰', text: 'Calculating budget breakdown...' },
  { icon: '✨', text: 'Finalizing your itinerary...' },
];

export default function ProgressIndicator() {
  const [elapsed, setElapsed] = useState(0);
  const [stepIndex, setStepIndex] = useState(0);
  const [fade, setFade] = useState(true);

  useEffect(() => {
    const t = setInterval(() => setElapsed(e => e + 1), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const t = setInterval(() => {
      setFade(false);
      setTimeout(() => {
        setStepIndex(i => (i + 1) % STEPS.length);
        setFade(true);
      }, 200);
    }, 2800);
    return () => clearInterval(t);
  }, []);

  const step = STEPS[stepIndex];

  return (
    <div className="py-1 space-y-2.5">
      {/* Status row */}
      <div
        className="flex items-center gap-2.5 transition-opacity duration-200"
        style={{ opacity: fade ? 1 : 0 }}
      >
        <span className="text-lg leading-none">{step.icon}</span>
        <span className="text-sm text-gray-700 font-medium">{step.text}</span>
      </div>

      {/* Animated bar + timer */}
      <div className="flex items-center gap-3">
        <div className="flex-1 h-1 bg-gray-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-beijing-red rounded-full"
            style={{
              width: `${Math.min(95, (elapsed / 30) * 100)}%`,
              transition: 'width 1s linear',
            }}
          />
        </div>
        <span className="text-xs text-gray-400 tabular-nums flex-none">{elapsed}s</span>
      </div>
    </div>
  );
}
