---
title: Home
hide:
  - navigation
  - toc
---

<style>
.md-typeset .home2 {
  width: 100%;
}
.md-typeset .home2,
.md-typeset .home2 * {
  box-sizing: border-box;
  font-size: 1em;
}
.md-typeset .home2 h1,
.md-typeset .home2 h2,
.md-typeset .home2 h3 {
  color: var(--md-default-fg-color);
  font-weight: 700;
  letter-spacing: normal;
  line-height: inherit;
}
.md-typeset .home2 h1,
.md-typeset .home2 h2 {
  margin: 0 0 .75em;
  font-size: calc(2 * var(--site-heading-base) - 1px);
  line-height: 1.2;
}
.md-typeset .home2 h3 { font-size: calc(var(--site-heading-base) - 1px); }
.md-typeset .home2 h2,
.md-typeset .home2 h3 { margin: 0 0 .75em; }
.home2 p { margin: 0 0 1em; }
.home2 .h2-hero {
  display: grid;
  grid-template-columns: .9fr 1.2fr;
  align-items: center;
  gap: 3em;
  padding: 1em 0 2em;
}
.home2 .h2-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 1em; margin-top: 1.5em; }
.md-typeset .home2 .md-button { padding: .5em 1em; }
.home2 figure { min-width: 0; margin: 0; }
.home2 img { display: block; width: 100%; height: auto; }
.md-typeset .home2 figcaption {
  max-width: none;
  margin: .5em 0 0;
  color: var(--md-default-fg-color);
  font-style: normal;
  text-align: center;
}
.home2 .h2-section {
  padding-top: 1.5em;
  margin-top: 1.5em;
  border-top: 1px solid var(--md-default-fg-color--lightest);
}
.home2 .h2-scenes,
.home2 .h2-pillars,
.home2 .h2-resources {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 2em;
  margin: 1.5em 0;
}
.home2 .h2-scene { min-width: 0; }
.home2 .h2-scene img { margin-bottom: 1em; }
.home2 .h2-scene > a { display: block; margin-top: .75em; }
.home2 .h2-pillar { min-width: 0; }
.home2 .h2-pillar p:last-child { margin-bottom: 0; }
.home2 .h2-resource {
  min-width: 0;
  padding: 1.5em;
  border: 1px solid var(--md-default-fg-color--lightest);
  border-radius: .2rem;
}
.home2 .h2-resource p { margin-bottom: 1.5em; }
.home2 .h2-resource > a { display: inline-block; }
.home2 a:focus-visible {
  outline: 2px solid var(--md-accent-fg-color);
  outline-offset: 4px;
}
@media (max-width: 900px) {
  .home2 .h2-hero,
  .home2 .h2-scenes,
  .home2 .h2-pillars,
  .home2 .h2-resources { grid-template-columns: 1fr; gap: 1.5em; }
  .home2 .h2-hero { padding-top: 0; }
  .home2 figure { max-width: 600px; margin: 0 auto; }
  .home2 .h2-scene img { max-width: 420px; margin: 0 auto 1em; }
}
@media (max-width: 600px) {
  .md-main:has(.home2) .md-sidebar--primary { width: 12.1rem; }
  .md-main__inner:has(.home2) { padding: 0 8px; }
}
</style>

<div class="home2">
  <section class="h2-hero" aria-labelledby="home2-title">
    <div>
      <h1 id="home2-title">One business signal.<br>Many ways to act.</h1>
      <p>A sales dip. A newly placed order. Recognize what matters and share the facts teams need to act or analyze.</p>
      <p>Your workloads already detect important business situations. <strong>Business Events</strong> represent those signals through a shared, schema-defined contract, so teams and applications can reuse the same business meaning and respond independently.</p>
      <div class="h2-actions">
        <a class="md-button md-button--primary" href="../business-events/scenarios/order-history-analysis/">Build your first event</a>
        <a href="../business-events/introduction/what-are-business-events/">Understand the basics &rarr;</a>
      </div>
    </div>
    <figure>
      <img src="../assets/home/event-to-action.png" width="1200" height="675" fetchpriority="high" alt="Existing sales detection identifies a business signal. A shared Business Event represents that signal for independent notification and analytics consumers.">
      <figcaption>Keep your detection. Share the signal. Enable independent responses.</figcaption>
    </figure>
  </section>

  <section class="h2-section" aria-labelledby="home2-scenarios-title">
    <h2 id="home2-scenarios-title">Find a moment<br>worth acting on.</h2>
    <div class="h2-scenes">
      <article class="h2-scene">
        <img src="../assets/home/sales-alert.png" width="1200" height="675" loading="lazy" alt="A retail storefront with a chart showing falling sales.">
        <h3>Alert the team.</h3>
        <p>A Notebook detects a sales dip and publishes a Business Event. Activator sends a Teams alert; other consumers can reuse the same signal without changing the Notebook.</p>
        <a href="../business-events/scenarios/sales-volume-alert/">Build a sales volume alert &rarr;</a>
      </article>
      <article class="h2-scene">
        <img src="../assets/home/cart-recovery.png" width="1200" height="675" loading="lazy" alt="A shopping bag and abandoned cart connect to a recovery email.">
        <h3>Bring a customer back.</h3>
        <p>Share one cart abandonment signal with independent consumers for recovery actions and conversion analytics, instead of integrating each response into the publisher.</p>
        <a href="../business-events/scenarios/abandoned-cart-recovery/">Explore cart recovery &rarr;</a>
      </article>
      <article class="h2-scene">
        <img src="../assets/home/event-to-action.png" width="1200" height="675" loading="lazy" alt="One shared business signal connects independent response and analytics consumers.">
        <h3>Understand the activity.</h3>
        <p>Publish six synthetic orders from a Notebook, retain their Business Events in Eventhouse and compare order volume and value across two stores with KQL.</p>
        <a href="../business-events/scenarios/order-history-analysis/">Explore order history analysis &rarr;</a>
      </article>
    </div>
    <p><a href="../business-events/scenarios/">Browse all scenarios &rarr;</a></p>
  </section>

  <section class="h2-section" aria-labelledby="home2-pillars-title">
    <h2 id="home2-pillars-title">Three pillars.<br>One event-driven toolkit.</h2>
    <div class="h2-pillars">
      <div class="h2-pillar">
        <h3>Business Events</h3>
        <p>Shared, schema-defined representations of business signals that teams and applications can reuse without replacing existing detection logic.</p>
        <p><a href="../business-events/introduction/what-are-business-events/">Guides available now &rarr;</a></p>
      </div>
      <div class="h2-pillar">
        <h3>Fabric Events</h3>
        <p>Platform signals, such as item creation and workspace changes.</p>
        <p><em>Site coverage coming soon.</em></p>
      </div>
      <div class="h2-pillar">
        <h3>Azure Events</h3>
        <p>Cloud signals that connect Azure systems and Fabric workloads.</p>
        <p><em>Site coverage coming soon.</em></p>
      </div>
    </div>
  </section>

  <section class="h2-section" aria-labelledby="home2-resources-title">
    <h2 id="home2-resources-title">Go from an idea<br>to a working solution.</h2>
    <p>Choose your next step, whether you are exploring Business Events or building your own integration.</p>
    <div class="h2-resources">
      <article class="h2-resource">
        <h3>Build your first solution.</h3>
        <p>Follow a complete scenario, from the event schema and publisher to consumer setup and an end-to-end test.</p>
        <a href="../business-events/scenarios/sales-volume-alert/">Start with a sales alert &rarr;</a>
      </article>
      <article class="h2-resource">
        <h3>Implement a specific task.</h3>
        <p>Use focused recipes to publish events in batches or chain two Business Events into a connected workflow.</p>
        <a href="../business-events/recipes/">Find a recipe &rarr;</a>
      </article>
      <article class="h2-resource">
        <h3>Design for what comes next.</h3>
        <p>Explore payload design, schema versioning, retry behavior, and patterns for multiple independent consumers.</p>
        <a href="../business-events/design-guides/">Explore design guides &rarr;</a>
      </article>
    </div>
    <p>Need the platform reference? Read the <a href="https://learn.microsoft.com/fabric/real-time-hub/business-events/business-events-overview">official documentation</a>.</p>
  </section>

</div>
