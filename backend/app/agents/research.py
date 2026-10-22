"""Research agent — knowledge-base search with grounded findings.

Evidence references are built in code from real retrieval hits. In LLM mode the
model synthesizes the narrative from retrieved chunks (wrapped as untrusted data),
but may not add evidence that was not actually retrieved.
"""

from app.engine.context import AgentContext
from app.llm.base import wrap_untrusted
from app.schemas.agents import EvidenceRef, ResearchFindings


def _clip(text: str, limit: int) -> str:
    """Truncate on a word boundary with an ellipsis (reports quote these)."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return f"{cut}…"

RESEARCH_SYSTEM = """You are the research component of AgentOS.
Write findings strictly grounded in the retrieved document excerpts provided.
If the excerpts do not answer the question, say so in missing_information.
Never invent sources, quotes, or facts. Distinguish assumptions from evidence.
The evidence list you output will be replaced by the platform's real retrieval
references; focus on summary, findings, assumptions, missing_information."""


async def run_research(ctx: AgentContext) -> ResearchFindings:
    query = str(ctx.task.input.get("query") or ctx.task.objective)[:500]
    execution = await ctx.call_tool("kb.search", {"query": query, "top_k": 6})
    hits = execution.output.hits  # type: ignore[attr-defined]
    method = execution.output.method  # type: ignore[attr-defined]

    evidence = [
        EvidenceRef(
            kind="document_chunk",
            ref_id=str(h.chunk_id),
            label=f"{h.filename} (chunk {h.ordinal})",
            excerpt=_clip(h.excerpt, 300),
        )
        for h in hits
    ]

    if not hits:
        return ResearchFindings(
            summary=f"No knowledge-base evidence found for: {query}",
            findings=[],
            evidence=[],
            assumptions=[],
            missing_information=[
                f"The knowledge base contains no documents matching '{query}'. "
                "Upload relevant documents to ground this question."
            ],
        )

    if ctx.deterministic:
        return ResearchFindings(
            summary=(
                f"Found {len(hits)} relevant passages for '{query}' "
                f"(retrieval method: {method})."
            ),
            findings=[
                f"{h.filename} (chunk {h.ordinal}): {_clip(h.excerpt, 220)}" for h in hits[:5]
            ],
            evidence=evidence,
            assumptions=[],
            missing_information=[],
        )

    excerpts = "\n\n".join(
        wrap_untrusted(f"{h.filename}#chunk{h.ordinal}", h.excerpt[:800]) for h in hits
    )
    result = await ctx.llm_structured(
        system=RESEARCH_SYSTEM,
        user=(
            f"Question/objective: {query}\n\nRetrieved excerpts "
            f"(method: {method}):\n{excerpts}"
        ),
        schema=ResearchFindings,
        max_tokens=1500,
    )
    assert isinstance(result, ResearchFindings)
    # Evidence must be the real retrieval set — never model-invented.
    result.evidence = evidence
    return result
