"""SQLAlchemy models persisting the frozen contracts (backend/contracts.py).

Nested structures that the platform never queries relationally (lists,
extracted claims/disclosures) live in JSON columns; findings get their own
table because reviewers update their status individually.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from backend.contracts import CheckRun, Finding, OfferCell, Submission


class Base(DeclarativeBase):
    pass


class MatrixImport(Base):
    """Provenance record: one imported offer-matrix version."""

    __tablename__ = "matrix_imports"

    offer_matrix_version: Mapped[str] = mapped_column(String, primary_key=True)
    source_path: Mapped[str] = mapped_column(String)
    imported_at: Mapped[datetime] = mapped_column(DateTime)


class OfferCellRow(Base):
    __tablename__ = "offer_cells"

    offer_id: Mapped[str] = mapped_column(String, primary_key=True)
    offer_matrix_version: Mapped[str] = mapped_column(
        String, ForeignKey("matrix_imports.offer_matrix_version"), primary_key=True
    )
    product: Mapped[str] = mapped_column(String)
    offer_name: Mapped[str] = mapped_column(String)
    apr_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    apr_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    apr_type: Mapped[str | None] = mapped_column(String, nullable=True)
    term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    origination_fee_pct: Mapped[str | None] = mapped_column(String, nullable=True)
    fee_deducted_from_proceeds: Mapped[bool | None] = mapped_column(nullable=True)
    intro_apr_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    intro_period_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annual_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    badge_designation_allowed: Mapped[str] = mapped_column(String)
    is_firm_offer: Mapped[bool] = mapped_column(default=False)
    min_credit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    states_excluded: Mapped[list] = mapped_column(JSON, default=list)
    effective_start: Mapped[date] = mapped_column(Date)
    effective_end: Mapped[date] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="")

    def to_contract(self) -> OfferCell:
        return OfferCell(
            offer_id=self.offer_id,
            product=self.product,
            offer_name=self.offer_name,
            apr_min=self.apr_min,
            apr_max=self.apr_max,
            apr_type=self.apr_type,
            term_months=self.term_months,
            amount_min=self.amount_min,
            amount_max=self.amount_max,
            origination_fee_pct=self.origination_fee_pct,
            fee_deducted_from_proceeds=self.fee_deducted_from_proceeds,
            intro_apr_pct=self.intro_apr_pct,
            intro_period_months=self.intro_period_months,
            annual_fee=self.annual_fee,
            badge_designation_allowed=self.badge_designation_allowed,
            is_firm_offer=self.is_firm_offer,
            min_credit_score=self.min_credit_score,
            states_excluded=list(self.states_excluded or []),
            effective_start=self.effective_start,
            effective_end=self.effective_end,
            notes=self.notes,
        )

    @classmethod
    def from_contract(cls, cell: OfferCell, offer_matrix_version: str) -> "OfferCellRow":
        return cls(
            offer_matrix_version=offer_matrix_version,
            **{
                **cell.model_dump(),
                "product": cell.product.value,
                "badge_designation_allowed": cell.badge_designation_allowed.value,
            },
        )


class SubmissionRow(Base):
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    submission_id: Mapped[str] = mapped_column(String, unique=True)
    partner: Mapped[str] = mapped_column(String)
    date_submitted: Mapped[date] = mapped_column(Date)
    surface: Mapped[str] = mapped_column(String)
    product: Mapped[str] = mapped_column(String)
    template_id: Mapped[str] = mapped_column(String)
    template_version: Mapped[str] = mapped_column(String)
    offer_ids: Mapped[list] = mapped_column(JSON, default=list)
    proposed_headline: Mapped[str] = mapped_column(Text, default="")
    badge_text: Mapped[str] = mapped_column(String, default="")
    dynamic_slots: Mapped[list] = mapped_column(JSON, default=list)
    disclosures_included: Mapped[list] = mapped_column(JSON, default=list)
    asset_files: Mapped[list] = mapped_column(JSON, default=list)
    states_targeted: Mapped[str] = mapped_column(String, default="")
    requested_launch: Mapped[date | None] = mapped_column(Date, nullable=True)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="pending_review")
    sla_due: Mapped[date | None] = mapped_column(Date, nullable=True)
    mode: Mapped[str] = mapped_column(String, default="pre_publication")
    baseline_submission_id: Mapped[str | None] = mapped_column(String, nullable=True)

    check_runs: Mapped[list["CheckRunRow"]] = relationship(back_populates="submission")

    def to_contract(self) -> Submission:
        return Submission(
            id=self.id,
            submission_id=self.submission_id,
            partner=self.partner,
            date_submitted=self.date_submitted,
            surface=self.surface,
            product=self.product,
            template_id=self.template_id,
            template_version=self.template_version,
            offer_ids=list(self.offer_ids or []),
            proposed_headline=self.proposed_headline,
            badge_text=self.badge_text,
            dynamic_slots=list(self.dynamic_slots or []),
            disclosures_included=list(self.disclosures_included or []),
            asset_files=list(self.asset_files or []),
            states_targeted=self.states_targeted,
            requested_launch=self.requested_launch,
            change_summary=self.change_summary,
            status=self.status,
            sla_due=self.sla_due,
            mode=self.mode,
            baseline_submission_id=self.baseline_submission_id,
        )

    @classmethod
    def from_contract(cls, sub: Submission) -> "SubmissionRow":
        return cls(
            **{
                **sub.model_dump(),
                "product": sub.product.value,
                "mode": sub.mode.value,
            }
        )


class CheckRunRow(Base):
    __tablename__ = "check_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    submission_id: Mapped[str] = mapped_column(String, ForeignKey("submissions.id"))
    rulebook_version: Mapped[str] = mapped_column(String)
    offer_matrix_version: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    # Raw extraction output (Claim[]/Disclosure[] dumps) — stored for audit,
    # not part of the CheckRun contract.
    extracted_claims: Mapped[list] = mapped_column(JSON, default=list)
    extracted_disclosures: Mapped[list] = mapped_column(JSON, default=list)

    submission: Mapped[SubmissionRow] = relationship(back_populates="check_runs")
    findings: Mapped[list["FindingRow"]] = relationship(
        back_populates="check_run", cascade="all, delete-orphan"
    )

    def to_contract(self) -> CheckRun:
        return CheckRun(
            id=self.id,
            submission_id=self.submission_id,
            rulebook_version=self.rulebook_version,
            offer_matrix_version=self.offer_matrix_version,
            mode=self.mode,
            created_at=self.created_at,
            findings=[f.to_contract() for f in self.findings],
        )

    @classmethod
    def from_contract(cls, run: CheckRun) -> "CheckRunRow":
        row = cls(
            id=run.id,
            submission_id=run.submission_id,
            rulebook_version=run.rulebook_version,
            offer_matrix_version=run.offer_matrix_version,
            mode=run.mode.value,
            created_at=run.created_at,
        )
        row.findings = [FindingRow.from_contract(f) for f in run.findings]
        return row


class ReviewDecisionRow(Base):
    """A reviewer's approve/reject call on one submission.

    Additive to the frozen contracts: the queue view treats a submission as
    reviewed (and drops it from the queue) once a decision row exists.
    """

    __tablename__ = "review_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    submission_id: Mapped[str] = mapped_column(String, ForeignKey("submissions.id"))
    decision: Mapped[str] = mapped_column(String)  # "approved" | "rejected"
    decided_by: Mapped[str] = mapped_column(String, default="reviewer")
    decided_at: Mapped[datetime] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    check_run_id: Mapped[str] = mapped_column(String, ForeignKey("check_runs.id"))
    check_class: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)
    rule_id: Mapped[str | None] = mapped_column(String, nullable=True)
    claim_id: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    citation_url: Mapped[str | None] = mapped_column(String, nullable=True)
    suggested_redline: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")

    check_run: Mapped[CheckRunRow] = relationship(back_populates="findings")

    def to_contract(self) -> Finding:
        return Finding(
            id=self.id,
            check_class=self.check_class,
            severity=self.severity,
            rule_id=self.rule_id,
            claim_id=self.claim_id,
            summary=self.summary,
            explanation=self.explanation,
            citation_url=self.citation_url,
            suggested_redline=self.suggested_redline,
            status=self.status,
        )

    @classmethod
    def from_contract(cls, finding: Finding) -> "FindingRow":
        return cls(
            **{
                **finding.model_dump(),
                "check_class": finding.check_class.value,
                "severity": finding.severity.value,
                "status": finding.status.value,
            }
        )
