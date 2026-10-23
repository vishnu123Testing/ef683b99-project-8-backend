"""Notification connector for contract renewal alert dispatching and delivery [FR-9].

Adapts in-app alert persistence and external delivery channels behind the NotificationPort interface.
Supports synchronous SQLAlchemy Session persistence and fallback in-memory operation.
"""

from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional, Union
import uuid

from sqlalchemy.orm import Session

from src.lib.config import settings
from src.models.contract import Contract, RenewalAlert
from src.services.contract.models import ContractStatus, RenewalAlertDomain, RenewalThreshold
from src.services.contract.notification_port import (
    AlertSuppressedError,
    InvalidAlertThresholdError,
    NotificationDispatchError,
    NotificationPort,
    RenewalAlertDispatchResult,
    RenewalAlertPayload,
)

logger = logging.getLogger(__name__)


class NotificationConnector(NotificationPort):
    """Connector adapting in-app alert storage and external delivery for renewal alerts [FR-9].

    Implements NotificationPort using SQLAlchemy database sessions when provided,
    with built-in support for alert suppression (terminated/renewed/expired contracts),
    duplicate prevention per threshold, and in-memory tracking fallback.
    """

    VALID_THRESHOLDS = {90, 60, 30}
    SUPPRESSED_STATUSES = {
        ContractStatus.TERMINATED,
        ContractStatus.EXPIRED,
        "TERMINATED",
        "EXPIRED",
        "RENEWED",
    }

    def __init__(
        self,
        db: Optional[Session] = None,
        default_channel: str = "IN_APP",
        enable_external_delivery: bool = True,
    ) -> None:
        """Initialize NotificationConnector with optional database session and settings.

        Args:
            db: Optional SQLAlchemy Session instance for database persistence.
            default_channel: Default alert delivery channel ("IN_APP", "EMAIL", "WEBHOOK").
            enable_external_delivery: Flag indicating if external delivery simulation is enabled.
        """
        self.db = db
        self.default_channel = default_channel
        self.enable_external_delivery = enable_external_delivery
        self._alerts: Dict[uuid.UUID, RenewalAlertDomain] = {}
        self._cancelled_contracts: Dict[uuid.UUID, str] = {}

    def _normalize_threshold(self, threshold_days: Union[RenewalThreshold, int]) -> int:
        """Normalize threshold_days parameter to integer representation."""
        if isinstance(threshold_days, RenewalThreshold):
            return threshold_days.value
        try:
            return int(threshold_days)
        except (ValueError, TypeError) as exc:
            raise InvalidAlertThresholdError(f"Invalid alert threshold value: {threshold_days}") from exc

    def _normalize_status(self, contract_status: Union[ContractStatus, str]) -> str:
        """Normalize contract status to string value."""
        if isinstance(contract_status, ContractStatus):
            return contract_status.value
        return str(contract_status).upper()

    def should_suppress_alert(
        self,
        contract_status: Union[ContractStatus, str],
        contract_id: uuid.UUID,
        threshold_days: int,
    ) -> bool:
        """Determine whether renewal alert dispatch should be suppressed for a contract [FR-9].

        Alerts are suppressed if:
        - Contract status is TERMINATED, EXPIRED, RENEWED, or non-active [FR-9].
        - Contract has been explicitly cancelled via cancel_pending_alerts.
        - An alert for this specific contract_id and threshold_days has already been dispatched.
        """
        threshold_val = self._normalize_threshold(threshold_days)
        status_str = self._normalize_status(contract_status)

        # Check status suppression (Terminated, Expired, Renewed, or anything non-active)
        if status_str in {"TERMINATED", "EXPIRED", "RENEWED"} or status_str not in {"ACTIVE", "AMENDED"}:
            return True

        # Check explicit cancellation
        if contract_id in self._cancelled_contracts:
            return True

        # Check if already dispatched for this contract_id and threshold
        if self.get_dispatched_alert(contract_id, threshold_val) is not None:
            return True

        return False

    def dispatch_renewal_alert(
        self,
        payload: RenewalAlertPayload,
    ) -> RenewalAlertDispatchResult:
        """Dispatch a contract renewal expiration alert for 90, 60, or 30 day thresholds [FR-9].

        Args:
            payload: Encapsulated renewal alert request payload.

        Returns:
            RenewalAlertDispatchResult with dispatch metadata and status.

        Raises:
            InvalidAlertThresholdError: If threshold_days is not in valid thresholds (90, 60, 30).
            AlertSuppressedError: If contract status or duplicate alert causes suppression.
            NotificationDispatchError: If dispatch fails due to internal errors.
        """
        threshold_val = self._normalize_threshold(payload.threshold_days)
        valid_thresholds = set(settings.renewal_alert_thresholds_days) or self.VALID_THRESHOLDS

        if threshold_val not in valid_thresholds:
            raise InvalidAlertThresholdError(
                f"Invalid threshold days {threshold_val}. Supported thresholds: {sorted(list(valid_thresholds))}"
            )

        if self.should_suppress_alert(payload.contract_status, payload.contract_id, threshold_val):
            status_str = self._normalize_status(payload.contract_status)
            if status_str in {"TERMINATED", "EXPIRED", "RENEWED"} or status_str not in {"ACTIVE", "AMENDED"}:
                reason = f"Alert suppressed for contract {payload.contract_id} with status {status_str} [FR-9]"
            elif payload.contract_id in self._cancelled_contracts:
                reason = f"Alert suppressed for contract {payload.contract_id}: {self._cancelled_contracts[payload.contract_id]}"
            else:
                reason = f"Renewal alert for threshold {threshold_val} days already dispatched for contract {payload.contract_id}"
            raise AlertSuppressedError(reason)

        alert_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        try:
            # Save to DB if session provided
            if self.db is not None:
                alert_orm = RenewalAlert(
                    id=alert_id,
                    contract_id=payload.contract_id,
                    threshold_days=threshold_val,
                    dispatched_at=now,
                )
                self.db.add(alert_orm)
                self.db.flush()

            # Save in memory
            domain_alert = RenewalAlertDomain(
                id=alert_id,
                contract_id=payload.contract_id,
                threshold_days=threshold_val,
                dispatched_at=now,
            )
            self._alerts[alert_id] = domain_alert

            # Formulate delivery result
            delivery_details = {
                "contract_title": payload.contract_title,
                "counterparty": payload.counterparty,
                "expires_on": payload.expires_on.isoformat() if hasattr(payload.expires_on, "isoformat") else str(payload.expires_on),
                "delivery_status": "DELIVERED" if self.enable_external_delivery else "IN_APP_ONLY",
                **payload.metadata,
            }

            logger.info(
                f"Dispatched renewal alert {alert_id} for contract {payload.contract_id} "
                f"at threshold {threshold_val} days."
            )

            return RenewalAlertDispatchResult(
                alert_id=alert_id,
                contract_id=payload.contract_id,
                threshold_days=threshold_val,
                dispatched_at=now,
                channel=self.default_channel,
                is_suppressed=False,
                suppression_reason=None,
                recipients=payload.recipients,
                details=delivery_details,
            )
        except Exception as exc:
            if isinstance(exc, (InvalidAlertThresholdError, AlertSuppressedError)):
                raise
            logger.error(f"Failed to dispatch renewal alert for contract {payload.contract_id}: {exc}")
            raise NotificationDispatchError(f"Notification dispatch failed: {exc}") from exc

    def get_dispatched_alert(
        self,
        contract_id: uuid.UUID,
        threshold_days: int,
    ) -> Optional[RenewalAlertDomain]:
        """Retrieve a previously dispatched renewal alert domain record if it exists."""
        threshold_val = self._normalize_threshold(threshold_days)

        # Check DB if available
        if self.db is not None:
            orm_alert = (
                self.db.query(RenewalAlert)
                .filter(
                    RenewalAlert.contract_id == contract_id,
                    RenewalAlert.threshold_days == threshold_val,
                )
                .first()
            )
            if orm_alert is not None:
                return RenewalAlertDomain(
                    id=orm_alert.id,
                    contract_id=orm_alert.contract_id,
                    threshold_days=orm_alert.threshold_days,
                    dispatched_at=orm_alert.dispatched_at,
                )

        # Check in-memory store
        for alert in self._alerts.values():
            if alert.contract_id == contract_id and alert.threshold_days == threshold_val:
                return alert

        return None

    def list_dispatched_alerts(
        self,
        contract_id: Optional[uuid.UUID] = None,
        threshold_days: Optional[int] = None,
    ) -> List[RenewalAlertDomain]:
        """List dispatched renewal alert records filtered by contract or threshold interval."""
        threshold_val = self._normalize_threshold(threshold_days) if threshold_days is not None else None
        results: Dict[uuid.UUID, RenewalAlertDomain] = {}

        # Query DB if available
        if self.db is not None:
            query = self.db.query(RenewalAlert)
            if contract_id is not None:
                query = query.filter(RenewalAlert.contract_id == contract_id)
            if threshold_val is not None:
                query = query.filter(RenewalAlert.threshold_days == threshold_val)

            for orm_alert in query.all():
                results[orm_alert.id] = RenewalAlertDomain(
                    id=orm_alert.id,
                    contract_id=orm_alert.contract_id,
                    threshold_days=orm_alert.threshold_days,
                    dispatched_at=orm_alert.dispatched_at,
                )

        # Add from in-memory store
        for alert in self._alerts.values():
            if contract_id is not None and alert.contract_id != contract_id:
                continue
            if threshold_val is not None and alert.threshold_days != threshold_val:
                continue
            if alert.id and alert.id not in results:
                results[alert.id] = alert

        return list(results.values())

    def cancel_pending_alerts(
        self,
        contract_id: uuid.UUID,
        reason: Optional[str] = None,
    ) -> int:
        """Suppress or cancel pending alerts for a contract (e.g. upon termination or renewal).

        Args:
            contract_id: Unique identifier of the contract.
            reason: Optional explanation for alert cancellation.

        Returns:
            Count of pending alerts suppressed or cancelled.
        """
        cancellation_reason = reason or "Contract cancelled or terminated"
        self._cancelled_contracts[contract_id] = cancellation_reason

        # Calculate pending thresholds that haven't been dispatched yet
        valid_thresholds = set(settings.renewal_alert_thresholds_days) or self.VALID_THRESHOLDS
        pending_count = 0
        for threshold in valid_thresholds:
            if self.get_dispatched_alert(contract_id, threshold) is None:
                pending_count += 1

        logger.info(
            f"Cancelled pending renewal alerts for contract {contract_id}. "
            f"Reason: {cancellation_reason}. Pending count: {pending_count}."
        )

        return pending_count
