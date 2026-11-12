import assert from "node:assert/strict";
import test from "node:test";
import {connectionPorts, connectorFrames, connectorOrder, connectorPath, graphParts, readingOrder, wrapGraph} from "../docs/javascripts/scenario-diagram-layout.mjs";

test("reflow recognizes chains, independent branches, feedback and bidirectional arrows", () => {
  const graph = `flowchart TB
    APP["App"]
    UDF["Publisher"]
    BE(["Event"])
    FLOW["Flow"]
    TEAMS["Teams"]
    HISTORY[("History")]
    APP -->|"Facts"| UDF
    UDF -->|"Return"| APP
    UDF --> BE --> FLOW
    FLOW <-->|"Request<br/>Response"| TEAMS
    BE -.->|"Analyze"| HISTORY`;
  const {nodes, edges} = graphParts(graph);
  assert.deepEqual(edges, [
    {from: "APP", to: "UDF"}, {from: "UDF", to: "APP"},
    {from: "UDF", to: "BE"}, {from: "BE", to: "FLOW"},
    {from: "FLOW", to: "TEAMS"}, {from: "BE", to: "HISTORY"},
  ]);
  assert.deepEqual(readingOrder(nodes, edges), ["APP", "UDF", "BE", "FLOW", "TEAMS", "HISTORY"]);
  assert.equal(edges.some(edge => edge.from === "FLOW" && edge.to === "HISTORY"), false);
});

test("frame membership excludes shared inputs and independent outcomes", () => {
  const {nodes, frames, edges} = graphParts(`flowchart TB
    INPUT[("Catalog")]
    subgraph VALUE["Improvement"]
      MATCH["Match"]
      RESULT[("Candidates")]
    end
    EXCLUDED["Inactive"]
    INPUT --> MATCH
    MATCH --> RESULT
    MATCH --> EXCLUDED`);
  assert.deepEqual(nodes, ["INPUT", "MATCH", "RESULT", "EXCLUDED"]);
  assert.deepEqual(frames, [{id: "VALUE", nodes: ["MATCH", "RESULT"]}]);
  assert.equal(edges.length, 3);
});

test("internal connector labels belong to their frame, not entering or leaving links", () => {
  const {frames, edges} = graphParts(`flowchart TB
    SOURCE["Reported delay"]
    subgraph GAP["Coordination gap: Receipt is separate"]
      MESSAGE["Message"]
      RESPONDER["Responder"]
      RECORD["Record"]
    end
    subgraph VALUE["Improvement: Receipt is part of the flow"]
      REQUEST["Request"]
      RESPONSE["Response"]
    end
    SOURCE --> MESSAGE
    MESSAGE --> RESPONDER
    RESPONDER --> RECORD
    RECORD --> REQUEST
    REQUEST --> RESPONSE
    RESPONSE --> REQUEST
    MESSAGE --> RESPONDER`);
  assert.deepEqual(connectorFrames(edges, frames),
    [undefined, "GAP", "GAP", undefined, "VALUE", "VALUE", "GAP"]);
});

test("site diagrams retain multiple frames, undirected organization and parallel retry edges", () => {
  const {nodes, frames, edges} = graphParts(`flowchart TB
    subgraph REGISTRY["Definition"]
      REG["Registry"]
      SET["Schema set"]
      EVENT(["Event"])
      REG ---|"organizes"| SET
      SET ---|"contains"| EVENT
    end
    subgraph CONSUMERS["Consumers"]
      CONSUMER["Consumer"]
    end
    EVENT -->|"Delivery"| CONSUMER
    CONSUMER -->|"Failure"| EVENT
    EVENT -->|"Retry"| CONSUMER`);
  assert.deepEqual(nodes, ["REG", "SET", "EVENT", "CONSUMER"]);
  assert.deepEqual(frames, [
    {id: "REGISTRY", nodes: ["REG", "SET", "EVENT"]},
    {id: "CONSUMERS", nodes: ["CONSUMER"]},
  ]);
  assert.deepEqual(edges, [
    {from: "REG", to: "SET"}, {from: "SET", to: "EVENT"},
    {from: "EVENT", to: "CONSUMER"}, {from: "CONSUMER", to: "EVENT"},
    {from: "EVENT", to: "CONSUMER"},
  ]);
  assert.deepEqual(connectorOrder(edges, [
    "L_REG_SET_0", "L_SET_EVENT_0", "L_EVENT_CONSUMER_2",
    "L_CONSUMER_EVENT_0", "L_EVENT_CONSUMER_0",
  ]), [
    "L_REG_SET_0", "L_SET_EVENT_0", "L_EVENT_CONSUMER_0",
    "L_CONSUMER_EVENT_0", "L_EVENT_CONSUMER_2",
  ]);
  assert.throws(() => connectorOrder(edges, ["L_REG_SET_0"]), /Unrecognized diagram connector/);
  assert.throws(() => connectorOrder([], ["L_REG_SET_0"]), /could not all be reflowed/);
});

test("responsive wrapping preserves the graph, font floor and transparent label styles", () => {
  const settings = {
    themeVariables: {fontSize: "14px"},
    themeCSS: ".labelBkg { background: transparent !important; }",
    flowchart: {useMaxWidth: false},
  };
  const body = 'flowchart TB\nA["Source"]\nB[("History")]\nA -.->|"Analyze"| B\n';
  const graph = `%%{init: ${JSON.stringify(settings)}}%%\n${body}`;
  for (const width of [340, 680, 1100]) {
    const wrapped = wrapGraph(graph, width);
    const [directive, ...preserved] = wrapped.split("\n");
    const config = JSON.parse(directive.slice("%%{init: ".length, -"}%%".length));
    assert.equal(preserved.join("\n"), body);
    assert.deepEqual(config.themeVariables, settings.themeVariables);
    assert.deepEqual(config.flowchart, settings.flowchart);
    assert.ok(config.themeCSS.startsWith(settings.themeCSS));
    assert.ok(config.themeCSS.includes("overflow-wrap: anywhere"));
  }
  assert.notEqual(wrapGraph(graph, 340), wrapGraph(graph, 1100));
  assert.throws(() => wrapGraph(body, 340), /Missing shared/);
});

test("fanout, convergence and parallel connections use separate attachment points", () => {
  const edges = [
    {from: "EVENT", to: "HOLD"}, {from: "EVENT", to: "REVIEW"},
    {from: "EVENT", to: "RELEASE"}, {from: "HOLD", to: "OMS"},
    {from: "REVIEW", to: "OMS"}, {from: "RELEASE", to: "OMS"},
    {from: "OMS", to: "EVENT"}, {from: "EVENT", to: "REVIEW"},
  ];
  const ports = connectionPorts(edges, 150);
  assert.equal(new Set([0, 1, 2, 7].map(index => ports[index].startX)).size, 4);
  assert.deepEqual([3, 4, 5].map(index => ports[index].endX), [138, 150, 162]);
  assert.notEqual(ports[1].endX, ports[7].endX);
  assert.equal(ports[6].startX, 150);
  assert.deepEqual(connectionPorts([{from: "SOURCE", to: "TARGET"}], 150),
    [{startX: 150, endX: 150}]);
});

test("perpendicular routes cross with a visible hop, not a false junction", () => {
  const vertical = [[50, 0], [50, 100]];
  assert.equal(connectorPath([[0, 40], [100, 40]], [vertical]),
    "M0,40 L46,40 Q50,32 54,40 L100,40");
  assert.equal(connectorPath([[100, 40], [0, 40]], [vertical]),
    "M100,40 L54,40 Q50,32 46,40 L0,40");
  assert.equal(connectorPath([[0, 0], [50, 0]], [vertical]), "M0,0 L50,0");
  assert.equal(connectorPath([[50, 20], [50, 80]], [vertical]), "M50,20 L50,80");
  assert.equal(connectorPath([[0, 40], [100, 40]], [vertical, [[58, 0], [58, 100]]]),
    "M0,40 L46,40 Q54,32 62,40 L100,40");
});
