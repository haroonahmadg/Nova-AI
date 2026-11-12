"use client";

import { useState, useRef, useEffect, KeyboardEvent, ChangeEvent } from "react";
import {
  Plus,
  Mic,
  Square,
  ArrowUp,
  Brain,
  X,
  FileText,
  Loader2,
  Paperclip,
  ScanEye,
} from "lucide-react";
import clsx from "clsx";
import Image from "next/image";
import { IconButton } from "@/components/ui/icon-button";
import { Attachment, ImageAttachment } from "@/lib/types";

const TEXTAREA_MAX_HEIGHT = 200;

interface ChatInputProps {
  onSend: (text: string, researchMode?: boolean) => void;
  disabled?: boolean;
  attachments?: Attachment[];
  onAddFiles?: (files: File[]) => void;
  onRemoveAttachment?: (id: string) => void;
  imageAttachments?: ImageAttachment[];
  onAddImages?: (files: File[]) => void;
  onRemoveImageAttachment?: (id: string) => void;
  isRecording?: boolean;
  isTranscribing?: boolean;
  onStartRecording?: () => void;
  onStopRecording?: () => Promise<string | null>;
  voiceError?: string | null;
  maxPromptChars?: number | null;
  limitsLoading?: boolean;
  limitsError?: string | null;
  prefillText?: string;
  prefillKey?: number;
}

export function ChatInput({
  onSend,
  disabled = false,
  attachments = [],
  onAddFiles,
  onRemoveAttachment,
  imageAttachments = [],
  onAddImages,
  onRemoveImageAttachment,
  isRecording = false,
  isTranscribing = false,
  onStartRecording,
  onStopRecording,
  voiceError = null,
  maxPromptChars = null,
  limitsLoading = false,
  limitsError = null,
  prefillText,
  prefillKey,
}: ChatInputProps) {
  const [value, setValue] = useState("");
  const [thinkMode, setThinkMode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [recordingSeconds, setRecordingSeconds] = useState(0);

  useEffect(() => {
    if (!isRecording) {
      return;
    }
    const timer = setInterval(() => {
      setRecordingSeconds((prev) => prev + 1);
    }, 1000);
    return () => clearInterval(timer);
  }, [isRecording]);

  const hasUploadingAttachment = attachments.some((a) => a.status === "uploading");
  const hasFailedAttachment = attachments.some((a) => a.status === "error");

  // Auto-grow the textarea as the person types multiple lines, capped
  // at TEXTAREA_MAX_HEIGHT and then scrolling, instead of staying a
  // fixed single-line height regardless of content.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, TEXTAREA_MAX_HEIGHT)}px`;
  }, [value]);

  useEffect(() => {
    if (prefillKey !== undefined && prefillText !== undefined) {
      // This effect intentionally syncs the externally-triggered edit prefill into the local draft.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setValue(prefillText);
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillKey]);

  const handleSend = () => {
    const trimmed = value.trim();
    const hasReadyAttachment = attachments.some((a) => a.status === "ready");
    const hasImages = imageAttachments.length > 0;

    if (!trimmed && !hasReadyAttachment && !hasImages) {
      return;
    }

    if (maxPromptChars !== null && trimmed.length > maxPromptChars) {
      setError(
        `Message is too long (${trimmed.length} characters). Please keep it under ${maxPromptChars} characters.`,
      );
      return;
    }

    if (hasUploadingAttachment) {
      setError("Please wait for the attachment to finish uploading.");
      return;
    }

    if (hasFailedAttachment) {
      setError(
        "An attachment failed to upload. Remove it (X) and re-upload, or remove it to send without it.",
      );
      return;
    }

    setError(null);
    onSend(trimmed, thinkMode);
    setValue("");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (files.length > 0) onAddFiles?.(files);
    event.target.value = "";
  };

  const handleImageChange = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (files.length > 0) {
      const maxImages = Number(
        process.env.NEXT_PUBLIC_MAX_IMAGES_PER_REQUEST ?? "3",
      );
      const allowedImages = Number.isInteger(maxImages) && maxImages > 0
        ? maxImages
        : 3;
      const remaining = Math.max(0, allowedImages - imageAttachments.length);
      if (files.length > remaining) {
        setError(`You can attach at most ${allowedImages} images per message.`);
      }
      if (remaining > 0) onAddImages?.(files.slice(0, remaining));
    }
    event.target.value = "";
  };

  const handleMicClick = async () => {
    if (!onStartRecording || !onStopRecording) return;

    if (isRecording) {
      const transcribed = await onStopRecording();
      if (transcribed) {
        setValue((prev) => (prev ? `${prev} ${transcribed}` : transcribed));
      }
    } else {
      setRecordingSeconds(0);
      await onStartRecording();
    }
  };

  const canSend =
    !disabled &&
    (value.trim().length > 0 ||
      attachments.some((a) => a.status === "ready") ||
      imageAttachments.length > 0);

  return (
    <div className="w-full">
      {isRecording && (
        <div className="mb-2 flex items-center gap-2 rounded-xl border border-surface-border bg-surface-raised px-3 py-2">
          <div className="flex items-center gap-0.5" aria-hidden="true">
            {[0, 1, 2, 3, 4].map((i) => (
              <span
                key={i}
                className="w-1 rounded-full bg-red-400"
                style={{
                  height: "14px",
                  animation: `recording-wave 0.9s ease-in-out ${i * 0.12}s infinite`,
                }}
              />
            ))}
          </div>
          <span className="text-xs text-white/70">
            Recording… {Math.floor(recordingSeconds / 60)}:{(recordingSeconds % 60).toString().padStart(2, "0")}
          </span>
          <style jsx>{`
            @keyframes recording-wave {
              0%, 100% { transform: scaleY(0.4); }
              50% { transform: scaleY(1); }
            }
          `}</style>
        </div>
      )}

      {(attachments.length > 0 || imageAttachments.length > 0) && (
        <div className="mb-2 flex flex-wrap gap-2">
          {imageAttachments.map((img) => (
            <div
              key={img.id}
              className="flex items-center gap-1.5 rounded-lg border border-surface-border bg-surface-raised px-2 py-1 text-xs text-white/80"
              title={img.filename}
            >
              <Image
                src={img.previewUrl}
                alt={img.filename}
                width={24}
                height={24}
                unoptimized
                className="h-6 w-6 rounded object-cover"
              />
              <span className="max-w-[120px] truncate">{img.filename}</span>
              <button
                onClick={() => onRemoveImageAttachment?.(img.id)}
                aria-label={`Remove ${img.filename}`}
                className="text-white/50 hover:text-white"
              >
                <X size={12} />
              </button>
            </div>
          ))}

          {attachments.map((a) => (
            <div
              key={a.id}
              className={clsx(
                "flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs",
                a.status === "error"
                  ? "border-red-400/40 bg-red-400/10 text-red-400"
                  : "border-surface-border bg-surface-raised text-white/80",
              )}
            >
              {a.status === "uploading" ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <FileText size={12} />
              )}
              <span className="max-w-[140px] truncate">{a.filename}</span>
              {a.status === "error" && a.error && (
                <span className="max-w-[220px] truncate text-red-300">
                  — {a.error}
                </span>
              )}
              <button
                onClick={() => onRemoveAttachment?.(a.id)}
                aria-label={`Remove ${a.filename}`}
                className="text-white/50 hover:text-white"
              >
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
      )}

      <div
        className={clsx(
          "flex items-end gap-2 rounded-[28px] border border-[#414141] bg-[#1c1c1c] px-3 py-2.5 shadow-sm transition-colors focus-within:border-[#555555]",
          disabled && "opacity-60",
        )}
      >
        <input ref={fileInputRef} type="file" multiple hidden onChange={handleFileChange} />
        <input
          ref={imageInputRef}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={handleImageChange}
        />

        <div className="relative" ref={menuRef}>
          <IconButton
            aria-label="Add attachment"
            disabled={disabled}
            onClick={() => setMenuOpen((prev) => !prev)}
          >
            <Plus size={18} />
          </IconButton>

          {menuOpen && (
            <>
              <div
                className="fixed inset-0 z-10"
                onClick={() => setMenuOpen(false)}
              />
              <div className="absolute bottom-full left-0 z-20 mb-2 w-48 rounded-xl border border-surface-border bg-surface-raised py-1 shadow-lg">
                <button
                  onClick={() => {
                    setMenuOpen(false);
                    fileInputRef.current?.click();
                  }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-white/90 hover:bg-surface-hover"
                >
                  <Paperclip size={15} />
                  Upload files
                </button>
                <button
                  onClick={() => {
                    setMenuOpen(false);
                    imageInputRef.current?.click();
                  }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-white/90 hover:bg-surface-hover"
                >
                  <ScanEye size={15} />
                  Image analyzer
                </button>
              </div>
            </>
          )}
        </div>

        <textarea
          ref={textareaRef}
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            if (error) setError(null);
          }}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder="Ask Nova GPT"
          rows={1}
          style={{ maxHeight: TEXTAREA_MAX_HEIGHT }}
          className="min-w-0 flex-1 resize-none overflow-y-auto bg-transparent py-1.5 text-[15px] leading-6 text-white placeholder:text-white/45 focus:outline-none disabled:cursor-not-allowed"
        />

        <button
          onClick={() => setThinkMode((prev) => !prev)}
          disabled={disabled}
          className={clsx(
            "flex flex-shrink-0 items-center gap-1.5 rounded-full px-2.5 py-2 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50",
            thinkMode
              ? "bg-white/10 text-white"
              : "text-white/65 hover:bg-white/5 hover:text-white",
          )}
          title="Toggle extended reasoning"
        >
          <Brain size={18} />
          Think
        </button>

        <IconButton
          disabled={disabled || isTranscribing}
          onClick={handleMicClick}
          aria-label={
            isTranscribing
              ? "Transcribing recording"
              : isRecording
                ? "Stop recording"
                : "Record a voice message"
          }
        >
          {isTranscribing ? (
            <Loader2 size={18} className="animate-spin" />
          ) : isRecording ? (
            <Square size={18} />
          ) : (
            <Mic size={18} />
          )}
        </IconButton>

        <button
          onClick={handleSend}
          disabled={disabled || !canSend}
          aria-label={disabled ? "Processing" : "Send"}
          className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-[#3b82f6] text-white transition-colors hover:bg-[#5390f8] disabled:cursor-not-allowed disabled:bg-[#3b82f6]/45 disabled:opacity-70"
        >
          {disabled ? (
            <Square size={14} fill="currentColor" />
          ) : (
            <ArrowUp size={18} />
          )}
        </button>
      </div>

      {error && <p className="mt-2 px-2 text-xs text-red-400">{error}</p>}
      {limitsLoading && (
        <p className="mt-2 px-2 text-xs text-white/50" role="status">
          Loading message limit…
        </p>
      )}
      {limitsError && (
        <p className="mt-2 px-2 text-xs text-amber-300" role="status">
          {limitsError} The server will validate message length.
        </p>
      )}
      {voiceError && <p className="mt-2 px-2 text-xs text-red-400">{voiceError}</p>}
    </div>
  );
}