# agent/autosentry_agent/db/models.py
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    institution_name: Mapped[str] = mapped_column(String, nullable=False)
    workspace_graph_name: Mapped[str] = mapped_column(String, nullable=False)
    enablement_state: Mapped[str] = mapped_column(String, nullable=False, default="disabled")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )


class SchemaMappingVersion(Base):
    __tablename__ = "schema_mapping_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(
        String, ForeignKey("tenants.tenant_id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mapping_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )


class PolicyConfig(Base):
    __tablename__ = "policy_config"

    tenant_id: Mapped[str] = mapped_column(
        String, ForeignKey("tenants.tenant_id"), primary_key=True, index=True
    )
    approval_thresholds: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    high_impact_conditions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    cross_tenant_sharing_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )


class InvestigationHistory(Base):
    __tablename__ = "investigation_history"

    investigation_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String, ForeignKey("tenants.tenant_id"), nullable=False, index=True
    )
    conversation_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    known_entities_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    tool_call_trace_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    closed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )