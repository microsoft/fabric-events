import {connectorFrames, connectorOrder, frameLabelBays, graphParts, stackDiagram, wrapGraph} from "./scenario-diagram-layout.mjs";

(() => {
  const gap = 8;
  let library;
  let sequence = 0;
  const diagrams = new Map();
  const observer = new ResizeObserver(entries => {
    for (const {target} of entries) requestLayout(target);
  });

  function loadMermaid() {
    if (!library) {
      library = new Promise((resolve, reject) => {
        if (window.mermaid && typeof window.mermaid.render === "function") {
          resolve(window.mermaid);
          return;
        }
        const script = document.createElement("script");
        script.src = "https://unpkg.com/mermaid@11/dist/mermaid.min.js";
        script.onload = () => resolve(window.mermaid);
        script.onerror = () => reject(new Error("Could not load Mermaid."));
        document.head.append(script);
      });
    }
    return library;
  }

  function intersects(a, b, margin = 0) {
    return a.left < b.right + margin && a.right > b.left - margin &&
      a.top < b.bottom + margin && a.bottom > b.top - margin;
  }

  function extractFrameLegends(svg, graph) {
    const legends = [];
    for (const {id, nodes} of graphParts(graph).frames) {
      if (id !== "GAP" && id !== "VALUE") continue;
      const cluster = [...svg.querySelectorAll("g.cluster")].find(element => element.id.endsWith("-" + id));
      const caption = cluster.querySelector(".cluster-label");
      const frame = cluster.querySelector("rect");
      const bounds = frame.getBoundingClientRect();
      const top = Math.min(...nodes.map(node => {
        const element = [...svg.querySelectorAll("g.node")].find(element =>
          element.id.match(/-flowchart-(.+)-\d+$/)?.[1] === node);
        return element.getBoundingClientRect().top;
      })) - 16;
      const inverse = frame.getScreenCTM().inverse();
      const start = new DOMPoint(bounds.left, top).matrixTransform(inverse);
      const end = new DOMPoint(bounds.right, bounds.bottom).matrixTransform(inverse);
      frame.setAttribute("y", start.y);
      frame.setAttribute("height", end.y - start.y);
      legends.push({caption, frame, color: getComputedStyle(frame).stroke});
      caption.remove();
    }
    return legends;
  }

  function placeFrameLegends(svg, legends, width, padding) {
    const inverse = svg.getScreenCTM().inverse();
    const boxInSvg = element => {
      const bounds = element.getBoundingClientRect();
      const start = new DOMPoint(bounds.left, bounds.top).matrixTransform(inverse);
      const end = new DOMPoint(bounds.right, bounds.bottom).matrixTransform(inverse);
      return {left: start.x, right: end.x, top: start.y, bottom: end.y};
    };
    const obstacles = [...svg.querySelectorAll("g.node, g.edgeLabels > g.edgeLabel, .cluster-label, g.cluster > rect")]
      .map(boxInSvg);
    const points = [];
    for (const path of svg.querySelectorAll("path.flowchart-link")) {
      const matrix = inverse.multiply(path.getScreenCTM());
      for (let distance = 0; distance <= path.getTotalLength(); distance += 2) {
        points.push(path.getPointAtLength(distance).matrixTransform(matrix));
      }
    }
    const bounds = svg.getBBox();
    let footerTop = bounds.y + bounds.height + 24;
    for (const {caption, frame, color} of legends) {
      const current = svg.getBBox();
      const key = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      svg.append(key, caption);
      const box = caption.getBBox();
      const frameBox = boxInSvg(frame);
      const candidate = {
        left: frameBox.right + 24, right: frameBox.right + 48 + box.width,
        top: (frameBox.top + frameBox.bottom - box.height) / 2,
        bottom: (frameBox.top + frameBox.bottom + box.height) / 2,
      };
      const beside = Math.max(current.x + current.width, candidate.right) -
        Math.min(current.x, candidate.left) + 2 * padding <= width &&
        !obstacles.some(obstacle => intersects(candidate, obstacle, gap)) &&
        !points.some(point => point.x > candidate.left - gap && point.x < candidate.right + gap &&
          point.y > candidate.top - gap && point.y < candidate.bottom + gap);
      const left = beside ? candidate.left : bounds.x;
      const top = beside ? candidate.top : footerTop;
      key.setAttribute("x", left);
      key.setAttribute("y", top + box.height / 2 - 7);
      key.setAttribute("width", 14);
      key.setAttribute("height", 14);
      key.setAttribute("fill", "none");
      key.setAttribute("stroke", color);
      key.setAttribute("stroke-width", 1.5);
      key.setAttribute("aria-hidden", "true");
      caption.setAttribute("transform", `translate(${left + 24 - box.x},${top - box.y})`);
      obstacles.push({left, right: left + 24 + box.width, top, bottom: top + box.height});
      footerTop = Math.max(footerTop, top + box.height + 24);
    }
  }

  function clearCaptions(svg) {
    for (const caption of svg.querySelectorAll(".cluster-label")) {
      const box = caption.getBoundingClientRect();
      for (const path of svg.querySelectorAll("path.flowchart-link")) {
        const matrix = path.getScreenCTM();
        const length = path.getTotalLength();
        let crosses = false;
        for (let distance = 0; distance <= length; distance += 2) {
          const point = path.getPointAtLength(distance).matrixTransform(matrix);
          if (point.x > box.left - gap && point.x < box.right + gap &&
              point.y > box.top - gap && point.y < box.bottom + gap) {
            crosses = true;
            break;
          }
        }
        if (!crosses) continue;
        const start = path.getPointAtLength(0).matrixTransform(matrix);
        const end = path.getPointAtLength(length).matrixTransform(matrix);
        const down = end.y > start.y;
        const before = down ? box.top - 16 : box.bottom + 16;
        const after = down ? box.bottom + 16 : box.top - 16;
        const frame = caption.closest(".cluster").querySelector("rect").getBoundingClientRect();
        const rails = [Math.min(box.left, frame.left) - 16, Math.max(box.right, frame.right) + 16];
        rails.sort((a, b) => Math.abs(a - start.x) + Math.abs(a - end.x) -
          Math.abs(b - start.x) - Math.abs(b - end.x));
        const rail = rails[0];
        const inverse = matrix.inverse();
        const points = [start, {x: start.x, y: before}, {x: rail, y: before},
          {x: rail, y: after}, {x: end.x, y: after}, end]
          .map(point => new DOMPoint(point.x, point.y).matrixTransform(inverse));
        path.setAttribute("d", points.map((point, index) =>
          `${index ? "L" : "M"}${point.x},${point.y}`).join(" "));
        path.removeAttribute("data-points");
        const label = [...svg.querySelectorAll("g.edgeLabels > g.edgeLabel")]
          .find(element => element.querySelector("[data-id]")?.getAttribute("data-id") === path.dataset.id);
        if (label) {
          const anchor = new DOMPoint(rail, (before + after) / 2)
            .matrixTransform(label.parentElement.getScreenCTM().inverse());
          label.setAttribute("transform", `translate(${anchor.x},${anchor.y})`);
        }
      }
    }
  }

  function placeLabels(svg, graph, vertical) {
    const {edges, frames} = graphParts(graph);
    const internalFrames = connectorFrames(edges, frames);
    const frameBoxes = new Map(frames.map(frame => [
      frame.id, [...svg.querySelectorAll("g.cluster")].find(element => element.id.endsWith("-" + frame.id))
        .querySelector("rect").getBoundingClientRect(),
    ]));
    const ids = connectorOrder(edges, [...svg.querySelectorAll("path.flowchart-link")].map(path => path.dataset.id));
    const labelFrames = new Map(ids.map((id, index) => [id, frameBoxes.get(internalFrames[index])]));
    const scale = svg.getScreenCTM().a;
    const paths = [...svg.querySelectorAll("path.flowchart-link")].map(path => {
      const points = [];
      const length = path.getTotalLength();
      const matrix = path.getScreenCTM();
      for (let distance = 0; distance <= length; distance += 2) {
        points.push(path.getPointAtLength(distance).matrixTransform(matrix));
      }
      return points;
    });
    const obstacles = [...svg.querySelectorAll("g.node, .cluster-label")]
      .map(node => node.getBoundingClientRect());
    for (const frame of svg.querySelectorAll("g.cluster > rect")) {
      const box = frame.getBoundingClientRect();
      obstacles.push(
        {left: box.left, right: box.right, top: box.top, bottom: box.top + 1},
        {left: box.left, right: box.right, top: box.bottom - 1, bottom: box.bottom},
        {left: box.left, right: box.left + 1, top: box.top, bottom: box.bottom},
        {left: box.right - 1, right: box.right, top: box.top, bottom: box.bottom},
      );
    }
    const labels = [...svg.querySelectorAll("g.edgeLabels > g.edgeLabel")].map(label => ({
      foreign: label.querySelector("foreignObject"),
      anchor: new DOMPoint(0, 0).matrixTransform(label.getScreenCTM()),
      frame: labelFrames.get(label.querySelector("[data-id]").getAttribute("data-id")),
    }));

    // Reserve larger labels first so smaller labels can use the remaining gaps.
    labels.sort((a, b) => b.foreign.getBoundingClientRect().width -
      a.foreign.getBoundingClientRect().width);
    for (const {foreign, anchor, frame} of labels) {
      const original = foreign.getBoundingClientRect();
      const candidates = [];
      for (let distance = gap; distance <= 160; distance += gap) {
        for (const along of [0, -16, 16, -32, 32, -64, 64]) {
          const positions = vertical ? [
            [anchor.x + distance, anchor.y - original.height / 2 + along],
            [anchor.x - original.width - distance, anchor.y - original.height / 2 + along],
          ] : [
            [anchor.x - original.width / 2 + along, anchor.y - original.height - distance],
          ];
          for (const [left, top] of positions) {
            candidates.push({
              left, top, right: left + original.width, bottom: top + original.height,
              cost: distance + Math.abs(along),
            });
          }
        }
      }
      candidates.sort((a, b) => a.cost - b.cost);
      const chosen = candidates.find(box =>
        (!frame || box.left >= frame.left + gap && box.right <= frame.right - gap &&
          box.top >= frame.top + gap && box.bottom <= frame.bottom - gap) &&
        !obstacles.some(obstacle => intersects(box, obstacle, gap)) &&
        !paths.some(points => points.some(point =>
          point.x >= box.left - gap && point.x <= box.right + gap &&
          point.y >= box.top - gap && point.y <= box.bottom + gap
        ))
      );
      if (!chosen) {
        return false;
      }
      foreign.setAttribute("x", foreign.x.baseVal.value + (chosen.left - original.left) / scale);
      foreign.setAttribute("y", foreign.y.baseVal.value + (chosen.top - original.top) / scale);
      obstacles.push(chosen);
    }
    return true;
  }

  function reserveFrameLabelSpace(svg, graph, width, vertical) {
    const {edges, frames} = graphParts(graph);
    const internalFrames = connectorFrames(edges, frames);
    const ids = connectorOrder(edges, [...svg.querySelectorAll("path.flowchart-link")].map(path => path.dataset.id));
    const labelFrames = new Map(ids.map((id, index) => [id, internalFrames[index]]));
    const matrix = svg.getScreenCTM();
    const bounds = svg.getBBox();
    const left = new DOMPoint(bounds.x, bounds.y).matrixTransform(matrix).x;
    const right = new DOMPoint(bounds.x + bounds.width, bounds.y).matrixTransform(matrix).x;
    for (const frame of frames) {
      const cluster = [...svg.querySelectorAll("g.cluster")].find(element => element.id.endsWith("-" + frame.id));
      if (cluster.querySelector(".cluster-label")) continue;
      const rect = cluster.querySelector("rect");
      const box = rect.getBoundingClientRect();
      const blockers = [...svg.querySelectorAll("g.node")].filter(node =>
        !frame.nodes.includes(node.id.match(/-flowchart-(.+)-\d+$/)?.[1])
      ).map(node => node.getBoundingClientRect());
      blockers.push(...[...svg.querySelectorAll("g.cluster > rect")].filter(other => other !== rect)
        .map(other => other.getBoundingClientRect()));
      const labels = [...svg.querySelectorAll("g.edgeLabels > g.edgeLabel")].filter(label =>
        labelFrames.get(label.querySelector("[data-id]").getAttribute("data-id")) === frame.id
      );
      const labelWidth = Math.max(0, ...labels.map(label => label.getBoundingClientRect().width));
      const originalX = rect.getAttribute("x");
      const originalWidth = rect.getAttribute("width");
      const inverse = rect.getScreenCTM().inverse();
      for (const bay of frameLabelBays({
        left: box.left, right: box.right, top: box.top, bottom: box.bottom,
      }, blockers, {left, right}, width, labelWidth)) {
        const start = new DOMPoint(bay.left, bay.top).matrixTransform(inverse);
        const end = new DOMPoint(bay.right, bay.top).matrixTransform(inverse);
        rect.setAttribute("x", start.x);
        rect.setAttribute("width", end.x - start.x);
        if (placeLabels(svg, graph, vertical)) return true;
      }
      rect.setAttribute("x", originalX);
      rect.setAttribute("width", originalWidth);
    }
    return false;
  }

  function fitBounds(svg, padding = 16) {
    const bounds = svg.getBBox();
    const inverse = svg.getScreenCTM().inverse();
    const corners = [
      {x: bounds.x, y: bounds.y},
      {x: bounds.x + bounds.width, y: bounds.y + bounds.height},
    ];
    for (const foreign of svg.querySelectorAll("foreignObject")) {
      const box = foreign.getBoundingClientRect();
      corners.push(
        new DOMPoint(box.left, box.top).matrixTransform(inverse),
        new DOMPoint(box.right, box.bottom).matrixTransform(inverse),
      );
    }
    const left = Math.min(...corners.map(point => point.x));
    const top = Math.min(...corners.map(point => point.y));
    const right = Math.max(...corners.map(point => point.x));
    const bottom = Math.max(...corners.map(point => point.y));
    svg.setAttribute("viewBox",
      `${left - padding} ${top - padding} ${right - left + 2 * padding} ${bottom - top + 2 * padding}`);
    svg.setAttribute("width", right - left + 2 * padding);
    svg.setAttribute("height", bottom - top + 2 * padding);
    svg.style.maxWidth = "none";
  }

  function renderDiagrams() {
    for (const [region] of diagrams) {
      if (!region.isConnected) {
        observer.unobserve(region);
        diagrams.delete(region);
      }
    }
    const sources = [...document.querySelectorAll("pre.scenario-mermaid:not([data-rendering])")];
    for (const source of sources) {
      source.dataset.rendering = "true";
      const region = source.closest(".scenario-diagram");
      diagrams.set(region, {graph: source.textContent, width: 0, rendering: false});
      observer.observe(region);
      requestLayout(region);
    }
  }

  async function requestLayout(region) {
    const state = diagrams.get(region);
    const width = Math.floor(region.clientWidth);
    if (!state || width <= 0 || state.width === width) return;
    state.width = width;
    if (state.rendering) return;
    state.rendering = true;
    let host;
    try {
      const mermaid = await loadMermaid();
      await document.fonts.ready;
      mermaid.initialize({startOnLoad: false});
      do {
        const available = state.width;
        const graph = wrapGraph(state.graph, available);
        const {svg, bindFunctions} = await mermaid.render(`scenario-diagram-${sequence++}`, graph);
        if (!region.isConnected) break;
        host = document.createElement("div");
        host.className = "scenario-mermaid";
        host.dataset.measuring = "true";
        const shadow = host.attachShadow({mode: "open"});
        shadow.innerHTML = svg;
        region.append(host);
        const diagram = shadow.querySelector("svg");
        const legends = extractFrameLegends(diagram, graph);
        clearCaptions(diagram);
        const vertical = /^\s*flowchart TB\b/m.test(graph);
        const placed = placeLabels(diagram, graph, vertical) ||
          reserveFrameLabelSpace(diagram, graph, available, vertical);
        if (placed) fitBounds(diagram);
        if (!placed || diagram.getBoundingClientRect().width > available + 0.5) {
          stackDiagram(diagram, graph, available);
          fitBounds(diagram, 8);
          host.dataset.layout = "stacked";
        } else {
          host.dataset.layout = "native";
        }
        if (legends.length) {
          const padding = host.dataset.layout === "stacked" ? 8 : 16;
          placeFrameLegends(diagram, legends, available, padding);
          fitBounds(diagram, padding);
        }
        if (diagram.getBoundingClientRect().width > available + 0.5) {
          throw new Error("Diagram exceeds its available reading width.");
        }
        if (available !== state.width) {
          host.remove();
          continue;
        }
        if (bindFunctions) bindFunctions(shadow);
        host.removeAttribute("data-measuring");
        host.dataset.width = String(available);
        host.dataset.ready = "true";
        region.replaceChildren(host);
        host = undefined;
        if (available === state.width) break;
      } while (region.isConnected);
    } catch (error) {
      host?.remove();
      showError(region, error);
    } finally {
      state.rendering = false;
    }
  }

  function showError(region, error) {
    const message = document.createElement("p");
    message.setAttribute("role", "alert");
    message.textContent = "This diagram could not be rendered. Please reload the page.";
    region.prepend(message);
    console.error("Diagram:", error);
  }

  if (typeof document$ !== "undefined") {
    document$.subscribe(renderDiagrams);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderDiagrams);
  } else {
    renderDiagrams();
  }
})();
