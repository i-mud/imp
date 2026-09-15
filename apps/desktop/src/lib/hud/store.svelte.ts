import { applyEvent, INITIAL_MODEL, type HudModel } from './model.ts';
import type { StateSource } from '../source/types.ts';

export class HudStore {
  model = $state<HudModel>(INITIAL_MODEL);
  private source: StateSource | null = null;

  attach(source: StateSource): void {
    this.detach();
    this.source = source;
    source.start((event) => {
      this.model = applyEvent(this.model, event);
    });
  }

  detach(): void {
    this.source?.stop();
    this.source = null;
  }
}
