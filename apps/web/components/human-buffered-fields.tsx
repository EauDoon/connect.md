"use client";

import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { Input, Textarea } from "@/components/ui/field";
import {
  commitBufferedInputValue,
  createBufferedCommitter,
  createBufferedInputCommitter,
  nextBufferedInputValue,
} from "@/lib/human-input";

export type BufferedFlush = () => boolean;
export type BufferedFlushRegistry = (flush: BufferedFlush) => () => void;

export const BufferedCommitRegistry =
  createContext<BufferedFlushRegistry | null>(null);

export function FieldLabel({
  htmlFor,
  children,
}: {
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <label
      htmlFor={htmlFor}
      className="mb-1.5 block text-sm font-medium text-white"
    >
      {children}
    </label>
  );
}

type BufferedInputProps = {
  id: string;
  value: string;
  onCommit: (value: string) => void;
  disabled?: boolean;
  maxLength: number;
  placeholder: string;
  className?: string;
  autoComplete?: string;
  describedBy?: string;
};

export function BufferedInput({
  id,
  value,
  onCommit,
  disabled = false,
  maxLength,
  placeholder,
  className,
  autoComplete,
  describedBy,
}: BufferedInputProps) {
  const [draftValue, setDraftValue] = useState(value);
  const onCommitRef = useRef(onCommit);
  onCommitRef.current = onCommit;
  const committer = useMemo(
    () =>
      createBufferedInputCommitter((nextValue) =>
        onCommitRef.current(nextValue),
      ),
    [],
  );
  const registerBufferedFlush = useContext(BufferedCommitRegistry);
  useEffect(() => setDraftValue(value), [value]);
  useEffect(() => () => { committer.flush(); }, [committer]);
  useEffect(() => {
    if (!registerBufferedFlush) return undefined;
    return registerBufferedFlush(() => committer.flush());
  }, [committer, registerBufferedFlush]);

  return (
    <Input
      id={id}
      value={draftValue}
      disabled={disabled}
      maxLength={maxLength}
      placeholder={placeholder}
      className={className}
      autoComplete={autoComplete}
      aria-describedby={describedBy}
      onChange={(event) => { const nextValue = nextBufferedInputValue(event.target.value); setDraftValue(nextValue); committer.update(nextValue); }}
      onBlur={() => { const committed = commitBufferedInputValue(draftValue); setDraftValue(committed); committer.flush(); }}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
      }}
    />
  );
}

type BufferedTextareaProps = {
  id?: string;
  value: string;
  onCommit: (value: string) => void;
  disabled: boolean;
  placeholder: string;
  describedBy: string;
  className?: string;
};

export function BufferedTextarea({
  id,
  value,
  onCommit,
  disabled,
  placeholder,
  describedBy,
  className,
}: BufferedTextareaProps) {
  const [draftValue, setDraftValue] = useState(value);
  const focusedRef = useRef(false);
  const onCommitRef = useRef(onCommit);
  onCommitRef.current = onCommit;
  const committer = useMemo(
    () =>
      createBufferedCommitter((nextValue) => onCommitRef.current(nextValue)),
    [],
  );
  const registerBufferedFlush = useContext(BufferedCommitRegistry);
  useEffect(() => {
    if (!focusedRef.current) setDraftValue(value);
  }, [value]);
  useEffect(() => () => { committer.flush(); }, [committer]);
  useEffect(() => {
    if (!registerBufferedFlush) return undefined;
    return registerBufferedFlush(() => committer.flush());
  }, [committer, registerBufferedFlush]);

  return (
    <Textarea
      id={id}
      disabled={disabled}
      className={className ?? "min-h-28"}
      value={draftValue}
      onFocus={() => {
        focusedRef.current = true;
      }}
      onChange={(event) => { const nextValue = event.target.value; setDraftValue(nextValue); committer.update(nextValue); }}
      onBlur={() => {
        focusedRef.current = false;
        committer.flush();
      }}
      placeholder={placeholder}
      aria-describedby={describedBy}
    />
  );
}
