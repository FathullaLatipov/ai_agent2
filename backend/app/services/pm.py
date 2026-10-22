"""PM service: plan lifecycle, explicit agent requests, conversation commands.

The PM proposes and explains; every state change here calls the deterministic
services (projects/task_state/scheduler), which enforce the rules. Conversation
messages are never treated as proof of execution — replies report only what the
backend verifiably did.
"""

import datetime as dt
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.capabilities import (
    check_assignment,
    get_capability,
    producers_for,
    suggest_agent_for,
)
from app.agents.pm import PlanDraftError
from app.engine.pm_graph import generate_validated_plan
from app.models import (
    ActorType,
    Conversation,
    ConversationMessage,
    PlanStatus,
    PMTaskStatus,
    Project,
    ProjectPlan,
    ProjectStatus,
    ProjectTask,
    TaskDependency,
)
from app.schemas.pm import PlanDraft, PMIntent, TaskSpec
from app.services.audit import record_audit
from app.services.project_events import ProjectEventRecorder
from app.services.projects import (
    ProjectServiceError,
    assign_agent,
    create_task,
    eligibility,
    project_tasks,
    resolve_task_ref,
    retry_task,
    set_project_status,
    supplied_artifact_kinds,
    validate_project_dag,
)
from app.services.task_state import transition

PM_ROLE = "pm"
OWNER_ROLE = "owner"


# ---------------------------------------------------------------- plans


async def propose_plan(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    *,
    project: Project,
    user_id: uuid.UUID,
    goal: str | None = None,
    draft: PlanDraft | None = None,
    notes: str = "",
) -> ProjectPlan:
    """Generate (or accept) a validated draft and persist it as a proposed version."""
    from app.agents.pm import validate_plan_draft
    from app.llm.factory import get_llm_provider

    if draft is None:
        draft = await generate_validated_plan(goal or project.goal, get_llm_provider())
    else:
        validate_plan_draft(draft)  # supplied drafts get the same server-side checks
    version = (
        await session.scalar(
            select(func.coalesce(func.max(ProjectPlan.version), 0)).where(
                ProjectPlan.project_id == project.id
            )
        )
    ) + 1
    plan = ProjectPlan(
        organization_id=project.organization_id,
        project_id=project.id,
        version=version,
        status=PlanStatus.PROPOSED,
        goal=draft.goal,
        scope=draft.scope,
        assumptions=draft.assumptions,
        deliverables=draft.deliverables,
        risks=draft.risks,
        tasks_spec=[t.model_dump(mode="json") for t in draft.tasks],
        notes=notes or draft.notes,
    )
    session.add(plan)
    if project.status in (ProjectStatus.DRAFT.value, ProjectStatus.PLANNING.value):
        project.status = ProjectStatus.AWAITING_APPROVAL.value
    await session.flush()
    await recorder.emit(
        session,
        project_id=project.id,
        organization_id=project.organization_id,
        type="plan.proposed",
        payload={"version": version, "tasks": len(draft.tasks), "goal": draft.goal[:200]},
    )
    return plan


async def decide_plan(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    *,
    project: Project,
    plan: ProjectPlan,
    approve: bool,
    user_id: uuid.UUID,
) -> ProjectPlan:
    if plan.status != PlanStatus.PROPOSED:
        raise ProjectServiceError(f"plan v{plan.version} is already {plan.status.value}")
    plan.decided_by = user_id
    plan.decided_at = dt.datetime.now(dt.UTC)
    if not approve:
        plan.status = PlanStatus.REJECTED
        if project.status == ProjectStatus.AWAITING_APPROVAL.value:
            project.status = (
                ProjectStatus.ACTIVE.value
                if any(t for t in await project_tasks(session, project.id))
                else ProjectStatus.DRAFT.value
            )
        await recorder.emit(
            session,
            project_id=project.id,
            organization_id=project.organization_id,
            type="plan.rejected",
            payload={"version": plan.version},
        )
        return plan

    # Supersede previously approved plans (audit trail retained).
    for old in (
        await session.scalars(
            select(ProjectPlan).where(
                ProjectPlan.project_id == project.id,
                ProjectPlan.status == PlanStatus.APPROVED,
            )
        )
    ).all():
        old.status = PlanStatus.SUPERSEDED
    plan.status = PlanStatus.APPROVED
    created = await _materialize_plan(session, recorder, project, plan)
    await attach_prerequisite_dependency(session, project, plan, created)
    project.status = ProjectStatus.ACTIVE.value
    await recorder.emit(
        session,
        project_id=project.id,
        organization_id=project.organization_id,
        type="plan.approved",
        payload={"version": plan.version, "by": str(user_id)},
    )
    await record_audit(
        session,
        action="plan.approved",
        actor_type=ActorType.USER,
        actor_id=str(user_id),
        organization_id=project.organization_id,
        entity_type="project_plan",
        entity_id=str(plan.id),
    )
    return plan


async def _materialize_plan(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    project: Project,
    plan: ProjectPlan,
) -> list[ProjectTask]:
    """Create project tasks + dependency edges from the approved spec.

    Completed work is never recreated: specs materialize as NEW tasks; refs of
    the form '#N' attach dependencies to existing tasks (whatever their state).
    """
    ref_to_task: dict[str, ProjectTask] = {}
    for raw in plan.tasks_spec:
        spec = TaskSpec.model_validate(raw)
        task = await create_task(
            session,
            recorder,
            project=project,
            user_id=None,
            title=spec.title,
            description=spec.description,
            agent_key=spec.agent,
            params=spec.params,
            acceptance_criteria=[c.model_dump() for c in spec.acceptance_criteria],
            expected_deliverables=spec.expected_deliverables,
            priority=spec.priority,
            status=PMTaskStatus.PLANNED,
            actor=PM_ROLE,
        )
        ref_to_task[spec.ref] = task
    for raw in plan.tasks_spec:
        spec = TaskSpec.model_validate(raw)
        task = ref_to_task[spec.ref]
        for dep_ref in spec.depends_on:
            if dep_ref.startswith("#"):
                dep_task = await resolve_task_ref(session, project, dep_ref)
            else:
                dep_task = ref_to_task.get(dep_ref)
                if dep_task is None:
                    raise ProjectServiceError(f"plan references unknown task ref {dep_ref}")
            session.add(TaskDependency(task_id=task.id, depends_on_task_id=dep_task.id))
    await session.flush()
    await validate_project_dag(session, project.id)
    return list(ref_to_task.values())


# ------------------------------------------- explicit agent requests


async def request_agent(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    *,
    project: Project,
    task: ProjectTask,
    agent_key: str,
    mode: str,  # assign | start_now | manage
    user_id: uuid.UUID,
) -> dict:
    """Owner explicitly requests an agent. Three distinct modes (never conflated):
    assign = record intent; start_now = execute only if eligible, else explain and
    propose prerequisites; manage = PM plans end-to-end."""
    capability = get_capability(agent_key)
    if capability is None:
        return {"status": "rejected", "message": f"No registered agent '{agent_key}'."}
    if not capability.assignable:
        return {"status": "rejected", "message": capability.unassignable_reason}

    if mode == "manage":
        plan = await propose_plan(
            session,
            recorder,
            project=project,
            user_id=user_id,
            goal=f"{project.goal}\nFocus: {task.title} — {task.description}",
            notes=f"Proposed because the owner asked the PM to manage task #{task.ordinal}.",
        )
        return {
            "status": "plan_proposed",
            "plan_version": plan.version,
            "message": (
                f"I drafted plan v{plan.version} to manage '{task.title}' end to end. "
                "Review and approve it to start execution."
            ),
        }

    await assign_agent(
        session,
        recorder,
        project=project,
        task=task,
        agent_key=agent_key,
        requested=True,
        actor_id=str(user_id),
    )

    eligible, reason = await eligibility(session, project, task)
    if mode == "assign":
        note = "It is eligible to start." if eligible else f"It cannot start yet: {reason}."
        return {
            "status": "assigned",
            "message": f"{capability.name} is assigned to task #{task.ordinal}. {note}",
        }

    # start_now
    if eligible:
        if project.status != ProjectStatus.ACTIVE.value:
            return {
                "status": "blocked",
                "message": (
                    f"Task #{task.ordinal} is eligible, but the project is "
                    f"{project.status}. Resume/activate the project to execute."
                ),
            }
        if task.status in (PMTaskStatus.PLANNED, PMTaskStatus.BLOCKED):
            transition(task, PMTaskStatus.READY)
        await session.commit()
        await recorder.publish_pending()
        from app.services.project_scheduler import claim_task

        worker_id = f"owner-start-{uuid.uuid4().hex[:8]}"
        if await claim_task(session, task.id, worker_id):
            from app.worker.tasks import execute_project_task_attempt

            await execute_project_task_attempt.kiq(str(task.id), worker_id)
            return {
                "status": "started",
                "message": f"{capability.name} started on task #{task.ordinal}.",
            }
        return {"status": "blocked", "message": "Task was claimed by another worker."}

    # Not eligible: explain and, when inputs are missing, propose prerequisites.
    kinds = await supplied_artifact_kinds(session, project.organization_id, task)
    assignment = check_assignment(agent_key, kinds)
    if assignment.missing:
        producer_key = suggest_agent_for(task.description or task.title)
        if producer_key == agent_key or producer_key == "reviewer":
            producer_key = "research"  # an agent is never its own prerequisite
        producer = get_capability(producer_key)
        if producer is None or not producer.assignable or not any(
            producers_for(kind) for kind in assignment.missing
        ):
            return {
                "status": "blocked",
                "message": (
                    f"I cannot start {capability.name} on #{task.ordinal}: {assignment.reason}. "
                    "No available agent can produce the missing inputs — please supply "
                    "the artifacts or register a suitable agent."
                ),
            }
        draft = PlanDraft(
            goal=f"Prerequisites for '{task.title}' so {capability.name} can run.",
            scope="Produce the missing deliverable, then unblock the requested task.",
            assumptions=[],
            deliverables=[f"{producer.name} deliverable for '{task.title}'"],
            risks=[],
            tasks=[
                TaskSpec(
                    ref="p1",
                    title=f"Produce input for: {task.title}"[:300],
                    description=(task.description or task.title)[:2000],
                    agent=producer_key,
                    params=dict(task.params or {}),
                    depends_on=[],
                )
            ],
            notes=f"Prerequisite proposal for task #{task.ordinal}.",
        )
        # The requested task will depend on the new prerequisite: encode as an
        # extra spec entry linking via existing-ordinal ref.
        plan = await propose_plan(
            session,
            recorder,
            project=project,
            user_id=user_id,
            draft=draft,
            notes=f"prerequisite_for=#{task.ordinal}",
        )
        message = (
            f"I cannot start {capability.name} yet: {assignment.reason}. "
            f"I propose running {producer.name} first to produce the required "
            f"deliverable; '{task.title}' will begin only after it succeeds. "
            f"Approve plan v{plan.version} to proceed."
        )
        return {"status": "blocked", "plan_version": plan.version, "message": message}

    return {
        "status": "blocked",
        "message": f"I cannot start {capability.name} on #{task.ordinal} yet: {reason}.",
    }


async def attach_prerequisite_dependency(
    session: AsyncSession, project: Project, plan: ProjectPlan, created: list[ProjectTask]
) -> None:
    """After approving a prerequisite proposal, link the requested task to it."""
    match = re.search(r"prerequisite_for=#(\d+)", plan.notes or "")
    if not match or not created:
        return
    target = await resolve_task_ref(session, project, f"#{match.group(1)}")
    for new_task in created:
        session.add(TaskDependency(task_id=target.id, depends_on_task_id=new_task.id))
    await session.flush()
    await validate_project_dag(session, project.id)


# ---------------------------------------------------------------- intents

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("create_plan", re.compile(r"\b(create|make|draft|generate|build)\b.{0,30}\bplan\b", re.I)),
    ("run_ready", re.compile(r"\brun\b.{0,20}\b(all|ready|everything|eligible)\b", re.I)),
    ("explain_blocked", re.compile(r"\bwhy\b.{0,40}\bblocked\b", re.I)),
    ("retry_task", re.compile(r"\bretry\b", re.I)),
    ("reassign_task", re.compile(r"\breassign\b", re.I)),
    ("pause_project", re.compile(r"\bpause\b", re.I)),
    ("resume_project", re.compile(r"\b(resume|continue|unpause)\b", re.I)),
    ("cancel_project", re.compile(r"\bcancel\b.{0,20}\bproject\b", re.I)),
    (
        "status_report",
        re.compile(r"\b(status|progress|completed|what has|show me what)\b", re.I),
    ),
    ("start_task", re.compile(r"\bstart\b", re.I)),
    ("assign_task", re.compile(r"\b(assign|use)\b", re.I)),
]

_AGENT_WORDS = re.compile(
    r"\b(planner|research(?:er)?|analyst|executor|reviewer|reporter)\b", re.I
)
_TASK_REF = re.compile(r"#(\d+)|[\"“'](.+?)[\"”']|task\s+(\d+)", re.I)

_AGENT_ALIASES = {"researcher": "research"}


def parse_intent(text: str) -> PMIntent:
    kind = "unknown"
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            kind = name
            break
    agent = None
    agent_match = _AGENT_WORDS.search(text)
    if agent_match:
        raw_agent = agent_match.group(1).lower()
        agent = _AGENT_ALIASES.get(raw_agent, raw_agent)
    task_ref = None
    ref_match = _TASK_REF.search(text)
    if ref_match:
        task_ref = next(group for group in ref_match.groups() if group)
        if ref_match.group(1) or ref_match.group(3):
            task_ref = f"#{task_ref}"
    mode = None
    if kind == "start_task":
        mode = "start_now"
    elif kind in ("assign_task", "reassign_task"):
        mode = "assign"
        if re.search(r"\b(now|immediately|right away)\b", text, re.I):
            kind, mode = "start_task", "start_now"
    if re.search(r"\bmanage\b|\bend to end\b", text, re.I):
        kind, mode = "assign_task", "manage"
    return PMIntent(kind=kind, agent_key=agent, task_ref=task_ref, mode=mode, raw=text)


# ----------------------------------------------------------- conversation


async def _conversation(
    session: AsyncSession, project: Project, user_id: uuid.UUID
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(Conversation.project_id == project.id)
    )
    if conversation is None:
        conversation = Conversation(
            organization_id=project.organization_id,
            project_id=project.id,
            user_id=user_id,
            title=f"PM — {project.name}"[:300],
        )
        session.add(conversation)
        await session.flush()
    return conversation


async def _say(
    session: AsyncSession,
    conversation: Conversation,
    role: str,
    content: str,
    meta: dict | None = None,
) -> ConversationMessage:
    message = ConversationMessage(
        conversation_id=conversation.id, role=role, content=content, meta=meta or {}
    )
    session.add(message)
    await session.flush()
    return message


async def status_report(session: AsyncSession, project: Project) -> str:
    tasks = await project_tasks(session, project.id)
    if not tasks:
        return (
            f"Project '{project.name}' is {project.status} with no tasks yet. "
            "Say 'create a plan' and I will propose one."
        )
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.status.value] = counts.get(task.status.value, 0) + 1
    lines = [
        f"Project '{project.name}' is {project.status}: "
        + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted(counts.items()))
        + "."
    ]
    done = [t for t in tasks if t.status == PMTaskStatus.SUCCEEDED]
    if done:
        lines.append("Completed: " + "; ".join(f"#{t.ordinal} {t.title}" for t in done[:6]))
    for task in tasks:
        if task.status == PMTaskStatus.BLOCKED and task.blocked_reason:
            lines.append(f"#{task.ordinal} '{task.title}' is blocked: {task.blocked_reason}")
        if task.status == PMTaskStatus.FAILED:
            lines.append(f"#{task.ordinal} '{task.title}' FAILED: {task.error}")
        if task.status == PMTaskStatus.WAITING_APPROVAL:
            lines.append(f"#{task.ordinal} '{task.title}' awaits your approval (Approvals page).")
    return "\n".join(lines)


async def handle_pm_message(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    *,
    project: Project,
    user_id: uuid.UUID,
    content: str,
) -> ConversationMessage:
    """Parse an owner message into an intent, execute via validated services,
    and reply with what actually happened (verified backend results only)."""
    conversation = await _conversation(session, project, user_id)
    intent = parse_intent(content)
    await _say(
        session, conversation, OWNER_ROLE, content, {"intent": intent.model_dump()}
    )

    actions: list[dict] = []
    try:
        reply = await _execute_intent(
            session, recorder, project, user_id, intent, actions
        )
    except (ProjectServiceError, PlanDraftError) as exc:
        reply = f"I can't do that: {exc}"
    await recorder.emit(
        session,
        project_id=project.id,
        organization_id=project.organization_id,
        type="pm.message",
        payload={"intent": intent.kind, "summary": reply[:200]},
    )
    return await _say(
        session,
        conversation,
        PM_ROLE,
        reply,
        {"intent": intent.model_dump(), "actions": actions},
    )


async def _execute_intent(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    project: Project,
    user_id: uuid.UUID,
    intent: PMIntent,
    actions: list[dict],
) -> str:
    kind = intent.kind

    if kind == "create_plan":
        plan = await propose_plan(session, recorder, project=project, user_id=user_id)
        actions.append({"op": "plan.proposed", "version": plan.version})
        tasks_preview = "; ".join(
            f"{s['ref']}: {s['agent']} — {s['title']}" for s in plan.tasks_spec
        )
        return (
            f"I drafted plan v{plan.version} with {len(plan.tasks_spec)} task(s): "
            f"{tasks_preview}. It needs your approval before anything executes — "
            "use the plan panel to approve or reject it."
        )

    if kind == "status_report":
        return await status_report(session, project)

    if kind == "run_ready":
        if project.status == ProjectStatus.AWAITING_APPROVAL.value:
            return (
                "There is a proposed plan awaiting your approval — nothing runs "
                "until you approve it."
            )
        if project.status != ProjectStatus.ACTIVE.value:
            return f"The project is {project.status}; resume it before running tasks."
        await session.commit()
        await recorder.publish_pending()
        from app.services.project_scheduler import schedule_project

        dispatched = await schedule_project(str(project.id))
        actions.append({"op": "schedule", "dispatched": len(dispatched)})
        if dispatched:
            return f"Dispatched {len(dispatched)} eligible task(s). I'll report as they finish."
        return (
            "No task is currently eligible. "
            + await status_report(session, project)
        )

    if kind in ("pause_project", "resume_project", "cancel_project"):
        target = {
            "pause_project": ProjectStatus.PAUSED,
            "resume_project": ProjectStatus.ACTIVE,
            "cancel_project": ProjectStatus.CANCELLED,
        }[kind]
        await set_project_status(
            session, recorder, project=project, status=target, actor_id=str(user_id)
        )
        actions.append({"op": kind})
        notes = {
            "pause_project": "New work will not be scheduled; running attempts finish.",
            "resume_project": "Scheduling resumed.",
            "cancel_project": "All unfinished tasks were cancelled.",
        }
        return f"Done — the project is now {target.value}. {notes[kind]}"

    # Task-scoped intents below need a task reference.
    if kind in ("assign_task", "start_task", "reassign_task", "retry_task", "explain_blocked"):
        if not intent.task_ref:
            return (
                "Which task? Reference it like #2 or by its title in quotes "
                '(e.g. assign "Review findings" to reviewer).'
            )
        task = await resolve_task_ref(session, project, intent.task_ref)

        if kind == "explain_blocked":
            eligible, reason = await eligibility(session, project, task)
            if eligible:
                return (
                    f"#{task.ordinal} '{task.title}' is not blocked — it is "
                    f"{task.status.value} and eligible to run."
                )
            return f"#{task.ordinal} '{task.title}' cannot run yet: {reason}."

        if kind == "retry_task":
            await retry_task(
                session, recorder, project=project, task=task, actor_id=str(user_id)
            )
            await session.commit()
            await recorder.publish_pending()
            from app.services.project_scheduler import schedule_project

            dispatched = await schedule_project(str(project.id))
            actions.append({"op": "retry", "task": task.ordinal, "dispatched": len(dispatched)})
            return (
                f"#{task.ordinal} is back in the queue"
                + (" and has been dispatched." if dispatched else ".")
            )

        if not intent.agent_key:
            return "Which agent should take it? (research, analyst, executor, reviewer)"
        result = await request_agent(
            session,
            recorder,
            project=project,
            task=task,
            agent_key=intent.agent_key,
            mode=intent.mode or "assign",
            user_id=user_id,
        )
        meta = {k: v for k, v in result.items() if k != "message"}
        actions.append({"op": "request_agent", **meta})
        return result["message"]

    return (
        "I didn't recognize that command. I can: create a plan, assign/start a task "
        "(assign #2 to reviewer / start reviewer on #2 now), run ready tasks, explain "
        "why a task is blocked, retry a failed task, pause/resume/cancel the project, "
        "or report status."
    )
