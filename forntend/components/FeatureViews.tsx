"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent, ReactNode } from "react";
import { ArrowLeft, ArrowUp, Bell, Brain, Check, Code2, Copy, Pencil, Plus, RotateCcw, Trash2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { streamCodexMessage } from "@/lib/api";
import type { Citation } from "@/lib/types";

type Reminder = {
  id: string;
  title: string;
  dueAt: string;
  completed: boolean;
  notified: boolean;
};
type CodexEntry = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  plan?: { steps: string[] };
  streaming?: boolean;
};
type CodexConversation = {
  id: string;
  title: string;
  updatedAt: number;
  entries: CodexEntry[];
};

const EMPTY_REMINDERS: Reminder[] = [];
const EMPTY_CONVERSATIONS: CodexConversation[] = [];
const CODEX_SYSTEM_INSTRUCTION =
  "You are Codex, a coding assistant. Work only with the code and context provided. Do not claim to edit repository files. When showing source code, use fenced Markdown code blocks with the correct language identifier, and keep explanations outside code blocks.";

function markdownText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(markdownText).join("");
  if (node && typeof node === "object" && "props" in node) {
    return markdownText((node.props as { children?: ReactNode }).children);
  }
  return "";
}

function CodexCodeBlock({
  language,
  code,
  children,
}: {
  language: string;
  code: string;
  children: ReactNode;
}) {
  const [copied, setCopied] = useState(false);

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="my-4 overflow-hidden rounded-2xl border border-white/10 bg-[#252525]">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-2.5 text-sm text-white/75">
        <span className="flex items-center gap-2 font-medium">
          <Code2 size={16} aria-hidden="true" />
          {language || "Code"}
        </span>
        <button
          type="button"
          onClick={() => void copyCode()}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-white/65 transition-colors hover:bg-white/10 hover:text-white"
          aria-label={copied ? "Code copied" : "Copy code"}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto px-4 py-3 text-[13px] leading-6 !border-0 !rounded-none !bg-transparent">
        {children}
      </pre>
    </div>
  );
}

const codexMarkdownComponents: Components = {
  pre({ children }) {
    const child = Array.isArray(children) ? children[0] : children;
    const className =
      child && typeof child === "object" && "props" in child
        ? (child.props as { className?: string }).className ?? ""
        : "";
    const language = /language-([\w-]+)/.exec(className)?.[1] ?? "";
    const code = markdownText(children).replace(/\n$/, "");
    return (
      <CodexCodeBlock language={language} code={code}>
        {children}
      </CodexCodeBlock>
    );
  },
  code({ className, children, ...props }) {
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  },
};

function visibleCodexEntryContent(entry: CodexEntry): string {
  if (entry.role !== "user") return entry.content;
  const legacyPrefix = `${CODEX_SYSTEM_INSTRUCTION}\n\n`;
  return entry.content.startsWith(legacyPrefix)
    ? entry.content.slice(legacyPrefix.length)
    : entry.content;
}

function isReminderList(value: unknown): value is Reminder[] {
  return Array.isArray(value) && value.every(
    (reminder) =>
      reminder &&
      typeof reminder.id === "string" &&
      typeof reminder.title === "string" &&
      typeof reminder.dueAt === "string" &&
      !Number.isNaN(Date.parse(reminder.dueAt)) &&
      typeof reminder.completed === "boolean" &&
      typeof reminder.notified === "boolean",
  );
}

function isCitation(value: unknown): value is Citation {
  if (!value || typeof value !== "object") return false;
  if ("type" in value && value.type === "web" && "title" in value && "url" in value) {
    if (typeof value.title !== "string" || typeof value.url !== "string") return false;
    try {
      const url = new URL(value.url);
      return (url.protocol === "https:" || url.protocol === "http:") &&
        !url.username && !url.password;
    } catch {
      return false;
    }
  }
  return "file" in value &&
    typeof value.file === "string" &&
    "page" in value &&
    (value.page === null || typeof value.page === "number") &&
    "preview" in value &&
    typeof value.preview === "string";
}

function isCodexEntryList(value: unknown): value is CodexEntry[] {
  return Array.isArray(value) && value.every(
    (entry) =>
      entry &&
      typeof entry.id === "string" &&
      (entry.role === "user" || entry.role === "assistant") &&
      typeof entry.content === "string" &&
      (entry.citations === undefined ||
        (Array.isArray(entry.citations) && entry.citations.every(isCitation))) &&
      (entry.plan === undefined ||
        (entry.plan && Array.isArray(entry.plan.steps) &&
          entry.plan.steps.every((step: unknown) => typeof step === "string"))) &&
      (entry.streaming === undefined || typeof entry.streaming === "boolean"),
  );
}

function isCodexConversationList(value: unknown): value is CodexConversation[] {
  return Array.isArray(value) && value.every(
    (conversation) =>
      conversation &&
      typeof conversation.id === "string" &&
      conversation.id.startsWith("codex-") &&
      typeof conversation.title === "string" &&
      typeof conversation.updatedAt === "number" &&
      isCodexEntryList(conversation.entries),
  );
}

function clearPersistedCodexStreaming(storageKey: string) {
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return;

    const parsed: unknown = JSON.parse(raw);
    if (!isCodexConversationList(parsed)) return;

    const hasStreamingEntry = parsed.some((conversation) =>
      conversation.entries.some((entry) => entry.streaming),
    );
    if (!hasStreamingEntry) return;

    const settled = parsed.map((conversation) => ({
      ...conversation,
      entries: conversation.entries.map((entry) =>
        entry.streaming ? { ...entry, streaming: false } : entry,
      ),
    }));
    window.localStorage.setItem(storageKey, JSON.stringify(settled));
  } catch {
    // Storage may be unavailable; do not prevent Codex from unmounting.
  }
}

function localDateTimeValue(date: Date) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function useAccountStorage<T>(
  key: string,
  fallback: T,
  isValid: (value: unknown) => value is T,
) {
  const [value, setValue] = useState<T>(fallback);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    // This effect synchronizes the account-scoped localStorage snapshot.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoaded(false);
    try {
      const raw = window.localStorage.getItem(key);
      const parsed: unknown = raw ? JSON.parse(raw) : null;
      setValue(isValid(parsed) ? parsed : fallback);
    } catch {
      setValue(fallback);
    } finally {
      setLoaded(true);
    }
  }, [fallback, isValid, key]);

  useEffect(() => {
    if (!loaded) return;
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Storage may be unavailable in private browsing; the current view remains usable.
    }
  }, [key, loaded, value]);

  return [value, setValue, loaded] as const;
}

export function CodexView({ userKey, onExit }: { userKey: string; onExit: () => void }) {
  const [request, setRequest] = useState("");
  const [thinkMode, setThinkMode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedEntryId, setCopiedEntryId] = useState<string | null>(null);
  const [editingEntryId, setEditingEntryId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const storageKey = `nova-codex-workspace-v2:${userKey}`;
  const [conversations, setConversations, loaded] = useAccountStorage<CodexConversation[]>(
    storageKey,
    EMPTY_CONVERSATIONS,
    isCodexConversationList,
  );
  const [activeId, setActiveId] = useState<string | null>(null);
  const streamRef = useRef<{ abort: () => void } | null>(null);
  const activeConversation = conversations.find((conversation) => conversation.id === activeId);
  const entries = activeConversation?.entries ?? [];
  const lastUserEntryId = [...entries].reverse().find((entry) => entry.role === "user")?.id;

  useEffect(() => {
    // Streaming belongs to the account-scoped workspace and resets when its key changes.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setIsStreaming(false);
    return () => {
      streamRef.current?.abort();
      streamRef.current = null;
      clearPersistedCodexStreaming(storageKey);
    };
  }, [storageKey]);

  useEffect(() => {
    if (loaded && activeId && !conversations.some((conversation) => conversation.id === activeId)) {
      // The persisted workspace may no longer contain the selected conversation.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setActiveId(null);
    }
  }, [activeId, conversations, loaded]);

  const createConversation = () => {
    if (isStreaming) return;
    const conversation: CodexConversation = {
      id: `codex-${crypto.randomUUID()}`,
      title: "New task",
      updatedAt: Date.now(),
      entries: [],
    };
    setConversations((current) => [conversation, ...current]);
    setActiveId(conversation.id);
    setRequest("");
    setError(null);
    setEditingEntryId(null);
  };

  const sendRequest = (value: string, updatedAt: number, replaceEntryId?: string) => {
    if (isStreaming) return;
    const prompt = value.trim();
    if (!prompt) {
      setError("Enter a coding task or paste code to get started.");
      return;
    }

    const title = prompt.split("\n")[0].slice(0, 48) || "New task";
    const conversation = activeConversation ?? {
      id: `codex-${crypto.randomUUID()}`,
      title,
      updatedAt,
      entries: [],
    };
    const replaceIndex = replaceEntryId
      ? conversation.entries.findIndex((entry) => entry.id === replaceEntryId)
      : -1;
    if (replaceEntryId && replaceIndex < 0) {
      setError("That prompt is no longer available to retry or edit.");
      return;
    }
    const previousEntries = replaceIndex >= 0
      ? conversation.entries.slice(0, replaceIndex)
      : conversation.entries;
    const task = [
      CODEX_SYSTEM_INSTRUCTION,
      prompt,
    ].join("\n\n");
    const turnId = crypto.randomUUID();
    const assistantEntryId = `${turnId}-assistant`;
    const newEntries: CodexEntry[] = [
      { id: `${turnId}-user`, role: "user", content: prompt },
      { id: assistantEntryId, role: "assistant", content: "", streaming: true },
    ];
    const conversationId = conversation.id;

    setActiveId(conversationId);
    setConversations((current) => {
      const exists = current.some((item) => item.id === conversationId);
      const updated = {
        ...conversation,
        title: previousEntries.length ? conversation.title : title,
        updatedAt,
        entries: [...previousEntries, ...newEntries].slice(-60),
      };
      return exists
        ? current.map((item) => item.id === conversationId ? updated : item)
        : [updated, ...current];
    });
    setRequest("");
    setError(null);
    setEditingEntryId(null);
    setIsStreaming(true);
    streamRef.current = streamCodexMessage(
      conversationId,
      task,
      (event) => {
        if (event.type === "text") {
          // The timestamp is captured before the functional state update.
          const updatedAt = Date.now();
          setConversations((current) => current.map((item) => item.id === conversationId
            ? { ...item, updatedAt, entries: item.entries.map((entry) =>
              entry.id === assistantEntryId ? { ...entry, content: entry.content + event.text } : entry,
            ) }
            : item));
        } else if (event.type === "plan") {
          setConversations((current) => current.map((item) => item.id === conversationId
            ? { ...item, entries: item.entries.map((entry) =>
              entry.id === assistantEntryId ? { ...entry, plan: { steps: event.steps } } : entry,
            ) }
            : item));
        } else if (event.type === "citations") {
          setConversations((current) => current.map((item) => item.id === conversationId
            ? { ...item, entries: item.entries.map((entry) =>
              entry.id === assistantEntryId ? { ...entry, citations: event.items } : entry,
            ) }
            : item));
        } else if (event.type === "done" || event.type === "error") {
          if (event.type === "error") setError(event.message);
          setConversations((current) => current.map((item) => item.id === conversationId
            ? { ...item, entries: item.entries.map((entry) =>
              entry.id === assistantEntryId ? { ...entry, streaming: false } : entry,
            ) }
            : item));
          streamRef.current = null;
          setIsStreaming(false);
        }
      },
      (streamError) => {
        setError(streamError.message || "Codex could not complete the request.");
        setConversations((current) => current.map((item) => item.id === conversationId
          ? { ...item, entries: item.entries.map((entry) =>
            entry.id === assistantEntryId ? { ...entry, streaming: false } : entry,
          ) }
          : item));
        streamRef.current = null;
        setIsStreaming(false);
      },
      thinkMode,
    );
  };

  const copyEntry = async (entry: CodexEntry) => {
    try {
      await navigator.clipboard.writeText(visibleCodexEntryContent(entry));
      setCopiedEntryId(entry.id);
      window.setTimeout(() => {
        setCopiedEntryId((current) => current === entry.id ? null : current);
      }, 1500);
    } catch {
      setError("Could not copy this message to the clipboard.");
    }
  };

  const editEntry = (entry: CodexEntry) => {
    setRequest(visibleCodexEntryContent(entry));
    setEditingEntryId(entry.id);
    setError(null);
  };

  if (!loaded) {
    return <main className="flex flex-1 items-center justify-center text-sm text-white/50">Loading Codex workspace…</main>;
  }

  return (
    <main className="flex min-h-0 flex-1 overflow-hidden bg-surface">
      <aside className="flex w-64 shrink-0 flex-col border-r border-surface-border bg-surface-raised p-3">
        <div className="mb-4 flex items-center justify-between">
          <button type="button" onClick={onExit} title="Back to Nova GPT" aria-label="Back to Nova GPT" className="rounded-lg p-2 text-white/65 hover:bg-surface-hover hover:text-white">
            <ArrowLeft size={18} />
          </button>
          <div className="flex items-center gap-2 text-sm font-semibold"><Code2 size={18} className="text-accent" /> Codex</div>
          <span className="w-9" />
        </div>
        <button type="button" onClick={createConversation} disabled={isStreaming} className="mb-5 flex items-center gap-2 rounded-lg border border-surface-border px-3 py-2.5 text-sm text-white hover:bg-surface-hover disabled:opacity-50">
          <Plus size={16} /> New chat
        </button>
        <h2 className="mb-2 px-2 text-xs font-medium uppercase tracking-wide text-white/40">Recents</h2>
        <nav aria-label="Codex conversations" className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {conversations.map((conversation) => (
            <button key={conversation.id} type="button" onClick={() => { setActiveId(conversation.id); setError(null); setEditingEntryId(null); setRequest(""); }} className={`w-full truncate rounded-lg px-3 py-2 text-left text-sm ${activeId === conversation.id ? "bg-surface-hover text-white" : "text-white/65 hover:bg-surface-hover hover:text-white"}`}>
              {conversation.title}
            </button>
          ))}
          {conversations.length === 0 && <p className="px-2 py-2 text-xs text-white/40">Your coding chats will appear here.</p>}
        </nav>
        <p className="mt-3 border-t border-surface-border pt-3 text-xs text-white/40">Separate from Nova GPT chats</p>
      </aside>
      <section className="flex min-w-0 flex-1 flex-col items-center overflow-y-auto px-4 py-7">
        <div className="flex w-full max-w-2xl flex-1 flex-col">
          <header className="mb-5 flex items-center gap-3">
            <Code2 size={22} className="text-accent" />
            <div>
              <h1 className="text-xl font-semibold">{activeConversation?.title || "Codex"}</h1>
              <p className="text-sm text-white/50">A dedicated workspace for coding tasks.</p>
            </div>
          </header>
          <div className="mb-5 flex flex-1 flex-col items-stretch gap-5">
            {entries.length === 0 && (
              <div className="flex min-h-64 flex-col items-center justify-center text-center">
                <Code2 size={34} className="mb-4 text-accent" />
                <h2 className="text-2xl font-semibold">What are we building?</h2>
                <p className="mt-2 max-w-lg text-sm text-white/50">Ask Codex to explain, refactor, debug, or complete code. Your work stays in this separate workspace.</p>
                <div className="mt-6 grid w-full max-w-xl gap-2 sm:grid-cols-2">
                  {["Explain a code snippet", "Debug an error", "Refactor a function", "Complete this implementation"].map((suggestion) => (
                    <button key={suggestion} type="button" onClick={() => setRequest(suggestion)} className="rounded-xl border border-surface-border p-3 text-left text-sm text-white/65 hover:bg-surface-raised">
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {entries.map((entry) => (
              <div key={entry.id} className={`flex w-full flex-col ${entry.role === "user" ? "items-end" : "items-start"}`}>
              <article className={`rounded-xl p-4 ${entry.role === "user" ? "w-fit max-w-[85%] bg-surface-raised" : "w-full max-w-4xl"}`}>
                <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-white/45">{entry.role === "user" ? "You" : "Codex"}</h2>
                {entry.role === "assistant" ? (
                  <>
                    {entry.plan?.steps.length ? (
                      <section aria-label="Research plan" className="mb-4 rounded-lg border border-white/10 bg-black/10 p-3 text-sm">
                        <h3 className="mb-2 font-medium text-white/75">Plan</h3>
                        <ol className="list-inside list-decimal space-y-1 text-white/60">
                          {entry.plan.steps.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}
                        </ol>
                      </section>
                    ) : null}
                    <div className="prose prose-invert max-w-none break-words text-sm leading-relaxed text-white/85 [&_p]:my-2 [&_ul]:my-2 [&_ol]:my-2">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        rehypePlugins={[rehypeHighlight]}
                        components={codexMarkdownComponents}
                      >
                        {visibleCodexEntryContent(entry) ||
                          (entry.streaming ? "Codex is working…" : "No response was returned.")}
                      </ReactMarkdown>
                    </div>
                    {entry.citations?.length ? (
                      <section aria-label="Sources" className="mt-4 border-t border-white/10 pt-3 text-sm">
                        <h3 className="mb-2 font-semibold text-white/75">Sources</h3>
                        <ul className="space-y-2">
                          {entry.citations.map((citation, index) => (
                            <li key={"url" in citation ? `web-${citation.url}` : `document-${citation.file}-${citation.page ?? "unknown"}-${index}`}>
                              {"url" in citation ? (
                                <a href={citation.url} target="_blank" rel="noopener noreferrer" className="break-words text-blue-300 underline decoration-blue-300/40 underline-offset-2 hover:text-blue-200">
                                  {citation.title}
                                  <span className="ml-1 text-xs text-white/45">(web source)</span>
                                </a>
                              ) : (
                                <span className="text-white/70">
                                  {citation.file}{citation.page !== null ? `, page ${citation.page}` : ""}
                                </span>
                              )}
                            </li>
                          ))}
                        </ul>
                      </section>
                    ) : null}
                  </>
                ) : (
                  <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-white/85">
                    {visibleCodexEntryContent(entry)}
                  </pre>
                )}
              </article>
                {!isStreaming && (entry.role === "user" || visibleCodexEntryContent(entry)) && (
                  <div className="mt-1 flex gap-1">
                    <button
                      type="button"
                      onClick={() => void copyEntry(entry)}
                      className="rounded p-1.5 text-white/45 hover:bg-surface-hover hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                      aria-label={copiedEntryId === entry.id ? "Message copied" : "Copy message"}
                      title={copiedEntryId === entry.id ? "Copied" : "Copy message"}
                    >
                      {copiedEntryId === entry.id ? <Check size={14} /> : <Copy size={14} />}
                    </button>
                    {entry.role === "user" && entry.id === lastUserEntryId && (
                      <>
                        <button
                          type="button"
                          onClick={() => sendRequest(visibleCodexEntryContent(entry), Date.now(), entry.id)}
                          className="rounded p-1.5 text-white/45 hover:bg-surface-hover hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                          aria-label="Retry message"
                          title="Retry message"
                        >
                          <RotateCcw size={14} />
                        </button>
                        <button
                          type="button"
                          onClick={() => editEntry(entry)}
                          className="rounded p-1.5 text-white/45 hover:bg-surface-hover hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                          aria-label="Edit message"
                          title="Edit message"
                        >
                          <Pencil size={14} />
                        </button>
                      </>
                    )}
                  </div>
                )}
                </div>
            ))}
          </div>
          <form onSubmit={(event) => {
            event.preventDefault();
            sendRequest(request, Date.now(), editingEntryId ?? undefined);
          }} className="rounded-2xl border border-surface-border bg-surface-raised p-3 shadow-lg transition-colors focus-within:border-white/20">
            <label htmlFor="codex-request" className="sr-only">What should Codex do?</label>
            {editingEntryId && (
                <div className="mb-2 flex items-center justify-between px-2 text-xs text-white/55">
                  <span>Editing your last prompt; submitting replaces that turn.</span>
                  <button
                    type="button"
                    onClick={() => {
                      setEditingEntryId(null);
                      setRequest("");
                    }}
                    className="rounded px-2 py-1 hover:bg-white/10 hover:text-white"
                  >
                    Cancel
                  </button>
                </div>
            )}
            <textarea
              id="codex-request"
              value={request}
              onChange={(event) => setRequest(event.target.value)}
              onKeyDown={(event: KeyboardEvent<HTMLTextAreaElement>) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              maxLength={12000}
              rows={2}
              disabled={isStreaming}
              className="max-h-32 min-h-14 w-full resize-none bg-transparent px-2 py-1.5 text-sm text-white outline-none placeholder:text-white/40 disabled:opacity-60 sm:max-h-64 sm:min-h-24 sm:py-2"
              placeholder="Ask Codex anything about your code…"
            />
            {error && <p role="alert" className="px-2 pt-2 text-sm text-red-400">{error}</p>}
            <div className="mt-2 flex items-center justify-between border-t border-surface-border px-1 pt-3">
              <button
                type="button"
                onClick={() => setThinkMode((enabled) => !enabled)}
                disabled={isStreaming}
                aria-pressed={thinkMode}
                title="Toggle Think mode (research and planning)"
                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-2 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                  thinkMode
                    ? "bg-white/10 text-white"
                    : "text-white/65 hover:bg-white/5 hover:text-white"
                }`}
              >
                <Brain size={18} aria-hidden="true" />
                Think
              </button>
              <button type="submit" disabled={isStreaming || !request.trim()} aria-label={isStreaming ? "Codex is working" : "Send message"} title={isStreaming ? "Codex is working" : "Send message"} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50">
                <ArrowUp size={19} strokeWidth={2.5} />
              </button>
            </div>
          </form>
        </div>
      </section>
    </main>
  );
}

export function ScheduledView({ userKey }: { userKey: string }) {
  const [reminders, setReminders, loaded] = useAccountStorage<Reminder[]>(
    `nova-reminders:${userKey}`,
    EMPTY_REMINDERS,
    isReminderList,
  );
  const [title, setTitle] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(0);

  useEffect(() => {
    // Initialize from the system clock after mount, then keep reminders synchronized.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!loaded || !("Notification" in window) || Notification.permission !== "granted") return;
    const due = reminders.filter((reminder) => !reminder.completed && !reminder.notified && Date.parse(reminder.dueAt) <= now);
    if (!due.length) return;
    due.forEach((reminder) => {
      new Notification("Nova GPT reminder", { body: reminder.title });
    });
    setReminders((current) => current.map((reminder) => due.some((item) => item.id === reminder.id) ? { ...reminder, notified: true } : reminder));
  }, [loaded, now, reminders, setReminders]);

  const createReminder = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = title.trim();
    const dueDate = new Date(dueAt);
    if (!trimmed) {
      setError("Enter a reminder.");
      return;
    }
    if (trimmed.length > 120) {
      setError("Reminders must be 120 characters or fewer.");
      return;
    }
    if (!dueAt || Number.isNaN(dueDate.getTime()) || dueDate.getTime() <= Date.now()) {
      setError("Choose a valid future date and time.");
      return;
    }
    setReminders((current) => [...current, {
      id: crypto.randomUUID(),
      title: trimmed,
      dueAt: dueDate.toISOString(),
      completed: false,
      notified: false,
    }].sort((a, b) => Date.parse(a.dueAt) - Date.parse(b.dueAt)));
    setTitle("");
    setDueAt("");
    setError(null);
  };

  const enableNotifications = async () => {
    if (!("Notification" in window)) {
      setError("This browser does not support notifications.");
      return;
    }
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setError("Notification permission was not granted. Reminders remain visible here.");
      } else {
        setError(null);
      }
    } catch {
      setError("Could not enable notifications. Reminders remain visible here.");
    }
  };

  if (!loaded) return <main className="flex flex-1 items-center justify-center text-sm text-white/50">Loading reminders…</main>;

  return (
    <main className="flex flex-1 justify-center overflow-y-auto px-4 py-8">
      <section className="w-full max-w-2xl space-y-6">
        <header className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Bell size={22} className="text-accent" />
            <div>
              <h1 className="text-xl font-semibold">Scheduled</h1>
              <p className="text-sm text-white/50">Create reminders. Notifications run while this app is open.</p>
            </div>
          </div>
          <button type="button" onClick={() => void enableNotifications()} className="rounded-lg border border-surface-border px-3 py-2 text-xs text-white/70 hover:bg-surface-hover">Enable notifications</button>
        </header>
        <form onSubmit={createReminder} className="space-y-3 rounded-xl border border-surface-border bg-surface-raised p-4">
          <label className="block space-y-1 text-sm text-white/70">
            Reminder
            <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} className="w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-white" placeholder="What should you remember?" />
          </label>
          <label className="block space-y-1 text-sm text-white/70">
            Date and time (your local timezone)
            <input type="datetime-local" value={dueAt} min={now ? localDateTimeValue(new Date(now + 60_000)) : undefined} onChange={(event) => setDueAt(event.target.value)} className="w-full rounded-lg border border-surface-border bg-surface px-3 py-2 text-white" />
          </label>
          {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
          <button className="rounded-lg bg-accent px-3 py-2 text-sm text-white">Add reminder</button>
        </form>
        <div className="space-y-2">
          {reminders.length === 0 ? <p className="rounded-xl border border-surface-border p-5 text-sm text-white/50">No reminders yet.</p> : reminders.map((reminder) => (
            <article key={reminder.id} className="flex items-center gap-3 rounded-xl border border-surface-border p-3">
              <input type="checkbox" checked={reminder.completed} onChange={(event) => setReminders((current) => current.map((item) => item.id === reminder.id ? { ...item, completed: event.target.checked } : item))} aria-label={`Mark ${reminder.title} complete`} />
              <div className="min-w-0 flex-1">
                <p className={`truncate text-sm ${reminder.completed ? "text-white/40 line-through" : "text-white/85"}`}>{reminder.title}</p>
                <p className="text-xs text-white/45">{new Date(reminder.dueAt).toLocaleString()}{!reminder.completed && Date.parse(reminder.dueAt) <= now ? " · Due" : ""}</p>
              </div>
              <button type="button" onClick={() => setReminders((current) => current.filter((item) => item.id !== reminder.id))} aria-label={`Delete reminder ${reminder.title}`} className="rounded p-1 text-white/45 hover:text-red-400"><Trash2 size={15} /></button>
            </article>
          ))}
        </div>
        <p className="text-xs text-white/35">Reminders are stored in this browser and are not synced or executed while the app is closed.</p>
      </section>
    </main>
  );
}
