export interface Command<State> {
  readonly label?: string;
  execute(state: State): State;
}

export interface HistoryOptions {
  /** Maximum undo entries retained. Defaults to 100. */
  readonly capacity?: number;
}

interface Entry<State> {
  readonly before: State;
  readonly after: State;
  readonly label?: string;
}

export class CommandHistory<State> {
  readonly #capacity: number;
  #state: State;
  #undoStack: Entry<State>[] = [];
  #redoStack: Entry<State>[] = [];

  constructor(initialState: State, options: HistoryOptions = {}) {
    const capacity = options.capacity ?? 100;
    if (!Number.isSafeInteger(capacity) || capacity < 0) {
      throw new RangeError('History capacity must be a non-negative integer');
    }
    this.#state = initialState;
    this.#capacity = capacity;
  }

  get state(): State {
    return this.#state;
  }

  get canUndo(): boolean {
    return this.#undoStack.length > 0;
  }

  get canRedo(): boolean {
    return this.#redoStack.length > 0;
  }

  get undoCount(): number {
    return this.#undoStack.length;
  }

  get redoCount(): number {
    return this.#redoStack.length;
  }

  get nextUndoLabel(): string | undefined {
    return this.#undoStack.at(-1)?.label;
  }

  get nextRedoLabel(): string | undefined {
    return this.#redoStack.at(-1)?.label;
  }

  execute(command: Command<State>): State {
    const before = this.#state;
    const after = command.execute(before);
    this.#state = after;
    this.#redoStack = [];
    if (this.#capacity > 0 && after !== before) {
      const entry: Entry<State> =
        command.label === undefined ? { before, after } : { before, after, label: command.label };
      this.#undoStack.push(entry);
      if (this.#undoStack.length > this.#capacity) this.#undoStack.shift();
    }
    return this.#state;
  }

  undo(): State {
    const entry = this.#undoStack.pop();
    if (entry === undefined) return this.#state;
    this.#state = entry.before;
    this.#redoStack.push(entry);
    return this.#state;
  }

  redo(): State {
    const entry = this.#redoStack.pop();
    if (entry === undefined) return this.#state;
    this.#state = entry.after;
    this.#undoStack.push(entry);
    return this.#state;
  }

  clear(): void {
    this.#undoStack = [];
    this.#redoStack = [];
  }

  /** Replaces the current state and discards all history. */
  reset(state: State): void {
    this.#state = state;
    this.clear();
  }
}
