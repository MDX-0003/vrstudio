<template>
  <div class="submit-bar">
    <div class="submit-inner">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.submit-bar {
  position: fixed;
  left: var(--nav-current-width, var(--nav-width));
  right: 0;
  bottom: 0;
  z-index: 20;
  background: var(--color-overlay);
  border-top: 1px solid var(--color-border);
  box-shadow: var(--shadow-submitbar);
  backdrop-filter: blur(8px);
  transition: left var(--duration-base) ease;
}

.submit-inner {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-width: 1600px;
  margin: 0 auto;
  padding: var(--space-3) var(--space-6);
}

@media (max-width: 1024px) {
  .submit-inner {
    padding: var(--space-2) var(--space-4);
  }
}
</style>
