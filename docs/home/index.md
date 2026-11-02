---
title: Home
hide:
  - navigation
  - toc
---

<style>
.md-typeset .home2 {
  max-width: 1160px;
  margin: 0 auto;
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
.md-typeset .home2 h1 {
  margin: 0 0 .75em;
  font-size: 2em;
  line-height: 1.2;
}
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
.home2 .h2-brand { font-weight: 700; }
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
.home2 .h2-pillars {
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
.home2 a:focus-visible {
  outline: 2px solid var(--md-accent-fg-color);
  outline-offset: 4px;
}
@media (max-width: 900px) {
  .home2 .h2-hero,
  .home2 .h2-scenes,
  .home2 .h2-pillars { grid-template-columns: 1fr; gap: 1.5em; }
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
      <p class="h2-brand">Fabric Events</p>
      <h1 id="home2-title">One event.<br>The right action.</h1>
      <p>A sales dip. A delayed shipment. A moment that matters to your business.</p>
      <p><strong>Business Events</strong> let Fabric workloads publish these moments as named, schema-defined signals. Independent consumers turn them into alerts, workflows, and analytics.</p>
      <div class="h2-actions">
        <a class="md-button md-button--primary" href="../business-events/scenarios/sales-volume-alert/">Build your first event</a>
        <a href="../business-events/introduction/what-are-business-events/">Understand the basics &rarr;</a>
      </div>
    </div>
    <figure>
      <img src="../assets/home2/v001/event-to-action.png" width="1200" height="675" fetchpriority="high" alt="A store's falling sales become a Business Event, which connects independently to a notification and an analytics view.">
      <figcaption>Detect a moment. Publish an event. Let consumers react.</figcaption>
    </figure>
  </section>

  <section class="h2-section" aria-labelledby="home2-scenarios-title">
    <h2 id="home2-scenarios-title">Find a moment worth acting on.</h2>
    <div class="h2-scenes">
      <article class="h2-scene">
        <img src="../assets/home2/v001/sales-alert.png" width="1200" height="675" loading="lazy" alt="A retail storefront with a chart showing falling sales.">
        <h3>Alert the team.</h3>
        <p>A Notebook detects a sales dip. A Business Event triggers an Activator rule that sends a Teams alert.</p>
        <a href="../business-events/scenarios/sales-volume-alert/">Build a sales volume alert &rarr;</a>
      </article>
      <article class="h2-scene">
        <img src="../assets/home2/v001/cart-recovery.png" width="1200" height="675" loading="lazy" alt="A shopping bag and abandoned cart connect to a recovery email.">
        <h3>Bring a customer back.</h3>
        <p>A cart abandonment event lets independent consumers trigger recovery actions and capture conversion analytics.</p>
        <a href="../business-events/scenarios/abandoned-cart-recovery/">Explore cart recovery &rarr;</a>
      </article>
      <article class="h2-scene">
        <img src="../assets/home2/v001/shipment-response.png" width="1200" height="675" loading="lazy" alt="A delivery truck follows an alternative route toward a destination marker.">
        <h3>Respond to a delay.</h3>
        <p>A shipment delay event triggers mitigation actions, notifies the customer, and records the incident for analysis.</p>
        <a href="../business-events/scenarios/shipment-delay-auto-mitigation/">Explore shipment mitigation &rarr;</a>
      </article>
    </div>
    <p><a href="../business-events/scenarios/">Browse all scenarios &rarr;</a></p>
  </section>

  <section class="h2-section" aria-labelledby="home2-pillars-title">
    <h2 id="home2-pillars-title">Three pillars. One event-driven toolkit.</h2>
    <div class="h2-pillars">
      <div class="h2-pillar">
        <h3>Business Events</h3>
        <p>Signals your workloads publish when something matters to the business.</p>
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
    <h2 id="home2-resources-title">Go from an idea to a working solution.</h2>
    <p><a href="../business-events/recipes/">Recipes</a> &middot; <a href="../business-events/design-guides/">Design guides</a> &middot; <a href="https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/business-events-overview">Official documentation</a> &middot; <a href="https://github.com/microsoft/fabric-events/discussions">Join the community</a></p>
  </section>
</div>
