"""Durable, idempotent diagnostics for records that cannot identify valid work."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from mic3_api.application.runs.consumption import RecordLocation, Rejection
from mic3_api.infrastructure.persistence.base import Base


class RejectedMessage(Base):
    __tablename__ = "rejected_messages"

    topic: Mapped[str] = mapped_column(Text, primary_key=True)
    partition: Mapped[int] = mapped_column(Integer, primary_key=True)
    offset: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reason_code: Mapped[str] = mapped_column(String(64))
    diagnostic: Mapped[str] = mapped_column(String(256))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SqlAlchemyRejections:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, location: RecordLocation, rejection: Rejection) -> None:
        with self._session.begin():
            self._session.execute(insert(RejectedMessage).values(
                topic=location.topic, partition=location.partition, offset=location.offset,
                reason_code=rejection.code, diagnostic=rejection.diagnostic,
            ).on_conflict_do_nothing(index_elements=["topic", "partition", "offset"]))
