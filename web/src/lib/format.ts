export const FALLBACK_INFO = {
  model: "unknown",
  transport: "nim · https",
  runtime: "fastapi · uvicorn",
  store: "sqlite",
  endpoint: "POST /chat/stream",
};

export const PROMPTS = [
  "Who are you and what can you do?",
  "Break down a goal I am working towards",
  "Summarise what you remember about me",
];

export const shortModel = (model: string) => {
  const name = (model || "").split("/").pop();
  return name ? name.split("-").slice(0, 3).join("-") : "unknown";
};

const pad = (n: number, w = 2) => String(n).padStart(w, "0");

export const fmtClock = (ms: number) => {
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};

export const fmtStamp = (ms: number) => {
  const d = new Date(ms);
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};

export const fmtElapsed = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
};

export const fmtMs = (ms: number | null) =>
  ms == null ? "—" : ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(2)}s`;

export const fmtChars = (n: number) => (n > 999 ? `${(n / 1000).toFixed(1)}k` : String(n));