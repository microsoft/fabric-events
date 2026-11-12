export function wrapGraph(graph, width) {
  const directive = graph.match(/^%%\{init: (.+)\}%%\n/);
  if (!directive) throw new Error("Missing shared scenario diagram configuration.");
  const settings = JSON.parse(directive[1]);
  const narrow = width < 500;
  const nodeWidth = narrow ? 160 : Math.min(200, Math.max(140, (width - 240) / 3));
  const edgeWidth = narrow ? Math.min(78, (width - 96) / 2) : Math.min(150, Math.max(100, (width - 320) / 3));
  const captionWidth = narrow ? Math.min(200, width - 96) : 260;
  settings.themeCSS += `
    .nodeLabel {
      display: inline-block; max-width: ${nodeWidth}px;
      white-space: normal !important; overflow-wrap: anywhere;
    }
    .edgeLabel, .labelBkg {
      max-width: ${edgeWidth}px; white-space: normal !important; overflow-wrap: anywhere;
    }
    .edgeLabel span { display: inline-block; }
    .labelBkg { max-width: ${edgeWidth}px !important; width: max-content !important; }
    .cluster-label span {
      display: inline-block; max-width: ${captionWidth}px; white-space: normal !important;
    }
  `;
  return "%%{init: " + JSON.stringify(settings) + "}%%\n" + graph.slice(directive[0].length);
}

export function graphParts(graph) {
  const nodes = [];
  const edges = [];
  const frames = [];
  let frame;
  for (const line of graph.split("\n")) {
    const heading = line.match(/^\s*subgraph\s+(\w+)\["/);
    if (heading) {
      if (frame) throw new Error("Nested scenario frames cannot be reflowed.");
      frame = {id: heading[1], nodes: []};
      frames.push(frame);
    } else if (/^\s*end\s*$/.test(line)) {
      frame = undefined;
    }
    const node = line.match(/^\s*(\w+)(?:\[\(|\(\[|\[|\{)"/);
    if (node) {
      nodes.push(node[1]);
      if (frame) frame.nodes.push(node[1]);
    }
    if (!/^\s*\w+\s+(?:-->|<-->|-\.->|---)/.test(line)) continue;
    for (const match of line.matchAll(/(?=(\b\w+)\s+(<-->|-->|-\.->|---)(?:\|"[^"]*"\|)?\s+(\w+))/g)) {
      edges.push({from: match[1], to: match[3]});
    }
  }
  return {nodes, edges, frames};
}

export function readingOrder(nodes, edges) {
  const remaining = new Set(nodes);
  const order = [];
  while (remaining.size) {
    // Feedback links retain their arrows; only their reading order is linearized.
    const next = [...remaining].find(node => !edges.some(edge =>
      edge.to === node && remaining.has(edge.from) && edge.from !== node
    )) || remaining.values().next().value;
    remaining.delete(next);
    order.push(next);
  }
  return order;
}

export function connectorOrder(edges, ids) {
  const remaining = new Set(ids);
  const order = edges.map(edge => {
    const prefix = `L_${edge.from}_${edge.to}_`;
    const matches = [...remaining].filter(id => id.startsWith(prefix) && /^\d+$/.test(id.slice(prefix.length)))
      .sort((a, b) => Number(a.slice(prefix.length)) - Number(b.slice(prefix.length)));
    if (!matches.length) throw new Error("Unrecognized diagram connector: " + prefix);
    remaining.delete(matches[0]);
    return matches[0];
  });
  if (remaining.size) throw new Error("Diagram connectors could not all be reflowed.");
  return order;
}

export function connectorFrames(edges, frames) {
  return edges.map(edge => frames.find(frame =>
    frame.nodes.includes(edge.from) && frame.nodes.includes(edge.to)
  )?.id);
}

export function connectionPorts(edges, center) {
  const port = (edge, endpoint) => {
    const siblings = edges.filter(other => other[endpoint] === edge[endpoint]);
    return center + (siblings.indexOf(edge) - (siblings.length - 1) / 2) * 12;
  };
  return edges.map(edge => ({startX: port(edge, "from"), endX: port(edge, "to")}));
}

export function connectorPath(points, routes = []) {
  let path = `M${points[0][0]},${points[0][1]}`;
  for (let index = 1; index < points.length; index++) {
    const [x, y] = points[index];
    const [previousX, previousY] = points[index - 1];
    if (y === previousY && x !== previousX) {
      const direction = Math.sign(x - previousX);
      const crossings = new Set();
      for (const route of routes) {
        for (let other = 1; other < route.length; other++) {
          const [a, b] = route[other - 1];
          const [c, d] = route[other];
          if (a === c && y > Math.min(b, d) + 6 && y < Math.max(b, d) - 6 &&
              a > Math.min(x, previousX) + 6 && a < Math.max(x, previousX) - 6) {
            crossings.add(a);
          }
        }
      }
      const groups = [];
      for (const crossing of [...crossings].sort((a, b) => direction * (a - b))) {
        const group = groups.at(-1);
        if (group && Math.abs(crossing - group.at(-1)) < 12) group.push(crossing);
        else groups.push([crossing]);
      }
      for (const group of groups) {
        const first = group[0];
        const last = group.at(-1);
        path += ` L${first - direction * 4},${y} Q${(first + last) / 2},${y - 8} ${last + direction * 4},${y}`;
      }
    }
    path += ` L${x},${y}`;
  }
  return path;
}

function nodeBoundary(element, x, bottom, svg) {
  const container = element.querySelector(".label-container");
  const shapes = container instanceof SVGGeometryElement ? [container] :
    [...container.querySelectorAll("path,rect,polygon,ellipse,circle")];
  const crossings = [];
  for (const shape of shapes) {
    const matrix = svg.getCTM().inverse().multiply(shape.getCTM());
    const length = shape.getTotalLength();
    let previous = shape.getPointAtLength(0).matrixTransform(matrix);
    for (let distance = 2; distance < length + 2; distance += 2) {
      const point = shape.getPointAtLength(Math.min(distance, length)).matrixTransform(matrix);
      if (Math.min(previous.x, point.x) <= x && Math.max(previous.x, point.x) >= x &&
          Math.abs(point.x - previous.x) > 0.001) {
        crossings.push(previous.y + (point.y - previous.y) * (x - previous.x) / (point.x - previous.x));
      }
      previous = point;
    }
  }
  if (!crossings.length) throw new Error("Could not attach a connector to its node outline.");
  return bottom ? Math.max(...crossings) : Math.min(...crossings);
}

export function stackDiagram(svg, graph, width) {
  const parts = graphParts(graph);
  const elements = new Map([...svg.querySelectorAll("g.node")].map(node => {
    const match = node.id.match(/-flowchart-(.+)-\d+$/);
    if (!match) throw new Error("Unrecognized scenario node: " + node.id);
    return [match[1], node];
  }));
  if (elements.size !== parts.nodes.length || parts.nodes.some(id => !elements.has(id))) {
    throw new Error("Scenario node definitions do not match the rendered diagram.");
  }
  const frames = parts.frames.map(frame => {
    const element = [...svg.querySelectorAll("g.cluster")].find(node => node.id.endsWith("-" + frame.id));
    if (!element || !frame.nodes.length) throw new Error("Unrecognized scenario frame: " + frame.id);
    return {...frame, element};
  });
  const frameFor = new Map(frames.flatMap(frame => frame.nodes.map(node => [node, frame])));
  const units = [...new Set(parts.nodes.map(node => frameFor.get(node)?.id || node))];
  const unitEdges = parts.edges.map(edge => ({
    from: frameFor.get(edge.from)?.id || edge.from,
    to: frameFor.get(edge.to)?.id || edge.to,
  })).filter(edge => edge.from !== edge.to);
  const order = readingOrder(units, unitEdges).flatMap(unit => {
    const frame = frames.find(candidate => candidate.id === unit);
    return frame ? readingOrder(frame.nodes, parts.edges.filter(edge =>
      frame.nodes.includes(edge.from) && frame.nodes.includes(edge.to)
    )) : [unit];
  });
  const labels = new Map([...svg.querySelectorAll("g.edgeLabels > g.edgeLabel")].map(label => [
    label.querySelector("[data-id]").getAttribute("data-id"), label,
  ]));
  const paths = new Map([...svg.querySelectorAll("path.flowchart-link")].map(path => [
    path.getAttribute("data-id"), path,
  ]));
  const connectorIds = connectorOrder(parts.edges, paths.keys());
  const counts = new Map();
  const edges = parts.edges.map((edge, index) => {
    const key = `${edge.from}_${edge.to}`;
    const count = counts.get(key) || 0;
    counts.set(key, count + 1);
    const id = connectorIds[index];
    const path = paths.get(id);
    if (!path) throw new Error("Unrecognized scenario connector: " + id);
    return {...edge, parallelIndex: count, path, label: labels.get(id)};
  });
  if (edges.length !== paths.size) throw new Error("Scenario connectors could not all be reflowed.");

  const group = name => {
    const element = document.createElementNS("http://www.w3.org/2000/svg", "g");
    element.setAttribute("class", name);
    svg.append(element);
    return element;
  };
  const frameGroup = group("clusters");
  const pathGroup = group("edgePaths");
  const nodeGroup = group("nodes");
  const labelGroup = group("edgeLabels");
  const center = width / 2;
  const positions = new Map();
  let cursor = 16;
  for (const [index, id] of order.entries()) {
    const frame = frameFor.get(id);
    if (frame && frame.nodes.every(node => !positions.has(node))) {
      frame.top = cursor;
      frame.caption = frame.element.querySelector(".cluster-label");
      frame.captionBox = frame.caption?.getBBox();
      cursor += frame.caption ? frame.captionBox.height + 64 : 16;
    }
    const element = elements.get(id);
    const box = element.getBBox();
    positions.set(id, {top: cursor, bottom: cursor + box.height, width: box.width, index});
    element.setAttribute("transform", `translate(${center - box.x - box.width / 2}, ${cursor - box.y})`);
    nodeGroup.append(element);
    cursor += box.height;
    if (frame && frame.nodes.every(node => positions.has(node))) {
      frame.bottom = cursor + 16;
      cursor += 16;
    }
    cursor += 24;
    for (const edge of edges.filter(edge => edge.from === id)) {
      edge.labelBox = edge.label?.getBBox();
      edge.labelTop = cursor;
      const height = edge.label?.textContent.trim() ? edge.labelBox.height : 0;
      edge.channel = cursor + height + 12;
      cursor += height + 32;
    }
    const next = order[index + 1];
    cursor += Math.max(32, edges.filter(edge => edge.to === next).length * 12 + 16);
  }
  const labelWidth = Math.max(0, ...edges.map(edge => edge.labelBox?.width || 0));
  for (const frame of frames) {
    frameGroup.append(frame.element);
    const frameWidth = Math.max(labelWidth * 2 + 56, Math.max(frame.captionBox?.width || 0,
      ...frame.nodes.map(node => positions.get(node).width)) + 24);
    const rect = frame.element.querySelector("rect");
    rect.setAttribute("x", center - frameWidth / 2);
    rect.setAttribute("y", frame.top);
    rect.setAttribute("width", frameWidth);
    rect.setAttribute("height", frame.bottom - frame.top);
    frame.caption?.setAttribute("transform",
      `translate(${center - frame.captionBox.width / 2 - frame.captionBox.x}, ${frame.top + 12 - frame.captionBox.y})`);
  }

  const incomingCounts = new Map();
  const lanes = [[], []];
  const ports = connectionPorts(edges, center);
  for (const [index, edge] of edges.entries()) {
    const source = positions.get(edge.from);
    const target = positions.get(edge.to);
    const incomingIndex = incomingCounts.get(edge.to) || 0;
    incomingCounts.set(edge.to, incomingIndex + 1);
    const entersFrame = frameFor.has(edge.to) && frameFor.get(edge.to) !== frameFor.get(edge.from) &&
      frameFor.get(edge.to).caption;
    const {startX, endX} = ports[index];
    const start = nodeBoundary(elements.get(edge.from), startX, true, svg) +
      (edge.path.hasAttribute("marker-start") ? 4 : 0);
    const end = nodeBoundary(elements.get(edge.to), endX, false, svg) -
      (edge.path.hasAttribute("marker-end") ? 4 : 0);
    const targetFrame = frameFor.get(edge.to);
    const entryTop = targetFrame && !targetFrame.caption && target.top === targetFrame.top + 16 ?
      targetFrame.top - 12 : target.top - 16;
    const endChannel = entryTop - incomingIndex * 12;
    let points;
    if (target.index === source.index + 1 && !entersFrame && edge.parallelIndex === 0) {
      points = [[startX, start], [startX, endChannel], [endX, endChannel], [endX, end]];
    } else {
      const interval = [Math.min(source.index, target.index), Math.max(source.index, target.index)];
      const freeLane = side => {
        let lane = 0;
        while (lanes[side][lane]?.some(other => other[0] <= interval[1] && other[1] >= interval[0])) lane++;
        return lane;
      };
      const left = freeLane(0);
      const right = freeLane(1);
      const side = startX < center ? 0 : startX > center ? 1 : left < right ? 0 : 1;
      const lane = side === 0 ? left : right;
      if (!lanes[side][lane]) lanes[side][lane] = [];
      lanes[side][lane].push(interval);
      const rail = side === 0 ? 8 + lane * 8 : width - 8 - lane * 8;
      points = [[startX, start], [startX, edge.channel], [rail, edge.channel],
        [rail, endChannel], [endX, endChannel], [endX, end]];
    }
    edge.points = points;
    edge.path.removeAttribute("data-points");
    pathGroup.append(edge.path);
    if (edge.label?.textContent.trim()) {
      edge.label.setAttribute("transform",
        `translate(${center + 20 - edge.labelBox.x}, ${edge.labelTop - edge.labelBox.y})`);
      labelGroup.append(edge.label);
    } else {
      edge.label?.remove();
    }
  }
  for (const edge of edges) {
    edge.path.setAttribute("d", connectorPath(edge.points,
      edges.filter(other => other !== edge).map(other => other.points)));
  }
  // Required responses remain visually dominant at optional-history crossings.
  for (const edge of edges.filter(edge => !edge.path.classList.contains("edge-pattern-dotted"))) {
    pathGroup.append(edge.path);
  }
  svg.setAttribute("viewBox", `0 0 ${width} ${cursor}`);
  svg.setAttribute("width", width);
  svg.setAttribute("height", cursor);
  svg.style.maxWidth = "none";
}
