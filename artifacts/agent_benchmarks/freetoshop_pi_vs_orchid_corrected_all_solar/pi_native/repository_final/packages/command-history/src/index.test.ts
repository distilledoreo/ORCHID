import { describe, expect, it } from 'vitest';
import { CommandHistory } from './index.js';

const add = (amount: number) => ({
  label: `Add ${amount}`,
  execute: (value: number) => value + amount,
});

describe('CommandHistory', () => {
  it('executes, undoes, and redoes immutable state transitions', () => {
    const history = new CommandHistory(1);
    expect(history.execute(add(2))).toBe(3);
    expect(history.nextUndoLabel).toBe('Add 2');
    expect(history.undo()).toBe(1);
    expect(history.canRedo).toBe(true);
    expect(history.redo()).toBe(3);
  });

  it('clears redo entries after a new command', () => {
    const history = new CommandHistory(0);
    history.execute(add(1));
    history.undo();
    history.execute(add(4));
    expect(history.canRedo).toBe(false);
    expect(history.state).toBe(4);
  });

  it('honors capacity and supports disabled history', () => {
    const history = new CommandHistory(0, { capacity: 2 });
    history.execute(add(1));
    history.execute(add(2));
    history.execute(add(3));
    expect(history.undoCount).toBe(2);
    expect(history.undo()).toBe(3);
    expect(history.undo()).toBe(1);
    expect(history.undo()).toBe(1);

    const disabled = new CommandHistory(0, { capacity: 0 });
    disabled.execute(add(1));
    expect(disabled.canUndo).toBe(false);
    expect(disabled.state).toBe(1);
  });
});
