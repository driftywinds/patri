<script>
  let { text = '' } = $props();

  let box = $state(null);
  let over = $state(false);
  let width = $state(0);

  $effect(() => {
    if (!box) return;

    const measure = () => {
      const seg = box.querySelector('.seg');
      if (!seg) return;
      box.title = text;
      const range = document.createRange();
      range.selectNodeContents(seg);
      width = range.getBoundingClientRect().width;
      over = width > box.clientWidth + 1;
    };

    measure();
    // The web font lands after mount and changes every width.
    document.fonts?.ready.then(measure);
    addEventListener('resize', measure);
    return () => removeEventListener('resize', measure);
  });

  let duration = $derived(Math.max(6, Math.round((width + 40) / 45)));
</script>

<span
  class="marquee {over ? 'over' : ''}"
  bind:this={box}
  style="--dur: {duration}s"
>
  <span class="run">
    <span class="seg">{text}</span>
    {#if over}<span class="seg" aria-hidden="true">{text}</span>{/if}
  </span>
</span>

<style>
  .marquee {
    display: block;
    overflow: hidden;
  }
  .run {
    display: inline-flex;
    white-space: nowrap;
  }
  .seg {
    padding-right: 2.5rem;
  }
  .over .run {
    animation: slide var(--dur, 10s) linear infinite;
  }
  .over:hover .run,
  .over:focus-within .run {
    animation-play-state: paused;
  }
  @keyframes slide {
    to {
      transform: translateX(-50%);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .over .run {
      animation: none;
    }
    .over .seg + .seg {
      display: none;
    }
    .over .seg {
      padding-right: 0;
    }
  }
</style>
