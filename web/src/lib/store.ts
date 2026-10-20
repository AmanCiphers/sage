export interface LogRecord {
  id?: number;
  seq?: number;
  ts: number;
  level: "error" | "warning" | "info" | "debug";
  category: string;
  message: string;
  data?: unknown;
  task_id?: string;
}

export interface TimelineEvent {
  seq: number;
  kind: "timeline" | "log" | "state";
  level?: string;
  category?: string;
  message?: string;
  data?: unknown;
  status?: "queued" | "running" | "needs_approval" | "done" | "failed" | "cancelled";
  started_at?: number;
  elapsed?: number;
  stage?: string;
  progress?: string;
  result_preview?: string;
  result_chars?: number;
  error?: string;
}

export interface TaskState {
  id: string;
  title: string;
  status: "queued" | "running" | "needs_approval" | "done" | "failed" | "cancelled";
  started_at: number;
  stage?: string;
  progress?: string;
  result_preview?: string;
  result_chars?: number;
  error?: string;
  logs: LogRecord[];
  timeline: TimelineEvent[];
}

export interface Approval {
  id: string;
  description?: string;
  target?: string;
  worker_task_id?: string;
  fromTimeline?: boolean;
}

let logSeq = 0;
let globalSeq = 0;

export const logRecords: LogRecord[] = [];
export const tasks = new Map<string, TaskState>();
export const pendingApprovals = new Map<string, Approval>();
export const notificationQueue: Array<{ kind: string; body: string }> = [];

export function addLogRecord(record: Omit<LogRecord, "id" | "seq">) {
  const seq = ++globalSeq;
  const id = ++logSeq;
  const fullRecord: LogRecord = { ...record, id, seq };
  logRecords.push(fullRecord);
  if (logRecords.length > 5000) logRecords.shift();
  return fullRecord;
}

export function addTask(task: TaskState) {
  tasks.set(task.id, task);
}

export function getTask(id: string) {
  return tasks.get(id);
}

export function updateTask(id: string, updates: Partial<TaskState>) {
  const task = tasks.get(id);
  if (task) {
    tasks.set(id, { ...task, ...updates });
    return tasks.get(id);
  }
  return null;
}

export function addTimelineEvent(taskId: string, event: TimelineEvent) {
  const task = tasks.get(taskId);
  if (task) {
    task.timeline.push(event);
    if (task.timeline.length > 2000) task.timeline.shift();
  }
}

export function addTaskLog(taskId: string, log: LogRecord) {
  const task = tasks.get(taskId);
  if (task) {
    task.logs.push(log);
    if (task.logs.length > 2000) task.logs.shift();
  }
}

export function addPendingApproval(approval: Approval) {
  pendingApprovals.set(approval.id, approval);
  notificationQueue.push({ kind: "approval", body: approval.description || approval.target || "" });
}

export function getPendingApproval(id: string) {
  return pendingApprovals.get(id);
}

export function resolvePendingApproval(id: string) {
  pendingApprovals.delete(id);
}

export function addNotification(kind: string, body: string) {
  notificationQueue.push({ kind, body });
  if (notificationQueue.length > 100) notificationQueue.shift();
}

export function getNotifications() {
  const notifications = [...notificationQueue];
  notificationQueue.length = 0;
  return notifications;
}

export function createTaskId() {
  return Math.random().toString(36).slice(2, 10);
}

export function createApprovalId() {
  return `appr_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}