"""Shared test helpers."""

import csv
import datetime as dt
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import (
    Document,
    DocumentStatus,
    MemberRole,
    Organization,
    OrganizationMember,
    User,
)


async def make_org(
    session: AsyncSession, *, slug: str = "testorg", role: MemberRole = MemberRole.OWNER
) -> tuple[Organization, User]:
    org = Organization(name=slug.title(), slug=f"{slug}-{uuid.uuid4().hex[:6]}")
    user = User(
        email=f"{slug}-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("irrelevant-pass-123"),
        full_name="Test User",
    )
    session.add_all([org, user])
    await session.flush()
    session.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
    await session.flush()
    return org, user


def write_sales_csv(path: Path, *, days: int = 45) -> None:
    """Deterministic demo sales data: products A-C healthy, D underperforming."""
    end = dt.date(2026, 10, 15)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "product", "units", "revenue"])
        for offset in range(days):
            day = end - dt.timedelta(days=offset)
            for product, revenue in (
                ("Alpha Widget", 1000.0),
                ("Beta Widget", 900.0),
                ("Gamma Widget", 1100.0),
                ("Delta Widget", 12.5),
            ):
                writer.writerow([day.isoformat(), product, 10, revenue])


async def seed_csv_document(
    session: AsyncSession, org_id: uuid.UUID, path: Path, filename: str = "sales.csv"
) -> Document:
    doc = Document(
        organization_id=org_id,
        filename=filename,
        content_type="text/csv",
        size_bytes=path.stat().st_size,
        sha256=uuid.uuid4().hex * 2,
        storage_path=str(path),
        status=DocumentStatus.READY,
    )
    session.add(doc)
    await session.flush()
    return doc
