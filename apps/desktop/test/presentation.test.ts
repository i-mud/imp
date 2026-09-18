import { describe, expect, it } from 'vitest';

import { INITIAL_MODEL, type HudModel } from '../src/lib/hud/model.ts';
import { displayModeFromPersisted, statusIndicatorOf, statusLabelOf } from '../src/lib/hud/presentation.ts';

function model(overrides: Partial<HudModel>): HudModel {
  return { ...INITIAL_MODEL, phase: 'connected', feed: 'live', hasData: true, ...overrides };
}

describe('statusIndicatorOf', () => {
  it('shows up only for fresh usable game data', () => {
    expect(statusIndicatorOf(model({}))).toBe('up');
    expect(statusIndicatorOf(model({ hasData: false }))).toBe('down');
  });

  it('shows stale for a stalled feed', () => {
    expect(statusIndicatorOf(model({ feed: 'stale' }))).toBe('stale');
  });

  it('shows down for reconnecting, disconnected, and unavailable feeds', () => {
    expect(statusIndicatorOf(model({ phase: 'reconnecting' }))).toBe('down');
    expect(statusIndicatorOf(model({ phase: 'disconnected' }))).toBe('down');
    expect(statusIndicatorOf(model({ feed: 'down' }))).toBe('down');
  });
});

describe('statusLabelOf', () => {
  it('keeps tooltip wording specific to the underlying freshness state', () => {
    expect(statusLabelOf(model({}))).toBe('Game data live');
    expect(statusLabelOf(model({ feed: 'stale' }))).toBe('Game feed stalled');
    expect(statusLabelOf(model({ feed: 'down' }))).toBe('No game feed');
    expect(statusLabelOf(model({ phase: 'reconnecting' }))).toBe('Reconnecting to relay');
    expect(statusLabelOf(model({ phase: 'connecting' }))).toBe('Connecting to relay');
    expect(statusLabelOf(model({ hasData: false }))).toBe('Waiting for game data');
  });
});

describe('displayModeFromPersisted', () => {
  it('restores compact mode', () => {
    expect(displayModeFromPersisted('compact')).toBe('compact');
  });

  it('restores expanded mode', () => {
    expect(displayModeFromPersisted('expanded')).toBe('expanded');
  });

  it('falls back to expanded mode for invalid stored values', () => {
    expect(displayModeFromPersisted('minimal')).toBe('expanded');
    expect(displayModeFromPersisted(null)).toBe('expanded');
  });
});
