"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * VoiceBar: wake-word / push-to-talk mic -> /ws/voice -> SAGE -> spoken line -> TTS.
 *
 * Sends raw 16-bit mono PCM16 frames (16 kHz) from an AudioContext
 * ScriptProcessor, and plays back WAV audio frames received from the server.
 */

type VoiceState = "idle" | "listening" | "processing";

const reconnectDelay = (attempt: number) => Math.min(1000 * 2 ** attempt, 10000);

export default function VoiceBar({
  enabled = true,
  busy = false,
  onTranscript,
}: {
  enabled?: boolean;
  busy?: boolean;
  onTranscript?: (text: string) => void;
}) {
  const [mode, setMode] = useState<"ptt" | "wake">("ptt");
  const [state, setState] = useState<VoiceState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState("");
  const [voiceConnected, setVoiceConnected] = useState(false);
  const [audioConnected, setAudioConnected] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const audioWsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const pttHeldRef = useRef(false);
  const audioQueueRef = useRef<{ id: string | null; buf: ArrayBuffer }[]>([]);
  const audioPlayingRef = useRef(false);
  const pendingAudioIdRef = useRef<string | null>(null);
  const aliveRef = useRef(false);
  const voiceRetryRef = useRef(0);
  const audioRetryRef = useRef(0);
  const voiceRetryTimerRef = useRef<number | null>(null);
  const audioRetryTimerRef = useRef<number | null>(null);
  const onTranscriptRef = useRef(onTranscript);
  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  const connectVoiceRef = useRef<() => void>(() => {});
  const connectAudioRef = useRef<() => void>(() => {});

  const scheduleVoiceReconnect = useCallback(() => {
    if (!aliveRef.current) return;
    if (voiceRetryTimerRef.current !== null) return;
    const delay = reconnectDelay(voiceRetryRef.current++);
    voiceRetryTimerRef.current = window.setTimeout(() => {
      voiceRetryTimerRef.current = null;
      connectVoiceRef.current();
    }, delay);
  }, []);

  const scheduleAudioReconnect = useCallback(() => {
    if (!aliveRef.current) return;
    if (audioRetryTimerRef.current !== null) return;
    const delay = reconnectDelay(audioRetryRef.current++);
    audioRetryTimerRef.current = window.setTimeout(() => {
      audioRetryTimerRef.current = null;
      connectAudioRef.current();
    }, delay);
  }, []);

  const connectVoice = useCallback(() => {
    if (!aliveRef.current) return;
    try {
      const ws = new WebSocket(
        `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.hostname}:8000/ws/voice`
      );
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        voiceRetryRef.current = 0;
        setVoiceConnected(true);
        setError(null);
      };
      ws.onmessage = (e) => {
        if (e.data instanceof ArrayBuffer) {
          // Audio frames now come via /ws/audio, not /ws/voice
          return;
        }
        try {
          const msg = JSON.parse(e.data as string);
          if (msg.type === "state") setState(msg.payload as VoiceState);
          else if (msg.type === "transcript") {
            setTranscript(msg.payload);
            onTranscriptRef.current?.(msg.payload);
          } else if (msg.type === "voice_test") {
            setTestResult(msg.payload?.heard ? "heard ✓" : "not confirmed ✗");
          } else if (msg.type === "error") {
            setError(msg.payload);
            setState("idle");
          }
        } catch {
          /* ignore */
        }
      };
      ws.onclose = () => {
        setVoiceConnected(false);
        setState("idle");
        scheduleVoiceReconnect();
      };
      ws.onerror = () => {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      };
      wsRef.current = ws;
    } catch {
      setError("could not open voice websocket");
      scheduleVoiceReconnect();
    }
  }, [scheduleVoiceReconnect]);

  const connectAudio = useCallback(() => {
    if (!aliveRef.current) return;
    try {
      const ws = new WebSocket(
        `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.hostname}:8000/ws/audio`
      );
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        audioRetryRef.current = 0;
        setAudioConnected(true);
        setError(null);
      };
      ws.onmessage = (e) => {
        if (typeof e.data === "string") {
          try {
            const msg = JSON.parse(e.data);
            if (msg.type === "audio") pendingAudioIdRef.current = msg.id ?? null;
          } catch {
            /* ignore */
          }
          return;
        }
        if (e.data instanceof ArrayBuffer) {
          const id = pendingAudioIdRef.current;
          pendingAudioIdRef.current = null;
          enqueueWav(e.data, id);
        }
      };
      ws.onclose = () => {
        setAudioConnected(false);
        scheduleAudioReconnect();
      };
      ws.onerror = () => {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      };
      audioWsRef.current = ws;
    } catch {
      setError("could not open audio websocket");
      scheduleAudioReconnect();
    }
  }, [scheduleAudioReconnect]);

  useEffect(() => {
    connectVoiceRef.current = connectVoice;
    connectAudioRef.current = connectAudio;
  }, [connectVoice, connectAudio]);

  useEffect(() => {
    if (!enabled) return;
    aliveRef.current = true;
    connectVoice();
    connectAudio();
    return () => {
      aliveRef.current = false;
      if (voiceRetryTimerRef.current !== null) {
        clearTimeout(voiceRetryTimerRef.current);
        voiceRetryTimerRef.current = null;
      }
      if (audioRetryTimerRef.current !== null) {
        clearTimeout(audioRetryTimerRef.current);
        audioRetryTimerRef.current = null;
      }
      wsRef.current?.close();
      audioWsRef.current?.close();
      audioQueueRef.current = [];
      setVoiceConnected(false);
      setAudioConnected(false);
      stopMic();
    };
  }, [enabled, connectVoice, connectAudio]);

  const sendControl = useCallback((type: string, payload?: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type, payload }));
  }, []);

  const sendAudioAck = useCallback(
    (id: string | null, ok: boolean, error?: string) => {
      if (!id) return;
      const ws = audioWsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(
        JSON.stringify({
          type: ok ? "audio_played" : "audio_failed",
          id,
          error,
        })
      );
    },
    []
  );

  const startMic = useCallback(async () => {
    if (processorRef.current) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, sampleRate: 16000 },
      });
      streamRef.current = stream;
      const ctx = new AudioContext({ sampleRate: 16000 });
      audioCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      processor.onaudioprocess = (e) => {
        const data = e.inputBuffer.getChannelData(0);
        const pcm = new Int16Array(data.length);
        for (let i = 0; i < data.length; i++) {
          pcm[i] = Math.max(-1, Math.min(1, data[i])) * 0x7fff;
        }
        const ws = wsRef.current;
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(pcm.buffer);
        }
      };
      source.connect(processor);
      processor.connect(ctx.destination);
      processorRef.current = processor;
    } catch {
      setError("microphone permission denied");
    }
  }, []);

  const stopMic = useCallback(() => {
    processorRef.current?.disconnect();
    processorRef.current = null;
    audioCtxRef.current?.close();
    audioCtxRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const pttStart = useCallback(() => {
    if (busy) return;
    setError(null);
    setTranscript("");
    sendControl("mode", "ptt");
    pttHeldRef.current = true;
    startMic();
    sendControl("ptt_start");
  }, [busy, sendControl, startMic]);

  const pttEnd = useCallback(() => {
    if (!pttHeldRef.current) return;
    pttHeldRef.current = false;
    sendControl("ptt_end");
  }, [sendControl]);

  const toggleWake = useCallback(() => {
    setError(null);
    const next = mode === "wake" ? "ptt" : "wake";
    setMode(next);
    sendControl("mode", next);
    if (next === "wake") startMic();
    else stopMic();
  }, [mode, sendControl, startMic, stopMic]);

  const unlockAudio = useCallback(() => {
    const ctx = audioCtxRef.current ?? new AudioContext({ sampleRate: 16000 });
    audioCtxRef.current = ctx;
    if (ctx.state === "suspended") void ctx.resume();
  }, []);

  useEffect(() => {
    const handler = () => unlockAudio();
    window.addEventListener("pointerdown", handler, { once: true });
    return () => window.removeEventListener("pointerdown", handler);
  }, [unlockAudio]);

  const testVoice = useCallback(() => {
    setError(null);
    unlockAudio();
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setTestResult("voice socket disconnected");
      return;
    }
    setTestResult("testing…");
    sendControl("test_voice");
  }, [sendControl, unlockAudio]);

  const drainAudioQueue = useCallback(async () => {
    if (audioPlayingRef.current) return;
    audioPlayingRef.current = true;
    try {
      while (audioQueueRef.current.length > 0) {
        const item = audioQueueRef.current.shift();
        if (!item) continue;
        const { id, buf } = item;

        const ctx = audioCtxRef.current ?? new AudioContext({ sampleRate: 16000 });
        audioCtxRef.current = ctx;
        if (ctx.state === "suspended") {
          try {
            await ctx.resume();
          } catch {
            /* autoplay policy; a user gesture will unlock it later */
          }
        }
        if (ctx.state !== "running") {
          setError("audio is blocked until you interact with the page");
          sendAudioAck(id, false, "autoplay blocked");
          continue;
        }

        let buffer: AudioBuffer;
        try {
          buffer = await ctx.decodeAudioData(buf.slice(0));
        } catch {
          setError("could not decode TTS audio");
          sendAudioAck(id, false, "decode failed");
          continue;
        }

        await new Promise<void>((resolve) => {
          const source = ctx.createBufferSource();
          source.buffer = buffer;
          source.connect(ctx.destination);
          source.onended = () => resolve();
          source.start();
          sendAudioAck(id, true);
        });
      }
    } finally {
      audioPlayingRef.current = false;
    }
  }, [sendAudioAck]);

  const enqueueWav = useCallback(
    (buf: ArrayBuffer, id: string | null) => {
      audioQueueRef.current.push({ id, buf });
      void drainAudioQueue();
    },
    [drainAudioQueue]
  );

  if (!enabled) return null;

  const listening = state === "listening";

  return (
    <div className="flex items-center gap-3 border-t border-zinc-800 px-5 py-2">
      <span className="text-[11px] uppercase tracking-[0.15em] text-zinc-500 font-medium">
        voice
      </span>
      <button
        type="button"
        onPointerDown={pttStart}
        onPointerUp={pttEnd}
        onPointerLeave={pttEnd}
        disabled={busy || mode === "wake" || state === "processing"}
        className={`shrink-0 rounded-full px-4 py-1.5 text-[12px] font-medium uppercase tracking-wide transition-colors ${
          listening
            ? "bg-red-500/20 text-red-300 border border-red-500/50"
            : "bg-zinc-800 text-zinc-200 border border-zinc-700 hover:border-zinc-500 disabled:opacity-40"
        }`}
        title="Hold to talk"
      >
        {state === "processing" ? "…" : listening ? "rec" : "hold"}
      </button>
      <button
        type="button"
        onClick={toggleWake}
        className={`shrink-0 rounded-full px-3 py-1.5 text-[11px] uppercase tracking-wide border transition-colors ${
          mode === "wake"
            ? "bg-amber-500/15 text-amber-300 border-amber-500/50"
            : "bg-zinc-800 text-zinc-400 border-zinc-700 hover:border-zinc-500"
        }`}
      >
        {mode === "wake" ? "wake:on" : "wake:off"}
      </button>
      <button
        type="button"
        onClick={testVoice}
        className="shrink-0 rounded-full border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-[11px] uppercase tracking-wide text-zinc-400 transition-colors hover:border-zinc-500"
        title="Play a test line and confirm playback"
      >
        test
      </button>
      <span
        className={`shrink-0 text-[10px] uppercase tracking-wide ${
          voiceConnected && audioConnected ? "text-emerald-400" : "text-rose-400"
        }`}
        title={`voice socket: ${voiceConnected ? "connected" : "disconnected"}; audio socket: ${
          audioConnected ? "connected" : "disconnected"
        }`}
      >
        {voiceConnected && audioConnected ? "audio ✓" : "audio ✗"}
      </span>
      <div className="min-w-0 flex-1 truncate text-[12px] text-zinc-400">
        {error ? (
          <span className="text-red-400">{error}</span>
        ) : transcript ? (
          <span className="text-zinc-200">{transcript}</span>
        ) : testResult && testResult !== "testing…" ? (
          <span className="text-zinc-300">test: {testResult}</span>
        ) : (
          <span className="text-zinc-600">
            {!voiceConnected || !audioConnected
              ? "reconnecting voice…"
              : mode === "wake"
                ? "listening for wake word…"
                : "hold to talk to sage"}
          </span>
        )}
      </div>
    </div>
  );
}