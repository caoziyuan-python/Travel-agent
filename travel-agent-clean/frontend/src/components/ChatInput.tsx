import { useState, useRef, useEffect } from 'react';

interface Props {
  onSend: (text: string) => void;
  disabled?: boolean;
}

export default function ChatInput({ onSend, disabled }: Props) {
  const [text, setText] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 128)}px`;
  }, [text]);

  const handleSubmit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText('');
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="flex-none border-t border-gray-200 bg-white px-4 py-3 shadow-[0_-1px_8px_rgba(0,0,0,0.04)]">
      <div className="flex gap-3 items-end max-w-3xl mx-auto">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about Beijing attractions, plan an itinerary, check weather, find restaurants..."
          rows={1}
          disabled={disabled}
          className="flex-1 resize-none rounded-2xl border border-gray-300 bg-gray-50 px-4 py-3 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:border-beijing-red focus:ring-1 focus:ring-beijing-red focus:bg-white disabled:opacity-50 transition-all leading-relaxed"
        />
        <button
          onClick={handleSubmit}
          disabled={!text.trim() || disabled}
          aria-label="Send"
          className="flex-none w-10 h-10 rounded-full bg-beijing-red hover:bg-beijing-dark disabled:opacity-30 disabled:cursor-not-allowed text-white flex items-center justify-center transition-colors shadow-sm"
        >
          <svg className="w-[18px] h-[18px] translate-x-[1px]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 12 3.269 3.126A59.768 59.768 0 0 1 21.485 12 59.77 59.77 0 0 1 3.269 20.876L5.999 12Zm0 0h7.5" />
          </svg>
        </button>
      </div>
      <p className="text-center text-[11px] text-gray-400 mt-2">
        Ctrl + Enter to send · AI responses may contain errors — verify important details
      </p>
    </div>
  );
}
