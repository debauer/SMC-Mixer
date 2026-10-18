export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value == null) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.filter((child) => child != null && child !== false));
  return node;
}

export const KIND = {
  sink: { label: "OUTPUT", cls: "sink" },
  source: { label: "INPUT", cls: "source" },
};

export const pad = (n) => String(n).padStart(2, "0");
