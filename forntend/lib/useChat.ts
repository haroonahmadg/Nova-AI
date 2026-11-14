"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  listThreads,
  getThreadMessages,
  createThread,
  streamMessage,
  pinThread,
  unpinThread,
  deleteThread as apiDeleteThread,
  uploadFiles,
  transcribeAudio,
  speakText,
  exportThread as apiExportThread,
  editLastMessage as apiEditLastMessage,
  renameThread as apiRenameThread,
  archiveThread as apiArchiveThread,
  unarchiveThread as apiUnarchiveThread,
  fetchGeneratedImage,
} from "./api";
import {
  ChatMessage,
  ThreadSummary,
  ApiError,
  Attachment,
  ImageAttachment,
  FilePayload,
} from "./types";
import { getStoredToken } from "./useAuth";

export type VoiceStatus = { messageId: string; state: "loading" | "playing" } | null;

let attachmentIdSequence = 0;

type RetryAttachments = {
  files: FilePayload[];
  images: { image_bytes: string; image_type: string; filename: string }[];
};

type LastTurnRequest = RetryAttachments & {
  threadId: string;
  userMessageId: string;
  content: string;
  researchMode: boolean;
};

function createAttachmentId(kind: "upload" | "image"): string {
  attachmentIdSequence += 1;
  return `${kind}-${Date.now()}-${attachmentIdSequence}`;
}

export function useChat() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [threadsError, setThreadsError] = useState<string | null>(null);

  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const activeThreadIdRef = useRef<string | null>(null);
  activeThreadIdRef.current = activeThreadId;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [activityLabel, setActivityLabel] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [imageAttachments, setImageAttachments] = useState<ImageAttachment[]>([]);
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus>(null);

  const abortRef = useRef<{ abort: () => void } | null>(null);
  const threadLoadRequestRef = useRef(0);
  const threadLoadAbortRef = useRef<AbortController | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioElRef = useRef<HTMLAudioElement | null>(null);
  const audioObjectUrlRef = useRef<string | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const audioBytesRef = useRef(0);
  const recordingLimitExceededRef = useRef(false);
  const recordingStartRef = useRef(false);
  const mountedRef = useRef(false);
  const speakAbortRef = useRef<AbortController | null>(null);
  const sessionTokenRef = useRef<string | null>(null);
  const generatedObjectUrlsRef = useRef<Set<string>>(new Set());
  const lastTurnRequestRef = useRef<LastTurnRequest | null>(null);

  const clearGeneratedObjectUrls = useCallback(() => {
    generatedObjectUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    generatedObjectUrlsRef.current.clear();
  }, []);

  // -------------------- Letter-by-letter streaming reveal --------------------
  // Incoming "text" SSE events can arrive in large chunks; revealing a
  // chunk all at once looks like a hardcoded jump rather than real
  // streaming. These buffer incoming text per message and drip it out
  // a couple of characters at a time on a timer instead.
  const pendingTextRef = useRef<Record<string, string>>({});
  const revealTimerRef = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  const startReveal = useCallback((messageId: string) => {
    if (revealTimerRef.current[messageId]) return; // already running

    revealTimerRef.current[messageId] = setInterval(() => {
      const pending = pendingTextRef.current[messageId] ?? "";
      if (!pending) return;

      const chunk = pending.slice(0, 2);
      pendingTextRef.current[messageId] = pending.slice(2);

      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId ? { ...m, content: m.content + chunk } : m,
        ),
      );
    }, 15);
  }, []);

  const flushReveal = useCallback((messageId: string) => {
    const interval = revealTimerRef.current[messageId];
    if (interval) {
      clearInterval(interval);
      delete revealTimerRef.current[messageId];
    }

    const remaining = pendingTextRef.current[messageId];
    if (remaining) {
      delete pendingTextRef.current[messageId];
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId ? { ...m, content: m.content + remaining } : m,
        ),
      );
    }
  }, []);

  const stopSpeaking = useCallback(() => {
    speakAbortRef.current?.abort();
    audioElRef.current?.pause();
    audioElRef.current = null;
    if (audioObjectUrlRef.current) {
      URL.revokeObjectURL(audioObjectUrlRef.current);
      audioObjectUrlRef.current = null;
    }
    setVoiceStatus(null);
  }, []);

  const resetChatState = useCallback(() => {
    lastTurnRequestRef.current = null;
    threadLoadRequestRef.current += 1;
    threadLoadAbortRef.current?.abort();
    threadLoadAbortRef.current = null;
    abortRef.current?.abort();
    stopSpeaking();
    setIsStreaming(false);
    setActivityLabel(null);
    setSendError(null);
    setThreads([]);
    setThreadsLoading(true);
    setThreadsError(null);
    setActiveThreadId(null);
    clearGeneratedObjectUrls();
    setMessages([]);
    setMessagesLoading(false);
    setAttachments([]);
    setImageAttachments((prev) => {
      prev.forEach((img) => URL.revokeObjectURL(img.previewUrl));
      return [];
    });
    setVoiceError(null);
    setVoiceStatus(null);
  }, [clearGeneratedObjectUrls, stopSpeaking]);

  const refreshThreads = useCallback(async (signal?: AbortSignal) => {
    setThreadsLoading(true);
    setThreadsError(null);
    try {
      const result = await listThreads(signal);
      setThreads(result);
    } catch (err) {
      if (signal?.aborted) return;
      setThreadsError(
        err instanceof ApiError
          ? err.message
          : "Couldn't load your chats. Is the backend running?",
      );
    } finally {
      if (!signal?.aborted) setThreadsLoading(false);
    }
  }, []);

  useEffect(() => {
    const token = getStoredToken();
    const currentSessionKey = token ?? "logged-out";

    if (sessionTokenRef.current !== currentSessionKey) {
      sessionTokenRef.current = currentSessionKey;
      resetChatState();
      const controller = new AbortController();
      void refreshThreads(controller.signal);
      return () => controller.abort();
    }

    const controller = new AbortController();
    refreshThreads(controller.signal);
    return () => controller.abort();
  }, [refreshThreads, resetChatState]);

  const selectThread = useCallback(async (threadId: string) => {
    lastTurnRequestRef.current = null;
    const requestId = ++threadLoadRequestRef.current;
    threadLoadAbortRef.current?.abort();
    const controller = new AbortController();
    threadLoadAbortRef.current = controller;
    abortRef.current?.abort();
    stopSpeaking();
    setIsStreaming(false);
    setActivityLabel(null);
    setSendError(null);
    setAttachments([]);
    setImageAttachments((prev) => {
      prev.forEach((img) => URL.revokeObjectURL(img.previewUrl));
      return [];
    });

    setActiveThreadId(threadId);
    setMessagesLoading(true);
    clearGeneratedObjectUrls();
    setMessages([]);

    try {
      const history = await getThreadMessages(threadId, controller.signal);
      if (threadLoadRequestRef.current === requestId) {
        const historyWithImages = await Promise.all(
          history.map(async (message) => {
            if (!message.imageUrl?.startsWith("/generated-images/")) {
              return message;
            }
            try {
              const blob = await fetchGeneratedImage(
                message.imageUrl,
                controller.signal,
              );
              const imageUrl = URL.createObjectURL(blob);
              if (threadLoadRequestRef.current !== requestId) {
                URL.revokeObjectURL(imageUrl);
                return message;
              }
              generatedObjectUrlsRef.current.add(imageUrl);
              return { ...message, imageUrl };
            } catch (err) {
              if (controller.signal.aborted) throw err;
              return {
                ...message,
                imageUrl: undefined,
                content: "[Generated image could not be loaded]",
              };
            }
          }),
        );
        if (threadLoadRequestRef.current === requestId) {
          setMessages(historyWithImages);
        }
      }
    } catch (err) {
      if (threadLoadRequestRef.current === requestId) {
        setSendError(
          err instanceof ApiError
            ? err.message
            : "Couldn't load this conversation.",
        );
      }
    } finally {
      if (threadLoadRequestRef.current === requestId) {
        setMessagesLoading(false);
      }
    }
  }, [clearGeneratedObjectUrls, stopSpeaking]);

  const startNewChat = useCallback(() => {
    lastTurnRequestRef.current = null;
    threadLoadRequestRef.current += 1;
    threadLoadAbortRef.current?.abort();
    threadLoadAbortRef.current = null;
    abortRef.current?.abort();
    stopSpeaking();
    setIsStreaming(false);
    setActivityLabel(null);
    setSendError(null);
    setAttachments([]);
    setImageAttachments((prev) => {
      prev.forEach((img) => URL.revokeObjectURL(img.previewUrl));
      return [];
    });
    clearGeneratedObjectUrls();
    setActiveThreadId(null);
    setMessages([]);
  }, [clearGeneratedObjectUrls, stopSpeaking]);

  // -------------------- Document attachments (RAG) --------------------

  const addAttachments = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;

      let threadId = activeThreadId;
      if (!threadId) {
        try {
          const created = await createThread();
          threadId = created.threadId;
          setActiveThreadId(threadId);
          setThreads((prev) => [created, ...prev]);
        } catch (err) {
          setSendError(
            err instanceof ApiError
              ? err.message
              : "Couldn't start a new chat for this upload.",
          );
          return;
        }
      }

      const pending: Attachment[] = files.map((f) => ({
        id: createAttachmentId("upload"),
        filename: f.name,
        filePath: null,
        status: "uploading",
      }));
      setAttachments((prev) => [...prev, ...pending]);

      try {
        const results = await uploadFiles(threadId, files);

        setAttachments((prev) => {
          const next = [...prev];
          results.forEach((result, i) => {
            const idx = next.findIndex((a) => a.id === pending[i]?.id);
            if (idx === -1) return;
            next[idx] = result.error
              ? { ...next[idx], status: "error", error: result.error }
              : { ...next[idx], status: "ready", filePath: result.file_path };
          });
          return next;
        });
      } catch (err) {
        setAttachments((prev) =>
          prev.map((a) =>
            pending.some((p) => p.id === a.id)
              ? {
                  ...a,
                  status: "error",
                  error: err instanceof ApiError ? err.message : "Upload failed.",
                }
              : a,
          ),
        );
      }
    },
    [activeThreadId],
  );

  const removeAttachment = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }, []);

  // -------------------- Image attachments (vision) --------------------

  const addImageAttachment = useCallback((file: File): Promise<void> => {
    return new Promise((resolve) => {
      const reader = new FileReader();

      reader.onload = () => {
        const result = reader.result as string;
        const commaIdx = result.indexOf(",");
        const imageBytes = commaIdx >= 0 ? result.slice(commaIdx + 1) : result;

        setImageAttachments((prev) => [
          ...prev,
          {
            id: createAttachmentId("image"),
            filename: file.name,
            imageBytes,
            imageType: file.type || "image/png",
            previewUrl: URL.createObjectURL(file),
          },
        ]);
        resolve();
      };

      reader.onerror = () => resolve();
      reader.readAsDataURL(file);
    });
  }, []);

  const addImageAttachments = useCallback(
    async (files: File[]) => {
      const images = files.filter((f) => f.type.startsWith("image/"));
      if (images.length === 0) return;
      await Promise.all(images.map(addImageAttachment));
    },
    [addImageAttachment],
  );

  const removeImageAttachment = useCallback((id: string) => {
    setImageAttachments((prev) => {
      const target = prev.find((i) => i.id === id);
      if (target) URL.revokeObjectURL(target.previewUrl);
      return prev.filter((i) => i.id !== id);
    });
  }, []);

  // -------------------- Send / stream --------------------

  const sendMessage = useCallback(
    async (
      text: string,
      researchMode = false,
      retryRequest?: LastTurnRequest,
    ) => {
      const trimmed = text.trim();
      const readyFiles = attachments.filter(
        (a) => a.status === "ready" && a.filePath,
      );

      if (!trimmed && readyFiles.length === 0 && imageAttachments.length === 0) {
        return;
      }

      setSendError(null);
      let threadId = activeThreadId;

      if (!threadId) {
        try {
          const created = await createThread();
          threadId = created.threadId;
          setActiveThreadId(threadId);
          setThreads((prev) => [created, ...prev]);
        } catch (err) {
          setSendError(
            err instanceof ApiError
              ? err.message
              : "Couldn't start a new chat. Is the backend running?",
          );
          return;
        }
      }

      const filePayloads: FilePayload[] =
        retryRequest?.files ??
        readyFiles.map((a) => ({
          filename: a.filename,
          file_path: a.filePath as string,
        }));

      const imagePayloads =
        retryRequest?.images ??
        imageAttachments.map((img) => ({
          image_bytes: img.imageBytes,
          image_type: img.imageType,
          filename: img.filename,
        }));

      const attachmentNames = [
        ...filePayloads.map((file) => file.filename),
        ...imagePayloads.map((image) => image.filename),
      ];
      const displaySuffix =
        attachmentNames.length > 0
          ? `\n\n[Attached: ${attachmentNames.join(", ")}]`
          : "";

      const userMessage: ChatMessage = {
        id: `local-${Date.now()}-user`,
        role: "user",
        content: trimmed + displaySuffix,
        researchMode,
      };
      const assistantMessage: ChatMessage = {
        id: `local-${Date.now()}-assistant`,
        role: "assistant",
        content: "",
        streaming: true,
      };

      lastTurnRequestRef.current = {
        threadId,
        userMessageId: userMessage.id,
        content: trimmed,
        files: filePayloads,
        images: imagePayloads,
        researchMode,
      };

      setMessages((prev) => [...prev, userMessage, assistantMessage]);
      setAttachments([]);
      imageAttachments.forEach((img) => URL.revokeObjectURL(img.previewUrl));
      setImageAttachments([]);
      setIsStreaming(true);

      // Tracks whether real assistant text has started arriving for this
      // turn, so the first "text" event can clear any stale activity
      // label (e.g. "Searching the web") exactly once — without this,
      // a tool-call label from early in the turn stays displayed
      // underneath the reply even after real content starts streaming.
      let textStarted = false;

      abortRef.current = streamMessage(
        threadId,
        trimmed,
        filePayloads,
        imagePayloads,
        (event) => {
          if (event.type === "text") {
            if (!textStarted) {
              textStarted = true;
              setActivityLabel(null);
            }
            pendingTextRef.current[assistantMessage.id] =
              (pendingTextRef.current[assistantMessage.id] ?? "") + event.text;
            startReveal(assistantMessage.id);
          } else if (event.type === "plan") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessage.id
                  ? { ...m, plan: { steps: event.steps, completed: 0 } }
                  : m,
              ),
            );
          } else if (event.type === "image") {
            const sessionToken = getStoredToken();
            void fetchGeneratedImage(event.url)
              .then((blob) => {
                const imageUrl = URL.createObjectURL(blob);
                if (
                  !mountedRef.current ||
                  sessionToken !== getStoredToken() ||
                  !messagesRef.current.some(
                    (message) => message.id === assistantMessage.id,
                  )
                ) {
                  URL.revokeObjectURL(imageUrl);
                  return;
                }
                generatedObjectUrlsRef.current.add(imageUrl);
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMessage.id ? { ...m, imageUrl } : m,
                  ),
                );
              })
              .catch((err) => {
                setSendError(
                  err instanceof ApiError ? err.message : "Could not load the generated image.",
                );
              });
          } else if (event.type === "citations") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessage.id
                  ? { ...m, citations: event.items }
                  : m,
              ),
            );
          } else if (event.type === "activity") {
            setMessages((prev) =>
              prev.map((m) => {
                if (m.id === assistantMessage.id && m.plan) {
                  const currentCompleted = m.plan.completed ?? 0;
                  const nextCompleted = Math.min(
                    m.plan.steps.length,
                    currentCompleted + 1,
                  );
                  return {
                    ...m,
                    plan: { ...m.plan, completed: nextCompleted },
                  };
                }
                return m;
              }),
            );
            if (!textStarted) {
              setActivityLabel(event.message);
            }
          } else if (event.type === "done") {
            flushReveal(assistantMessage.id);
            setIsStreaming(false);
            setActivityLabel(null);
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessage.id
                  ? {
                      ...m,
                      streaming: false,
                      ...(m.plan
                        ? {
                            plan: {
                              ...m.plan,
                              completed: m.plan.steps.length,
                            },
                          }
                        : {}),
                    }
                  : m,
              ),
            );
            refreshThreads();
          } else if (event.type === "error") {
            flushReveal(assistantMessage.id);
            setIsStreaming(false);
            setActivityLabel(null);
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMessage.id ? { ...m, streaming: false } : m,
              ),
            );
            setSendError(event.message);
          }
        },
        (err) => {
          flushReveal(assistantMessage.id);
          setIsStreaming(false);
          setActivityLabel(null);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessage.id ? { ...m, streaming: false } : m,
            ),
          );
          setSendError(err.message || "The connection to the assistant was lost.");
        },
        researchMode,
      );
    },
    [activeThreadId, attachments, imageAttachments, refreshThreads, startReveal, flushReveal],
  );

  // -------------------- Archive --------------------

  const toggleArchive = useCallback(
    async (threadId: string) => {
      const target = threads.find((t) => t.threadId === threadId);
      if (!target) return;

      const prevThreads = threads;
      const nextArchived = !target.archived;

      setThreads((prev) =>
        prev.map((t) =>
          t.threadId === threadId ? { ...t, archived: nextArchived } : t,
        ),
      );

      try {
        const archived = nextArchived
          ? await apiArchiveThread(threadId)
          : await apiUnarchiveThread(threadId);

        setThreads((prev) =>
          prev.map((t) => (t.threadId === threadId ? { ...t, archived } : t)),
        );
      } catch {
        setThreads(prevThreads);
        setThreadsError("Couldn't update that chat — please try again.");
      }
    },
    [threads],
  );

  // -------------------- Pin / delete --------------------

  const togglePin = useCallback(
    async (threadId: string) => {
      const target = threads.find((t) => t.threadId === threadId);
      if (!target) return;

      const prevThreads = threads;
      const nextPinned = !target.pinned;

      setThreads((prev) =>
        prev.map((t) =>
          t.threadId === threadId ? { ...t, pinned: nextPinned } : t,
        ),
      );

      try {
        const pinned = nextPinned
          ? await pinThread(threadId)
          : await unpinThread(threadId);

        setThreads((prev) =>
          prev.map((t) => (t.threadId === threadId ? { ...t, pinned } : t)),
        );
      } catch {
        setThreads(prevThreads);
        setThreadsError("Couldn't update pin — please try again.");
      }
    },
    [threads],
  );

  const removeThread = useCallback(
    async (threadId: string) => {
      try {
        await apiDeleteThread(threadId);
        setThreads((prev) => prev.filter((t) => t.threadId !== threadId));
        if (activeThreadIdRef.current === threadId) startNewChat();
      } catch {
        setThreadsError("Couldn't delete that chat — please try again.");
      }
    },
    [startNewChat],
  );

  const renameThread = useCallback(
    async (threadId: string, title: string) => {
      const trimmed = title.trim();
      if (!trimmed) return;

      const prevThreads = threads;

      setThreads((prev) =>
        prev.map((t) => (t.threadId === threadId ? { ...t, title: trimmed } : t)),
      );

      try {
        const updated = await apiRenameThread(threadId, trimmed);
        setThreads((prev) =>
          prev.map((t) => (t.threadId === threadId ? updated : t)),
        );
      } catch (err) {
        setThreads(prevThreads);
        setThreadsError(
          err instanceof ApiError ? err.message : "Couldn't rename that chat.",
        );
      }
    },
    [threads],
  );

  // -------------------- Voice input --------------------

  const startRecording = useCallback(async () => {
    if (recordingStartRef.current || isRecording || isTranscribing) return;
    recordingStartRef.current = true;
    setVoiceError(null);
    setIsTranscribing(false);

    if (
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === "undefined"
    ) {
      setVoiceError("Voice input isn't supported in this browser.");
      recordingStartRef.current = false;
      return;
    }

    let stream: MediaStream | null = null;
    try {
      const mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      stream = mediaStream;
      if (!mountedRef.current) {
        mediaStream.getTracks().forEach((track) => track.stop());
        return;
      }

      const mimeTypeCandidates = [
        "audio/webm;codecs=opus",
        "audio/ogg;codecs=opus",
        "audio/mp4;codecs=mp4a.40.2",
        "audio/webm",
        "audio/ogg",
        "audio/mp4",
      ];
      const supportedMimeType = mimeTypeCandidates.find((mimeType) =>
        MediaRecorder.isTypeSupported?.(mimeType),
      );
      const recorder = supportedMimeType
        ? new MediaRecorder(mediaStream, { mimeType: supportedMimeType })
        : new MediaRecorder(mediaStream);
      audioChunksRef.current = [];
      audioBytesRef.current = 0;
      recordingLimitExceededRef.current = false;

      recorder.ondataavailable = (e) => {
        if (e.data.size === 0 || recordingLimitExceededRef.current) return;
        const maxAudioBytes = 10 * 1024 * 1024;
        if (audioBytesRef.current + e.data.size > maxAudioBytes) {
          recordingLimitExceededRef.current = true;
          audioChunksRef.current = [];
          audioBytesRef.current = 0;
          setVoiceError("Recording exceeds the 10 MB limit. Please record a shorter message.");
          if (recorder.state !== "inactive") recorder.stop();
          mediaStream.getTracks().forEach((track) => track.stop());
          if (mediaRecorderRef.current === recorder) {
            mediaRecorderRef.current = null;
          }
          setIsRecording(false);
          return;
        }
        audioChunksRef.current.push(e.data);
        audioBytesRef.current += e.data.size;
      };
      recorder.onerror = () => {
        stream?.getTracks().forEach((track) => track.stop());
        if (mediaRecorderRef.current === recorder) {
          mediaRecorderRef.current = null;
        }
        setIsRecording(false);
        setVoiceError("Recording failed. Check your microphone and try again.");
      };

      recorder.start();
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
    } catch (error) {
      stream?.getTracks().forEach((track) => track.stop());
      const errorName = error instanceof DOMException ? error.name : "";
      setVoiceError(
        errorName === "NotFoundError"
          ? "No microphone was found. Connect a microphone and try again."
          : errorName === "NotAllowedError" || errorName === "SecurityError"
            ? "Microphone access was denied. Allow microphone access and try again."
            : "Couldn't start recording. Check microphone permissions and try again.",
      );
    } finally {
      recordingStartRef.current = false;
    }
  }, [isRecording, isTranscribing]);

  const stopRecording = useCallback((): Promise<string | null> => {
    return new Promise((resolve) => {
      const recorder = mediaRecorderRef.current;
      if (!recorder || recorder.state === "inactive") {
        setIsRecording(false);
        recorder?.stream.getTracks().forEach((track) => track.stop());
        mediaRecorderRef.current = null;
        resolve(null);
        return;
      }

      let completed = false;
      const finish = (text: string | null) => {
        if (completed) return;
        completed = true;
        resolve(text);
      };

      recorder.onstop = async () => {
        if (completed) return;
        recorder.stream.getTracks().forEach((t) => t.stop());
        if (mediaRecorderRef.current === recorder) {
          mediaRecorderRef.current = null;
        }
        setIsRecording(false);

        const type =
          recorder.mimeType ||
          audioChunksRef.current.find((chunk) => chunk.type)?.type ||
          "audio/webm";
        if (recordingLimitExceededRef.current) {
          audioChunksRef.current = [];
          audioBytesRef.current = 0;
          setVoiceError("Recording exceeds the 10 MB limit. Please record a shorter message.");
          finish(null);
          return;
        }
        const blob = new Blob(audioChunksRef.current, { type });
        audioChunksRef.current = [];
        audioBytesRef.current = 0;

        if (blob.size === 0) {
          setVoiceError("No audio was captured — please try again.");
          finish(null);
          return;
        }

        const extensionByMimeType: Record<string, string> = {
          "audio/webm": "webm",
          "audio/ogg": "ogg",
          "audio/mp4": "mp4",
          "audio/mpeg": "mp3",
          "audio/wav": "wav",
          "audio/x-wav": "wav",
        };
        const normalizedType = type.split(";")[0].trim().toLowerCase();
        const filename = `recording.${extensionByMimeType[normalizedType] ?? "webm"}`;
        setIsTranscribing(true);
        try {
          const text = (await transcribeAudio(blob, filename)).trim();
          if (!text) {
            setVoiceError("No speech was recognized. Please try again.");
            finish(null);
          } else {
            finish(text);
          }
        } catch (err) {
          setVoiceError(
            err instanceof ApiError
              ? err.message
              : "Couldn't transcribe that recording.",
          );
          finish(null);
        } finally {
          setIsTranscribing(false);
        }
      };

      recorder.onerror = () => {
        recorder.stream.getTracks().forEach((track) => track.stop());
        if (mediaRecorderRef.current === recorder) {
          mediaRecorderRef.current = null;
        }
        setIsRecording(false);
        setVoiceError("Recording failed. Check your microphone and try again.");
        finish(null);
      };

      try {
        recorder.stop();
      } catch {
        recorder.stream.getTracks().forEach((track) => track.stop());
        if (mediaRecorderRef.current === recorder) {
          mediaRecorderRef.current = null;
        }
        setIsRecording(false);
        setVoiceError("Could not stop the recording. Please try again.");
        finish(null);
      }
    });
  }, []);

  // -------------------- Voice output (Listen) — toggle start/stop --------------------

  const speakMessage = useCallback(
    async (messageId: string, text: string) => {
      if (!text.trim()) return;

      if (voiceStatus?.messageId === messageId) {
        stopSpeaking();
        return;
      }

      stopSpeaking();
      setVoiceError(null);
      setVoiceStatus({ messageId, state: "loading" });

      const controller = new AbortController();
      speakAbortRef.current = controller;

      try {
        const blob = await speakText(text, controller.signal);
        if (controller.signal.aborted) return;

        const url = URL.createObjectURL(blob);
        audioObjectUrlRef.current = url;
        const audio = new Audio(url);
        audioElRef.current = audio;
        setVoiceStatus({ messageId, state: "playing" });

        audio.onended = () => {
          if (audioObjectUrlRef.current !== url) return;
          audioObjectUrlRef.current = null;
          audioElRef.current = null;
          setVoiceStatus(null);
          URL.revokeObjectURL(url);
        };
        audio.onerror = () => {
          if (audioObjectUrlRef.current !== url) return;
          audioObjectUrlRef.current = null;
          audioElRef.current = null;
          setVoiceStatus(null);
          URL.revokeObjectURL(url);
          setVoiceError("Couldn't play the generated audio.");
        };

        await audio.play();
      } catch (err) {
        if (controller.signal.aborted) return;
        if (audioObjectUrlRef.current) {
          URL.revokeObjectURL(audioObjectUrlRef.current);
          audioObjectUrlRef.current = null;
          audioElRef.current = null;
        }
        setVoiceStatus(null);
        setVoiceError(
          err instanceof ApiError ? err.message : "Couldn't generate audio.",
        );
      }
    },
    [voiceStatus, stopSpeaking],
  );

  // -------------------- Export --------------------

  const exportThreadById = useCallback(async (threadId: string) => {
    try {
      await apiExportThread(threadId);
    } catch (err) {
      setSendError(
        err instanceof ApiError ? err.message : "Couldn't export this chat.",
      );
    }
  }, []);

  const exportCurrentThread = useCallback(() => {
    if (activeThreadId) void exportThreadById(activeThreadId);
  }, [activeThreadId, exportThreadById]);

  // -------------------- Edit last message (prefill, don't resend) --------------------

  const editLastMessage = useCallback(async (): Promise<string | null> => {
    if (!activeThreadId) return null;

    const lastUserMessage = [...messages].reverse().find((m) => m.role === "user");

    try {
      const removed = await apiEditLastMessage(activeThreadId);
      if (!removed) return null;

      lastTurnRequestRef.current = null;
      setMessages((prev) => prev.slice(0, -2));
      return lastUserMessage?.content ?? "";
    } catch (err) {
      setSendError(
        err instanceof ApiError ? err.message : "Couldn't edit that message.",
      );
      return null;
    }
  }, [activeThreadId, messages]);

  // -------------------- Retry last message (resend immediately) --------------------
  //
  // Shares the exact same backend removal call as editLastMessage
  // (POST /threads/{id}/edit -> remove_last_turn), but instead of
  // handing the text back to prefill the input box, it immediately
  // resends the original request, including its attachments when they
  // are still available in this session.

  const retryLastMessage = useCallback(async (): Promise<void> => {
    if (!activeThreadId || isStreaming) return;

    const lastUserMessage = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUserMessage) return;

    const previousRequest = lastTurnRequestRef.current;
    const canRestoreAttachments =
      previousRequest?.threadId === activeThreadId &&
      previousRequest.userMessageId === lastUserMessage.id;
    const hasAttachmentMarker = lastUserMessage.content.includes("\n\n[Attached: ");
    if (hasAttachmentMarker && !canRestoreAttachments) {
      setSendError(
        "The original attachments are no longer available to retry. Please reattach them and send the message again.",
      );
      return;
    }

    try {
      const removed = await apiEditLastMessage(activeThreadId);
      if (!removed) return;

      setMessages((prev) => prev.slice(0, -2));
      lastTurnRequestRef.current = null;
      await sendMessage(
        canRestoreAttachments ? previousRequest.content : lastUserMessage.content,
        canRestoreAttachments
          ? previousRequest.researchMode
          : lastUserMessage.researchMode ?? false,
        canRestoreAttachments ? previousRequest : undefined,
      );
    } catch (err) {
      setSendError(
        err instanceof ApiError ? err.message : "Couldn't retry that message.",
      );
    }
  }, [activeThreadId, isStreaming, messages, sendMessage]);

  useEffect(() => {
    mountedRef.current = true;
    const revealTimers = revealTimerRef.current;
    const generatedObjectUrls = generatedObjectUrlsRef.current;
    return () => {
      mountedRef.current = false;
      threadLoadAbortRef.current?.abort();
      abortRef.current?.abort();
      audioElRef.current?.pause();
      if (audioObjectUrlRef.current) {
        URL.revokeObjectURL(audioObjectUrlRef.current);
        audioObjectUrlRef.current = null;
      }
      speakAbortRef.current?.abort();
      generatedObjectUrls.forEach((url) => URL.revokeObjectURL(url));
      generatedObjectUrls.clear();
      const recorder = mediaRecorderRef.current;
      if (recorder) {
        recorder.stream.getTracks().forEach((track) => track.stop());
        if (recorder.state !== "inactive") recorder.stop();
        mediaRecorderRef.current = null;
      }
      audioChunksRef.current = [];
      audioBytesRef.current = 0;
      Object.values(revealTimers).forEach(clearInterval);
    };
  }, []);

  return {
    threads,
    threadsLoading,
    threadsError,
    refreshThreads,
    activeThreadId,
    messages,
    messagesLoading,
    isStreaming,
    activityLabel,
    sendError,
    resetChatState,
    selectThread,
    startNewChat,
    sendMessage,
    togglePin,
    removeThread,
    renameThread,
    toggleArchive,
    attachments,
    addAttachments,
    removeAttachment,
    imageAttachments,
    addImageAttachments,
    removeImageAttachment,
    isRecording,
    isTranscribing,
    voiceError,
    startRecording,
    stopRecording,
    voiceStatus,
    speakMessage,
    stopSpeaking,
    exportCurrentThread,
    exportThreadById,
    editLastMessage,
    retryLastMessage,
  };
}