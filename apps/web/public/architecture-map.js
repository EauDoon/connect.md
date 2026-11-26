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

function draw() {
  const points = new Map(visible.map((node) => [node.id, project(node.position)]));
  const graph = document.createDocumentFragment();
  for (const radius of [110, 200]) {
    graph.append(element("ellipse", { cx: 0, cy: 28, rx: radius, ry: radius / 3.4, fill: "none", stroke: "#334744", "stroke-dasharray": "2 6" }));
  }
  for (const edge of links) {
    const from = points.get(edge.from);
    const to = points.get(edge.to);
    const selected = edge.from === active || edge.to === active;
    graph.append(element("line", { x1: from.x, y1: from.y, x2: to.x, y2: to.y, stroke: selected ? colors[byId.get(edge.from).layer] : "#6c8384", "stroke-width": selected ? 2.8 : 1.5 }));
  }
  const sorted = [...visible].sort((a, b) => points.get(a.id).depth - points.get(b.id).depth);
  for (const node of sorted) {
    const point = points.get(node.id);
    const group = element("g", { "data-id": node.id, transform: "translate(" + point.x + " " + point.y + ")", cursor: "pointer" });
    const color = colors[node.layer];
    const radius = (node.id === active ? 10 : 6.5) * point.scale;
    group.append(element("circle", { r: radius + 10, fill: "transparent" }));
    if (node.id === active) group.append(element("circle", { r: radius + 5, fill: "none", stroke: color, "stroke-width": 1.5 }));
    group.append(element("circle", { r: radius, fill: color, stroke: "#101819", "stroke-width": 1.5 }));
    const label = element("text", { y: radius + 18, "text-anchor": "middle", fill: "#f5faf3", "font-size": 11, "font-weight": 600, stroke: "#101819", "stroke-width": 3, "paint-order": "stroke" });
    label.textContent = node.label;
    group.append(label);
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
filterNodes();
get("explorer").hidden = false;
