<script lang="ts">
  import { onMount, tick } from 'svelte';

  import { closeWindow } from '../lib/window.ts';

  let { invoker, onclose }: { invoker: HTMLButtonElement; onclose: () => void } = $props();
  let cancelButton = $state<HTMLButtonElement>();
  let confirmButton = $state<HTMLButtonElement>();

  onMount(() => cancelButton?.focus());

  async function dismiss(): Promise<void> {
    const trigger = invoker;
    onclose();
    await tick();
    trigger.focus();
  }

  function confirm(event: MouseEvent): void {
    // The Confirm button can replace the Close Imp button under the pointer.
    if (event.detail > 1) return;
    void dismiss();
    closeWindow();
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.repeat && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      void dismiss();
    } else if (event.key === 'Tab') {
      event.preventDefault();
      // With two stops, Tab and Shift+Tab both move to the other button.
      (document.activeElement === cancelButton ? confirmButton : cancelButton)?.focus();
    }
  }
</script>

<svelte:window onkeydown={onKeydown} />

<div class="exit-confirmation" role="alertdialog" aria-modal="true" aria-labelledby="exit-confirmation-title">
  <h2 id="exit-confirmation-title">Close Imp?</h2>
  <button bind:this={cancelButton} class="manager-btn primary" type="button" onclick={dismiss}>Cancel</button>
  <button
    bind:this={confirmButton}
    class="manager-btn danger"
    type="button"
    onmousedown={(event) => {
      // Keep focus on Cancel when a double-click lands on the replacement button.
      if (event.detail > 1) event.preventDefault();
    }}
    onclick={confirm}>Confirm</button
  >
</div>

<style>
  .exit-confirmation {
    display: flex;
    grid-area: 1 / 1;
    /* The normal controls still size the row, including the compact intrinsic width. */
    contain: size;
    align-items: center;
    gap: var(--space-6);
  }

  h2 {
    margin: 0 auto 0 0;
    font-size: var(--font-md);
  }

  .manager-btn {
    box-sizing: border-box;
    height: 24px;
    font-size: var(--font-sm);
  }

  .manager-btn:focus-visible {
    outline: 2px solid var(--accent-focus);
    outline-offset: -2px;
  }
</style>
