"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Document-style report renderer (manual typography, light theme). */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="text-[14.5px] leading-relaxed text-foreground/90">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: (p) => (
            <h1 className="mb-4 mt-1 text-xl font-semibold tracking-tight text-foreground" {...p} />
          ),
          h2: (p) => (
            <h2
              className="mb-2.5 mt-6 border-b border-border pb-1.5 text-base font-semibold text-foreground"
              {...p}
            />
          ),
          h3: (p) => <h3 className="mb-1.5 mt-4 text-sm font-semibold text-foreground" {...p} />,
          p: (p) => <p className="mb-3" {...p} />,
          ul: (p) => <ul className="mb-3 list-disc space-y-1 pl-5" {...p} />,
          ol: (p) => <ol className="mb-3 list-decimal space-y-1 pl-5" {...p} />,
          li: (p) => <li {...p} />,
          code: (p) => (
            <code
              className="rounded bg-secondary px-1 py-0.5 font-mono text-[12.5px] text-foreground"
              {...p}
            />
          ),
          strong: (p) => <strong className="font-semibold text-foreground" {...p} />,
          blockquote: (p) => (
            <blockquote className="mb-3 border-l-2 border-border pl-3 text-muted-foreground" {...p} />
          ),
          table: (p) => (
            <div className="mb-4 overflow-x-auto rounded-md border border-border">
              <table className="w-full border-collapse text-[13px]" {...p} />
            </div>
          ),
          th: (p) => (
            <th
              className="border-b border-border bg-secondary/60 px-3 py-2 text-left font-medium"
              {...p}
            />
          ),
          td: (p) => <td className="border-b border-border px-3 py-1.5 last:border-b-0" {...p} />,
          a: (p) => (
            <a
              className="font-medium text-[#635BFF] underline underline-offset-2 hover:text-[#4F48D6]"
              {...p}
            />
          ),
          hr: () => <hr className="my-4 border-border" />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
