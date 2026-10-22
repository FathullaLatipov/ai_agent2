"""Seed demo data (idempotent).

    cd backend && uv run python -m scripts.seed

Creates:
- Demo organization + owner account (credentials printed below)
- System agent definitions
- Demo knowledge documents and the demo sales dataset (ingested inline)

All seeded content is synthetic and labeled as demo data.
"""

import asyncio
import hashlib
import shutil
import sys
import uuid
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.security import hash_password
from app.db.session import dispose_engine, get_sessionmaker
from app.models import (
    Document,
    DocumentStatus,
    MemberRole,
    Organization,
    OrganizationMember,
    User,
)
from app.rag.ingestion import ingest_document
from app.services.agents_sync import sync_system_agents

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "agentos-demo-123"  # noqa: S105 вЂ” demo credentials, printed to operator
DEMO_ORG_SLUG = "demo-workspace"

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "scripts" / "data"

SEED_FILES = [
    ("sales_demo.csv", "text/csv"),
    ("company_travel_policy.md", "text/markdown"),
    ("product_performance_playbook.md", "text/markdown"),
    ("refund_policy.md", "text/markdown"),
]


async def seed() -> None:
    settings = get_settings()
    configure_logging(settings.env)
    sessionmaker = get_sessionmaker()

    async with sessionmaker() as session:
        org = await session.scalar(
            select(Organization).where(Organization.slug == DEMO_ORG_SLUG)
        )
        if org is None:
            org = Organization(name="Demo Workspace", slug=DEMO_ORG_SLUG)
            session.add(org)
            await session.flush()
            print(f"created organization: {org.name}")

        user = await session.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            user = User(
                email=DEMO_EMAIL,
                password_hash=hash_password(DEMO_PASSWORD),
                full_name="Demo Owner",
            )
            session.add(user)
            await session.flush()
            session.add(
                OrganizationMember(
                    organization_id=org.id, user_id=user.id, role=MemberRole.OWNER
                )
            )
            print(f"created user: {DEMO_EMAIL}")

        synced = await sync_system_agents(session)
        if synced:
            print(f"synced {synced} system agent definitions")

        upload_dir = Path(settings.upload_dir) / str(org.id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        to_ingest: list[str] = []
        for filename, content_type in SEED_FILES:
            source = DATA_DIR / filename
            if not source.is_file():
                print(f"WARNING: seed file missing: {source}")
                continue
            content = source.read_bytes()
            sha256 = hashlib.sha256(content).hexdigest()
            existing = await session.scalar(
                select(Document).where(
                    Document.organization_id == org.id, Document.sha256 == sha256
                )
            )
            if existing is not None:
                continue
            storage_path = upload_dir / f"{uuid.uuid4().hex}{source.suffix}"
            shutil.copyfile(source, storage_path)
            doc = Document(
                organization_id=org.id,
                uploaded_by=user.id,
                filename=filename,
                content_type=content_type,
                size_bytes=len(content),
                sha256=sha256,
                storage_path=str(storage_path),
                status=DocumentStatus.PENDING,
            )
            session.add(doc)
            await session.flush()
            to_ingest.append(str(doc.id))
            print(f"seeded document: {filename}")

        await session.commit()

    for document_id in to_ingest:
        outcome = await ingest_document(document_id)
        print(f"ingested {document_id}: {outcome}")

    print("\nDemo data ready (all content is synthetic).")
    print(f"  Sign in at the frontend with: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print("  Try:")
    print('   - "Analyze the last 30 days of sales and identify underperforming products"')
    print('   - "What does our travel policy say about business class flights?"')
    print('   - "Prepare a customer follow-up for CUST-1001 and send it"')

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(seed())
