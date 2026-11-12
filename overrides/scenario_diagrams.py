"""Apply the shared reading size and connector-label style to all site diagrams."""

import json
import re

from mkdocs.structure.pages import Page


FENCE = re.compile(r"(?m)^```mermaid\n(.*?)^```[ \t]*$", re.S)
LABEL_STYLE = """
svg { max-width: none !important; }
.nodeLabel, .edgeLabel, .cluster-label { font-size: 14px !important; }
.edgeLabel, .edgeLabel p, .labelBkg { background: transparent !important; }
.edgeLabel rect, .labelBkg { fill: transparent !important; }
.nodeLabel, .edgeLabel, .cluster-label { color: var(--md-mermaid-label-fg-color); }
.cluster-label span, .cluster-label p { color: var(--md-mermaid-label-fg-color) !important; }
.cluster rect { fill: transparent; stroke: var(--md-mermaid-node-fg-color); }
.node rect, .node circle, .node ellipse, .node polygon, .node path {
  fill: var(--md-mermaid-node-bg-color);
  stroke: var(--md-mermaid-node-fg-color);
}
.flowchart-link { stroke: var(--md-mermaid-edge-color); }
marker path { fill: var(--md-mermaid-edge-color); }
"""


def style_diagram(match: re.Match) -> str:
    source = match.group(1)
    settings = {
        "themeCSS": LABEL_STYLE,
        "themeVariables": {
            "fontFamily": "var(--md-text-font-family)",
            "fontSize": "14px",
        },
        "flowchart": {
            "useMaxWidth": False,
            "htmlLabels": True,
            "diagramPadding": 16,
            "nodeSpacing": 32,
            "rankSpacing": 72,
            "padding": 8,
            "subGraphTitleMargin": {"top": 8, "bottom": 64},
        },
    }
    return "```mermaid\n%%{init: " + json.dumps(settings) + "}%%\n" + source + "```"


def on_page_markdown(markdown: str, page: Page, **kwargs) -> str:
    return FENCE.sub(style_diagram, markdown)


def wrap_diagram(match: re.Match) -> str:
    diagram = match.group(1).replace('class="mermaid"', 'class="scenario-mermaid"', 1)
    diagram = re.sub(
        r"(%%\{init: [^\n]+\}%%\n)",
        r'<span data-search-exclude="true">\1</span>',
        diagram,
        count=1,
    )
    return '<div class="scenario-diagram" role="region" aria-label="Diagram">' + diagram + "</div>"


def on_page_content(html: str, page: Page, **kwargs) -> str:
    return re.sub(
        r'(<pre class="mermaid">.*?</pre>)',
        wrap_diagram,
        html,
        flags=re.S,
    )
