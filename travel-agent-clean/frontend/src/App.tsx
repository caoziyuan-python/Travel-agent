import { useState, useRef, useEffect, useCallback } from 'react';
import { sendMessage, resetSession } from './api';
import type { Message } from './types';
import ChatMessage from './components/ChatMessage';
import ChatInput from './components/ChatInput';

const QUICK_PROMPTS = [
  'Help me plan a 3-day cultural trip to Beijing',
  'What are the must-try foods in Beijing?',
  "What's the weather like in Beijing right now?",
  'How do I get from Beijing Capital Airport to the Forbidden City?',
];

function EmptyState({ onPrompt }: { onPrompt: (text: string) => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-full min-h-[55vh] text-center px-6 select-none">
      <div className="text-6xl mb-5 drop-shadow">🏮</div>
      <h2 className="text-xl font-semibold text-gray-800 mb-1">Beijing Travel Agent</h2>
      <p className="text-xs text-gray-400 mb-8 max-w-xs leading-relaxed mt-1">
        Ask me to plan itineraries, find restaurants, check weather, get transit directions, or explore Beijing's attractions.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full max-w-lg">
        {QUICK_PROMPTS.map(p => (
          <button
            key={p}
            onClick={() => onPrompt(p)}
            className="text-left px-4 py-3 bg-white border border-gray-200 hover:border-beijing-red hover:bg-beijing-light rounded-xl text-sm text-gray-700 transition-colors shadow-sm leading-snug"
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [isLoading, setIsLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = useCallback(
    async (text: string) => {
      if (!text.trim() || isLoading) return;

      const userMsg: Message = {
        id: crypto.randomUUID(),
        role: 'user',
        content: text,
        timestamp: new Date(),
      };
      const loadingMsg: Message = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: '',
        timestamp: new Date(),
        isLoading: true,
      };

      setMessages(prev => [...prev, userMsg, loadingMsg]);
      setIsLoading(true);

      try {
        const res = await sendMessage(text, sessionId);
        setSessionId(res.session_id);

        const assistantMsg: Message = {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: res.reply,
          tools_called: res.tools_called,
          itinerary: res.itinerary,
          intent: res.intent,
          needs_clarification: res.needs_clarification,
          out_of_scope: res.out_of_scope,
          timestamp: new Date(),
        };
        setMessages(prev => [...prev.slice(0, -1), assistantMsg]);
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Unknown error';
        const errorMsg: Message = {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: `Connection error: ${msg}\n\nPlease make sure the backend is running:\n  cd backend\n  uvicorn main:app --reload --port 8000`,
          timestamp: new Date(),
          isError: true,
        };
        setMessages(prev => [...prev.slice(0, -1), errorMsg]);
      } finally {
        setIsLoading(false);
      }
    },
    [isLoading, sessionId],
  );

  const handleReset = useCallback(async () => {
    if (sessionId) {
      try {
        await resetSession(sessionId);
      } catch {
        // ignore
      }
    }
    setMessages([]);
    setSessionId(undefined);
  }, [sessionId]);

  return (
    <div className="flex flex-col h-full bg-[#F8F7F4]">
      {/* Header */}
      <header className="flex-none bg-white border-b border-gray-200 px-5 py-3.5 flex items-center justify-between shadow-sm z-10">
        <div className="flex items-center gap-3">
          <span className="text-2xl leading-none">🏮</span>
          <div>
            <h1 className="text-base font-semibold text-gray-900 leading-tight">Beijing Travel Agent</h1>
            <p className="text-[11px] text-gray-400 leading-tight">Powered by AI · Ask in English or Chinese</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {sessionId && (
            <span className="hidden sm:block text-[11px] text-gray-300 font-mono">
              {sessionId.slice(0, 8)}…
            </span>
          )}
          <button
            onClick={handleReset}
            disabled={isLoading}
            className="text-xs text-gray-500 hover:text-gray-800 hover:bg-gray-100 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-40"
          >
            + New Chat
          </button>
        </div>
      </header>

      {/* Chat area */}
      <div className="flex-1 overflow-y-auto chat-scrollbar">
        <div className="max-w-3xl mx-auto px-4 py-6 space-y-5">
          {messages.length === 0 ? (
            <EmptyState onPrompt={handleSend} />
          ) : (
            messages.map(msg => <ChatMessage key={msg.id} message={msg} />)
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* Input */}
      <ChatInput onSend={handleSend} disabled={isLoading} />
    </div>
  );
}
