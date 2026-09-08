import { useEffect, useState } from "react";
import { ListTree } from "lucide-react";

/** A collapsed reading-position rail; only already listed summaries are indexed. */
export function EventRail({
  items,
  onSelect,
  label = "事件索引",
  activeKey,
}: {
  label?: string;
  activeKey?: string;
  items: { key: string; target: string; id: string; title: string }[];
  onSelect: (key: string) => void;
}) {
  const [active, setActive] = useState("");
  const identity = items.map((x) => x.target).join("|");
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: "-10% 0px -50% 0px" },
    );
    identity.split("|").forEach((target) => {
      const el = document.getElementById(target);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [identity]);
  if (!items.length) return null;
  return (
    <nav
      className="event-rail"
      aria-label={label}
      onKeyDown={(e) => {
        if (e.key === "Escape") (e.target as HTMLElement).blur();
      }}
    >
      <div className="event-rail-label">
        <ListTree aria-hidden="true" />
        <span>{label}</span>
      </div>
      <div className="event-rail-items">
        {items.map((item) => (
          <button
            key={item.key}
            aria-label={`${item.id} · ${item.title}`}
            title={`${item.id} · ${item.title}`}
            aria-current={
              (
                activeKey !== undefined
                  ? activeKey === item.key
                  : active === item.target
              )
                ? "location"
                : undefined
            }
            onClick={() => {
              onSelect(item.key);
              requestAnimationFrame(() =>
                document
                  .getElementById(item.target)
                  ?.scrollIntoView({ behavior: "smooth", block: "start" }),
              );
            }}
          >
            <i aria-hidden="true" />
            <span>
              <b>{item.id}</b>
              {item.title}
            </span>
          </button>
        ))}
      </div>
    </nav>
  );
}
