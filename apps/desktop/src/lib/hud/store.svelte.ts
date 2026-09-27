import { SvelteSet } from 'svelte/reactivity';

import { applyEvent, INITIAL_MODEL, type HudModel } from './model.ts';
import type { SourceEvent, StateSource } from '../source/types.ts';

export type TextSourceEvent = Extract<SourceEvent, { readonly kind: 'text' }>;
export type TextSourceListener = (event: TextSourceEvent) => void;

export class HudStore {
  model = $state<HudModel>(INITIAL_MODEL);
  private source: StateSource | null = null;
  private readonly textListeners = new SvelteSet<TextSourceListener>();

  attach(source: StateSource): void {
    this.detach();
    this.source = source;
    source.start((event) => {
      if (event.kind === 'text') {
        for (const listener of this.textListeners) listener(event);
        return;
      }

      this.model = applyEvent(this.model, event);
    });
  }

  subscribeText(listener: TextSourceListener): () => void {
    this.textListeners.add(listener);
    return () => this.textListeners.delete(listener);
  }

  detach(): void {
    this.source?.stop();
    this.source = null;
  }
}
