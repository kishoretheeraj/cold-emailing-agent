import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UNDO_SECONDS, UndoManager } from "./undo-manager";

const base = { id: "42", type: "approve" as const, label: "Associate Product Manager at Acme Corp" };

describe("UndoManager", () => {
  let manager: UndoManager;

  beforeEach(() => {
    vi.useFakeTimers();
    manager = new UndoManager();
  });

  afterEach(() => {
    manager.cancelAll();
    vi.useRealTimers();
  });

  it("returns the action with a timestamp and timeout id", () => {
    vi.setSystemTime(new Date("2026-10-11T12:00:00Z"));
    const action = manager.schedule(base, vi.fn());
    expect(action.id).toBe("42");
    expect(action.type).toBe("approve");
    expect(action.label).toBe(base.label);
    expect(action.timestamp).toBe(new Date("2026-10-11T12:00:00Z").getTime());
    expect(action.timeoutId).toBeDefined();
  });

  it("calls commit after UNDO_SECONDS and not before", () => {
    const commit = vi.fn();
    manager.schedule(base, commit);
    vi.advanceTimersByTime(UNDO_SECONDS * 1000 - 1);
    expect(commit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it("calls commit only once even if time keeps advancing", () => {
    const commit = vi.fn();
    manager.schedule(base, commit);
    vi.advanceTimersByTime(30_000);
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it("calls onTick with 4, 3, 2, 1 and never 0 or 5", () => {
    const onTick = vi.fn();
    manager.schedule(base, vi.fn(), onTick);
    vi.advanceTimersByTime(UNDO_SECONDS * 1000);
    expect(onTick.mock.calls.map((c) => c[0])).toEqual([4, 3, 2, 1]);
  });

  it("stops ticking after commit", () => {
    const onTick = vi.fn();
    manager.schedule(base, vi.fn(), onTick);
    vi.advanceTimersByTime(UNDO_SECONDS * 1000);
    onTick.mockClear();
    vi.advanceTimersByTime(10_000);
    expect(onTick).not.toHaveBeenCalled();
  });

  it("cancel prevents commit and further ticks", () => {
    const commit = vi.fn();
    const onTick = vi.fn();
    const action = manager.schedule(base, commit, onTick);
    // Ticks fire once a second (4, 3, 2, 1), so two seconds in there have been two.
    vi.advanceTimersByTime(2000);
    expect(onTick.mock.calls.map((c) => c[0])).toEqual([4, 3]);
    manager.cancel(action);
    vi.advanceTimersByTime(10_000);
    expect(commit).not.toHaveBeenCalled();
    expect(onTick).toHaveBeenCalledTimes(2);
  });

  it("cancel on an already committed action is a no-op", () => {
    const commit = vi.fn();
    const action = manager.schedule(base, commit);
    vi.advanceTimersByTime(UNDO_SECONDS * 1000);
    expect(() => manager.cancel(action)).not.toThrow();
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it("cancelling one action leaves others running", () => {
    const commitA = vi.fn();
    const commitB = vi.fn();
    const a = manager.schedule({ ...base, id: "a" }, commitA);
    manager.schedule({ ...base, id: "b", type: "reject" }, commitB);
    manager.cancel(a);
    vi.advanceTimersByTime(UNDO_SECONDS * 1000);
    expect(commitA).not.toHaveBeenCalled();
    expect(commitB).toHaveBeenCalledTimes(1);
  });

  it("cancelAll cancels every pending action", () => {
    const commits = [vi.fn(), vi.fn(), vi.fn()];
    const ticks = vi.fn();
    commits.forEach((commit, i) => manager.schedule({ ...base, id: String(i) }, commit, ticks));
    manager.cancelAll();
    vi.advanceTimersByTime(10_000);
    commits.forEach((commit) => expect(commit).not.toHaveBeenCalled());
    expect(ticks).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("cancelAll does not affect actions scheduled afterwards", () => {
    manager.cancelAll();
    const commit = vi.fn();
    manager.schedule(base, commit);
    vi.advanceTimersByTime(UNDO_SECONDS * 1000);
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it("clears the interval even when commit throws", () => {
    manager.schedule(base, () => {
      throw new Error("boom");
    });
    expect(() => vi.advanceTimersByTime(UNDO_SECONDS * 1000)).toThrow("boom");
    expect(vi.getTimerCount()).toBe(0);
  });
});
