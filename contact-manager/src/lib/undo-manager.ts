/** Seconds the user has to undo an approve/reject before it commits. */
export const UNDO_SECONDS = 5;

export type PendingAction = {
  id: string;
  type: "approve" | "reject";
  /** Human-readable label, e.g. "Associate Product Manager at Acme Corp". */
  label: string;
  timestamp: number;
  timeoutId: ReturnType<typeof setTimeout>;
};

/**
 * Schedules approve/reject actions with a 5-second undo window.
 * The commit callback fires only if the action is not cancelled in time.
 * No React dependencies — the hook layer wires this to toasts and state.
 */
export class UndoManager {
  private pending = new Map<PendingAction, ReturnType<typeof setInterval>>();

  schedule(
    action: Omit<PendingAction, "timestamp" | "timeoutId">,
    commit: () => void,
    onTick?: (secondsLeft: number) => void,
  ): PendingAction {
    const startedAt = Date.now();
    let left = UNDO_SECONDS;

    const intervalId = setInterval(() => {
      left -= 1;
      if (left > 0) onTick?.(left);
    }, 1000);

    const timeoutId = setTimeout(() => {
      // Clean up before committing so a throwing commit cannot leak the interval.
      this.clear(scheduled);
      commit();
    }, UNDO_SECONDS * 1000);

    const scheduled: PendingAction = { ...action, timestamp: startedAt, timeoutId };
    this.pending.set(scheduled, intervalId);
    return scheduled;
  }

  cancel(action: PendingAction): void {
    this.clear(action);
  }

  cancelAll(): void {
    for (const action of Array.from(this.pending.keys())) {
      this.clear(action);
    }
  }

  private clear(action: PendingAction): void {
    clearTimeout(action.timeoutId);
    const intervalId = this.pending.get(action);
    if (intervalId !== undefined) clearInterval(intervalId);
    this.pending.delete(action);
  }
}
