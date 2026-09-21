import { decodeServerMessage, PROTOCOL_VERSION, type GameState, type StateContext } from '@tinyscry/protocol';

import type { SourceEvent, StateSource } from './types.ts';

interface MockOptions {
  readonly seed?: number;
  readonly intervalMs?: number;
}

interface MockVitals {
  hp: number;
  mana: number;
  moves: number;
}

const MAX_HP = 1_200;
const MAX_MANA = 800;
const MAX_MOVES = 500;
const MOCK_CONTEXT: StateContext = {
  session: 'mock_session',
  foreground: 1,
  connection: 1,
};

type TimerHandle = number | NodeJS.Timeout;

/** A small deterministic generator keeps the demo lively without making tests flaky. */
class SeededRandom {
  private state: number;

  constructor(seed: number) {
    this.state = seed >>> 0;
  }

  next(): number {
    this.state = (this.state * 1_664_525 + 1_013_904_223) >>> 0;
    return this.state / 4_294_967_296;
  }
}

export class MockStateSource implements StateSource {
  readonly id = 'mock';
  readonly label = 'Demo feed';

  private readonly intervalMs: number;
  private readonly random: SeededRandom;
  private listener: ((event: SourceEvent) => void) | null = null;
  private timer: TimerHandle | null = null;
  private sequence = 0;
  private target: GameState['target'] = { name: 'a frost giant', healthPercent: 100 };
  private vitals: MockVitals = { hp: 1_120, mana: 730, moves: 460 };

  constructor({ seed = 0x5c_0f_000, intervalMs = 600 }: MockOptions = {}) {
    this.random = new SeededRandom(seed);
    this.intervalMs = intervalMs;
  }

  start(listener: (event: SourceEvent) => void): void {
    this.stop();
    this.listener = listener;
    this.emit({ kind: 'connection', phase: 'connecting', detail: null });
    this.emit({ kind: 'connection', phase: 'connected', detail: null });

    // Exercise the same rejection path a hostile relay frame would use.
    this.decodeAndEmit(
      JSON.stringify({
        type: 'snapshot',
        protocol: PROTOCOL_VERSION,
        seq: 0,
        at: Date.now(),
        context: null,
        state: {
          character: { name: 'Aria', hp: { current: -1, max: MAX_HP }, mana: null, moves: null },
          target: null,
        },
      }),
    );

    this.tick();
    this.timer = setInterval(() => this.tick(), this.intervalMs);
  }

  stop(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.listener = null;
  }

  private tick(): void {
    this.vitals.hp = this.adjust(this.vitals.hp, MAX_HP, 58, 0.16);
    this.vitals.mana = this.adjust(this.vitals.mana, MAX_MANA, 34, 0.28);
    this.vitals.moves = this.adjust(this.vitals.moves, MAX_MOVES, 25, 0.22);

    if (this.target !== null) {
      const nextHealth = Math.max(
        0,
        (this.target.healthPercent ?? 100) - 1 - Math.floor(this.random.next() * 5),
      );
      this.target =
        nextHealth === 0 || this.random.next() < 0.035
          ? this.nextTarget()
          : { ...this.target, healthPercent: nextHealth };
    } else if (this.random.next() > 0.62) {
      this.target = this.nextTarget();
    }

    const state: GameState = {
      character: {
        name: 'Aria',
        hp: { current: this.vitals.hp, max: MAX_HP },
        mana: { current: this.vitals.mana, max: MAX_MANA },
        moves: { current: this.vitals.moves, max: MAX_MOVES },
      },
      target: this.target,
    };

    this.decodeAndEmit(
      JSON.stringify({
        type: 'snapshot',
        protocol: PROTOCOL_VERSION,
        seq: this.sequence++,
        at: Date.now(),
        context: MOCK_CONTEXT,
        state,
      }),
    );

    if (this.sequence % 5 === 0) {
      this.emit({ kind: 'feed', status: 'live', detail: 'Demo feed is updating.' });
    }
  }

  private adjust(current: number, max: number, drain: number, healChance: number): number {
    const direction = this.random.next() < healChance ? 1 : -1;
    const amount = 4 + Math.floor(this.random.next() * drain);
    return Math.max(0, Math.min(max, current + direction * amount));
  }

  private nextTarget(): GameState['target'] {
    const names = ['a frost giant', 'a clockwork sentry', 'a shadow hound'];
    const name = names[Math.floor(this.random.next() * names.length)] ?? 'a frost giant';
    return this.random.next() < 0.12 ? null : { name, healthPercent: 100 };
  }

  private decodeAndEmit(frame: string): void {
    const decoded = decodeServerMessage(frame);
    if (!decoded.ok) {
      this.emit({ kind: 'protocol-error', error: decoded.error });
      return;
    }

    switch (decoded.value.type) {
      case 'hello':
        this.emit({ kind: 'hello', relay: decoded.value.relay });
        break;
      case 'snapshot':
        this.emit({
          kind: 'snapshot',
          seq: decoded.value.seq,
          at: decoded.value.at,
          context: decoded.value.context,
          state: decoded.value.state,
        });
        break;
      case 'status':
        this.emit({ kind: 'feed', status: decoded.value.feed, detail: decoded.value.detail });
        break;
    }
  }

  private emit(event: SourceEvent): void {
    this.listener?.(event);
  }
}
