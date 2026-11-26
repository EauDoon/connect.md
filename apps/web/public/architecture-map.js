// The source directory is also the no-script view and the map's single data source.
const entries = [...document.querySelectorAll(".directory > li")];
const nodes = entries.map((entry) => ({
  id: entry.id,
  label: entry.querySelector("h3").textContent,
  layer: entry.dataset.layer,
  position: entry.dataset.position.split(",").map(Number),
  description: entry.querySelector("p").textContent,
  source: entry.querySelector(".source").href,
}));
const edges = entries.flatMap((entry) => [...entry.querySelectorAll(".relations li")].map((link) => ({
  from: entry.id,
  to: link.querySelector("a").hash.slice(1),
  relation: link.querySelector("span").textContent,
})));
const byId = new Map(nodes.map((node) => [node.id, node]));
const colors = { Guest: "#d6ff76", Network: "#84d7eb", Reference: "#f2b6a0" };
const get = (id) => document.getElementById(id);
const scene = get("scene");
const filter = get("layer");
const initialAngle = 0.12;
let angle = initialAngle;
let active = byId.has(location.hash.slice(1)) ? location.hash.slice(1) : "draft";
let visible = nodes;
let links = edges;

function element(tag, attributes) {
  const shape = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) shape.setAttribute(key, String(value));
  return shape;
}

function project([x, y, z]) {
  const rotatedX = x * Math.cos(angle) - z * Math.sin(angle);
  const depth = x * Math.sin(angle) + z * Math.cos(angle);
  const scale = 520 / (600 - depth);
  return { x: rotatedX * scale, y: (y - depth * 0.17) * scale, depth, scale };
}

const textMeasure = document.createElement("canvas").getContext("2d");
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

function frame(points, width, height) {
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const left = Math.min(...xs), right = Math.max(...xs);
  const top = Math.min(...ys), bottom = Math.max(...ys);
  const margin = width < 460 ? 34 : 56;
  const scaleX = Math.min((width - 2 * margin) / Math.max(right - left, 1), width / 150);
  const scaleY = Math.min((height - 96) / Math.max(bottom - top, 1), height / 145);
  return points.map((point) => ({
    ...point,
    x: width / 2 + (point.x - (left + right) / 2) * scaleX,
    y: height / 2 - 6 + (point.y - (top + bottom) / 2) * scaleY,
  }));
}

function overlaps(a, b) {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

// Place text in screen pixels after projection, keeping all component names readable when the camera turns.
function placeLabels(points, width, height, fontSize) {
  textMeasure.font = `600 ${fontSize}px system-ui, sans-serif`;
  const taken = points.map(({ x, y, radius }) => ({ x: x - radius - 3, y: y - radius - 3, w: 2 * radius + 6, h: 2 * radius + 6 }));
  const positions = new Map();
  for (const point of points) {
    const w = Math.ceil(textMeasure.measureText(point.node.label).width) + 10;
    const h = fontSize + 8;
    let best = null;
    // Try nearby rows first. Side candidates let parallel layers retain their own labels in a tight view.
    for (let row = 0; row <= Math.ceil(height / h); row++) {
      for (const side of [1, -1]) {
        for (const shift of [0, -0.5, 0.5, -1, 1]) {
          const x = clamp(point.x - w / 2 + shift * w, 3, width - w - 3);
          const y = clamp(point.y + side * (point.radius + 5 + row * h) - (side < 0 ? h : 0), 3, height - h - 3);
          const box = { x, y, w, h };
          if (taken.some((other) => overlaps(box, other))) continue;
          const score = Math.hypot(x + w / 2 - point.x, y + h / 2 - point.y) + (side < 0 ? 3 : 0);
          if (!best || score < best.score) best = { ...box, score };
        }
      }
      if (best && row * h > best.score) break;
    }
    if (best) {
      positions.set(point.node.id, best);
      taken.push(best);
    }
  }
  return positions;
}

function nodeShape(layer, r, color) {
  const stroke = { fill: color, stroke: "#101819", "stroke-width": 1.5 };
  if (layer === "Network") return element("rect", { ...stroke, x: -r, y: -r, width: 2 * r, height: 2 * r, rx: 1.5 });
  if (layer === "Reference") return element("path", { ...stroke, d: `M 0 ${-r} L ${r} 0 0 ${r} ${-r} 0 Z` });
  return element("circle", { ...stroke, r });
}

function draw() {
  const width = scene.clientWidth, height = scene.clientHeight;
  if (!width || !height) return;
  scene.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const graph = document.createDocumentFragment();
  const fontSize = width < 460 ? 12 : 14;
  const points = frame(visible.map((node) => {
    const point = project(node.position);
    return { ...point, node, radius: (node.id === active ? 9 : 6) * clamp(point.scale, 0.9, 1.25) };
  }), width, height);
  const byNode = new Map(points.map((p) => [p.node.id, p]));
  // Draw straight, directed edges up to the node outline; keep the arrowheads outside the fill.
  for (const edge of links) {
    const from = byNode.get(edge.from), to = byNode.get(edge.to);
    const dx = to.x - from.x, dy = to.y - from.y, distance = Math.hypot(dx, dy);
    if (distance < from.radius + to.radius + 9) continue;
    const ux = dx / distance, uy = dy / distance;
    const x = to.x - ux * (to.radius + 3), y = to.y - uy * (to.radius + 3);
    const selected = edge.from === active || edge.to === active;
    const color = selected ? colors[from.node.layer] : "#829a9a";
    graph.append(element("line", { x1: from.x + ux * from.radius, y1: from.y + uy * from.radius, x2: x - ux * 5, y2: y - uy * 5, stroke: color, "stroke-width": selected ? 2.5 : 1.6 }));
    graph.append(element("path", { d: `M ${x} ${y} l ${-8 * ux + 3.5 * uy} ${-8 * uy - 3.5 * ux} l ${-7 * uy} ${7 * ux} Z`, fill: color }));
  }
  const ordered = [...points].sort((a, b) => Number(b.node.id === active) - Number(a.node.id === active) || b.depth - a.depth);
  const labels = placeLabels(ordered, width, height, fontSize);
  for (const { node, x, y, radius } of ordered.reverse()) {
    const color = colors[node.layer];
    const group = element("g", { "data-id": node.id, transform: `translate(${x} ${y})`, cursor: "pointer" });
    const box = labels.get(node.id);
    if (box) {
      const lx = clamp(x, box.x, box.x + box.w) - x;
      const ly = clamp(y, box.y, box.y + box.h) - y;
      if (Math.hypot(lx, ly) > radius + 12) group.append(element("line", { x1: 0, y1: 0, x2: lx, y2: ly, stroke: color, opacity: 0.7, "stroke-dasharray": "2 3" }));
    }
    group.append(element("circle", { r: Math.max(16, radius + 6), fill: "transparent" }));
    if (node.id === active) group.append(element("circle", { r: radius + 5, fill: "none", stroke: color, "stroke-width": 1.5 }));
    group.append(nodeShape(node.layer, radius, color));
    if (box) {
      const left = box.x - x, top = box.y - y;
      group.append(element("rect", { x: left, y: top, width: box.w, height: box.h, rx: 4, fill: "#101819", "fill-opacity": 0.94 }));
      const label = element("text", { x: left + box.w / 2, y: top + box.h / 2, "dominant-baseline": "central", "text-anchor": "middle", fill: node.id === active ? color : "#ecf3ee", "font-size": fontSize, "font-family": "system-ui, sans-serif", "font-weight": 600 });
      label.textContent = node.label;
      group.append(label);
    }
    graph.append(group);
  }
  scene.replaceChildren(graph);
}

function select(id) {
  active = id;
  const node = byId.get(id);
  get("kind").textContent = node.layer;
  get("selected").textContent = node.label;
  get("description").textContent = node.description;
  get("source").href = node.source;
  get("announcement").textContent = node.label + " selected. " + node.description;
  for (const choice of get("choices").children) choice.setAttribute("aria-pressed", String(choice.dataset.id === id));
  const related = links.filter((edge) => edge.from === id || edge.to === id);
  get("connections").replaceChildren(...related.map((edge) => {
    const target = edge.from === id ? edge.to : edge.from;
    const button = document.createElement("button");
    button.textContent = byId.get(edge.from).label + " " + edge.relation + " " + byId.get(edge.to).label;
    button.addEventListener("click", () => {
      select(target);
      get("choices").querySelector('[data-id="' + target + '"]').focus();
    });
    return button;
  }));
  draw();
}

function filterNodes() {
  visible = nodes.filter((node) => !filter.value || node.layer === filter.value);
  scene.dataset.layer = filter.value;
  const ids = new Set(visible.map((node) => node.id));
  links = edges.filter((edge) => ids.has(edge.from) && ids.has(edge.to));
  const choices = visible.map((node) => {
    const button = document.createElement("button");
    button.textContent = node.label;
    button.dataset.id = node.id;
    button.addEventListener("click", () => select(node.id));
    return button;
  });
  get("choices").replaceChildren(...choices);
  const architecture = { nodes: visible.map(({ id, label, layer }) => ({ id, label, layer })), edges: links };
  get("result").textContent = JSON.stringify({ data: { architecture } }, null, 2);
  select(ids.has(active) ? active : visible[0].id);
}

filter.addEventListener("change", filterNodes);
get("left").addEventListener("click", () => { angle -= Math.PI / 10; draw(); });
get("right").addEventListener("click", () => { angle += Math.PI / 10; draw(); });
get("reset").addEventListener("click", () => { angle = initialAngle; draw(); });

let drag;
scene.addEventListener("pointerdown", (event) => {
  if (!event.isPrimary || event.button !== 0) return;
  drag = { id: event.pointerId, startX: event.clientX, startAngle: angle, moved: false, node: event.target.closest("[data-id]")?.dataset.id };
  scene.setPointerCapture(event.pointerId);
});
scene.addEventListener("pointermove", (event) => {
  if (!drag || drag.id !== event.pointerId) return;
  const distance = event.clientX - drag.startX;
  if (!drag.moved && Math.abs(distance) < 5) return;
  drag.moved = true;
  angle = drag.startAngle + distance * 0.009;
  draw();
});
scene.addEventListener("pointerup", (event) => {
  if (!drag || drag.id !== event.pointerId) return;
  if (!drag.moved && drag.node) select(drag.node);
  drag = undefined;
});
scene.addEventListener("lostpointercapture", () => { drag = undefined; });
get("explorer").hidden = false;
filterNodes();
new ResizeObserver(draw).observe(scene);
