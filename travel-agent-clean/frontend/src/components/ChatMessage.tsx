import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';
import type { Message } from '../types';
import ItineraryCard from './ItineraryCard';
import ProgressIndicator from './ProgressIndicator';

const MD_COMPONENTS: Components = {
  p:      ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-gray-900">{children}</strong>,
  em:     ({ children }) => <em className="italic">{children}</em>,
  ul:     ({ children }) => <ul className="list-disc list-outside pl-4 mb-2 space-y-1">{children}</ul>,
  ol:     ({ children }) => <ol className="list-decimal list-outside pl-4 mb-2 space-y-1">{children}</ol>,
  li:     ({ children }) => <li className="leading-relaxed">{children}</li>,
  h1:     ({ children }) => <h1 className="font-bold text-base mb-1 mt-2">{children}</h1>,
  h2:     ({ children }) => <h2 className="font-semibold text-sm mb-1 mt-2">{children}</h2>,
  h3:     ({ children }) => <h3 className="font-semibold text-sm mb-1 mt-1.5">{children}</h3>,
  hr:     () => <hr className="my-2 border-gray-200" />,
  code:   ({ children }) => (
    <code className="bg-gray-100 text-gray-700 px-1 py-0.5 rounded text-[12px] font-mono">
      {children}
    </code>
  ),
  pre:    ({ children }) => (
    <pre className="bg-gray-100 rounded-lg p-3 overflow-x-auto text-[12px] font-mono mb-2">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-gray-300 pl-3 italic text-gray-600 mb-2">
      {children}
    </blockquote>
  ),
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-beijing-red underline">
      {children}
    </a>
  ),
};

function MarkdownMessage({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
      {content}
    </ReactMarkdown>
  );
}

function ToolBadges({ tools }: { tools: string[] }) {
  if (!tools.length) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1 px-1">
      {tools.map(t => (
        <span key={t} className="text-[11px] px-2 py-0.5 bg-gray-100 text-gray-400 rounded-full border border-gray-200">
          ⚙ {t}
        </span>
      ))}
    </div>
  );
}

export default function ChatMessage({ message }: { message: Message }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end message-enter">
        <div className="max-w-[78%] bg-beijing-red text-white px-4 py-3 rounded-2xl rounded-tr-md text-sm leading-relaxed shadow-sm">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3 max-w-[92%] message-enter">
      <div className="flex-none w-8 h-8 rounded-full bg-gradient-to-br from-beijing-red to-beijing-dark flex items-center justify-center text-base shadow-sm mt-0.5">
        🏮
      </div>

      <div className="flex flex-col gap-2 flex-1 min-w-0">
        <div
          className={`bg-white border px-4 py-3 rounded-2xl rounded-tl-md shadow-sm text-sm ${
            message.isError
              ? 'border-red-200 bg-red-50 text-red-700'
              : 'border-gray-100 text-gray-800'
          }`}
        >
          {message.isLoading ? (
            <ProgressIndicator />
          ) : (
            <MarkdownMessage content={message.content} />
          )}
        </div>

        {message.tools_called && message.tools_called.length > 0 && (
          <ToolBadges tools={message.tools_called} />
        )}

        {message.itinerary && (
          <ItineraryCard itinerary={message.itinerary} />
        )}
      </div>
    </div>
  );
}
