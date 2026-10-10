// Picking one element on the page: hover to outline it, click to take it, ↑/↓ to widen or narrow, Esc to stop.

const Z = "2147483647";

export function pick(doc = document) {
  return new Promise((resolve) => {
    const win = doc.defaultView;
    const box = doc.createElement("div");
    const tip = doc.createElement("div");
    Object.assign(box.style, {
      position: "fixed",
      pointerEvents: "none",
      zIndex: Z,
      border: "2px solid #6d5dfc",
      background: "rgba(109, 93, 252, 0.12)",
      borderRadius: "3px",
      transition: "all 60ms ease-out",
    });
    Object.assign(tip.style, {
      position: "fixed",
      zIndex: Z,
      pointerEvents: "none",
      left: "50%",
      bottom: "16px",
      transform: "translateX(-50%)",
      font: "13px/1.4 system-ui, sans-serif",
      color: "#fff",
      background: "rgba(20, 20, 30, 0.92)",
      padding: "8px 12px",
      borderRadius: "8px",
      boxShadow: "0 4px 16px rgba(0,0,0,.25)",
    });
    tip.textContent = "Lens: click to clip · ↑ wider · ↓ narrower · Esc to cancel";
    doc.documentElement.append(box, tip);

    let current = null;
    const trail = [];
    const show = (el) => {
      current = el;
      const r = el.getBoundingClientRect();
      Object.assign(box.style, { left: `${r.left - 2}px`, top: `${r.top - 2}px`, width: `${r.width + 4}px`, height: `${r.height + 4}px` });
      tip.textContent = `Lens: ${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""} · click to clip · ↑ wider · ↓ narrower · Esc to cancel`;
    };
    const finish = (el) => {
      win.removeEventListener("mousemove", onMove, true);
      win.removeEventListener("click", onClick, true);
      win.removeEventListener("keydown", onKey, true);
      box.remove();
      tip.remove();
      resolve(el);
    };
    const onMove = (e) => {
      const el = doc.elementFromPoint(e.clientX, e.clientY);
      if (el && el !== current && el !== box && el !== tip) {
        trail.length = 0;
        show(el);
      }
    };
    const onClick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      finish(current);
    };
    const onKey = (e) => {
      if (e.key === "Escape") finish(null);
      else if (e.key === "ArrowUp" && current?.parentElement && current.parentElement !== doc.documentElement) {
        trail.push(current);
        show(current.parentElement);
      } else if (e.key === "ArrowDown" && trail.length) show(trail.pop());
      else if (e.key === "Enter" && current) finish(current);
      else return;
      e.preventDefault();
      e.stopPropagation();
    };
    win.addEventListener("mousemove", onMove, true);
    win.addEventListener("click", onClick, true);
    win.addEventListener("keydown", onKey, true);
  });
}
