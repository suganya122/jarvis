import { useEffect, useRef } from "react";
import type { HighlightBox } from "../contracts";

interface Props {
  src: string;
  width: number;
  height: number;
  highlights: HighlightBox[];
}

function HighlightRect({ box, width, height }: { box: HighlightBox; width: number; height: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Positions are set through the CSSOM (allowed by our CSP), as percentages of the image.
    const style = ref.current?.style;
    if (!style) return;
    style.left = `${(box.x / width) * 100}%`;
    style.top = `${(box.y / height) * 100}%`;
    style.width = `${(box.width / width) * 100}%`;
    style.height = `${(box.height / height) * 100}%`;
  }, [box, width, height]);
  return (
    <div ref={ref} className="shot-highlight" role="img" aria-label={`Highlighted: ${box.label || "this area"}`}>
      {box.label && <span className="shot-label">{box.label}</span>}
    </div>
  );
}

/** A local-only screenshot preview. Nothing here is uploaded or saved. */
export function Screenshot({ src, width, height, highlights }: Props) {
  return (
    <figure className="shot">
      <div className="shot-frame">
        <img src={src} alt="Screenshot the assistant took (kept only in this page)" />
        {highlights.map((box, i) => (
          <HighlightRect key={i} box={box} width={width} height={height} />
        ))}
      </div>
      <figcaption className="hint">Preview stays on this computer and isn't saved.</figcaption>
    </figure>
  );
}
