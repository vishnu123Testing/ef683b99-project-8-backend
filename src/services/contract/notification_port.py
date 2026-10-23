"""Abstract port interface for contract renewal alert dispatching and notification management.

Defines the contract for dispatching, tracking, suppressing, and querying contract
expiration renewal alerts at 90, 60, and 30 day threshold intervals [FR-9].
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date as date_type, datetime, timezone
from typing import Any, Dict, List, Optional, Union
import uuid

from src.services.contract.models import ContractStatus, RenewalAlertDomain, RenewalThreshold


class NotificationPortError(Exception):
    """Base exception for notification port operations."""

    pass


class NotificationDispatchError(NotificationPortError):
    """Raised when renewal alert dispatch or notification delivery fails."""

    pass


class AlertSuppressedError(NotificationPortError):
    """Raised when alert dispatch is requested for a suppressed contract status or duplicate threshold."""

    pass


class InvalidAlertThresholdError(NotificationPortError):
    """Raised when an invalid or unsupported renewal alert threshold is specified."""

    pass


@dataclass
class RenewalAlertPayload:
    """Dataclass encapsulating details required for dispatching a contract renewal alert [FR-9]."""

    contract_id: uuid.UUID
    threshold_days: Union[RenewalThreshold, int]
    contract_title: str
    counterparty: str
    expires_on: date_type
    contract_status: Union[ContractStatus, str] = ContractStatus.ACTIVE
    recipients: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert renewal alert payload to dictionary representation."""
        threshold_val = (
            self.threshold_days.value if isinstance(self.threshold_days, RenewalThreshold) else int(self.threshold_days)
        )
        status_val = (
            self.contract_status.value if isinstance(self.contract_status, ContractStatus) else str(self.contract_status)
        )
        return {
            "contract_id": str(self.contract_id),
            "threshold_days": threshold_val,
            "contract_title": self.contract_title,
            "counterparty": self.counterparty,
            "expires_on": self.expires_on.isoformat(),
            "contract_status": status_val,
            "recipients": self.recipients,
            "metadata": self.metadata,
        }


@dataclass
class RenewalAlertDispatchResult:
    """Dataclass holding metadata resulting from a renewal alert dispatch attempt [FR-9]."""

    alert_id: uuid.UUID
    contract_id: uuid.UUID
    threshold_days: int
    dispatched_at: datetime
    channel: str = "IN_APP"
    is_suppressed: bool = False
    suppression_reason: Optional[str] = None
    recipients: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert dispatch result to dictionary representation."""
        return {
            "alert_id": str(self.alert_id),
            "contract_id": str(self.contract_id),
            "threshold_days": self.threshold_days,
            "dispatched_at": self.dispatched_at.isoformat(),
            "channel": self.channel,
            "is_suppressed": self.is_suppressed,
            "suppression_reason": self.suppression_reason,
            "recipients": self.recipients,
            "details": self.details,
        }


class NotificationPort(ABC):
    """Abstract port interface for contract renewal alert dispatching.

    Abstracts underlying notification delivery channels (in-app alert records,
    email services, webhooks, external alert hubs) from domain business logic [FR-9].
    """

    @abstractmethod
    def dispatch_renewal_alert(
        self,
        payload: RenewalAlertPayload,
    ) -> RenewalAlertDispatchResult:
        """Dispatch a contract renewal expiration alert for 90, 60, or 30 day thresholds.

        Args:
            payload: Encapsulated renewal alert request payload.

        Returns:
            RenewalAlertDispatchResult indicating alert record metadata and delivery status.

        Raises:
            AlertSuppressedError: If alerts are suppressed for contract status (e.g., TERMINATED)
                or if an alert for this threshold was already dispatched.
            InvalidAlertThresholdError: If threshold_days is not 90, 60, or 30 days.
            NotificationDispatchError: If dispatching fails due to infrastructure or connector errors.
        """
        pass

    @abstractmethod
    def should_suppress_alert(
        self,
        contract_status: Union[ContractStatus, str],
        contract_id: uuid.UUID,
        threshold_days: int,
    ) -> bool:
        """Determine whether renewal alert dispatch should be suppressed for a contract.

        Alerts are suppressed if:
        - Contract status is TERMINATED, EXPIRED, or otherwise non-active [FR-9].
        - An alert for this specific contract_id and threshold_days has already been dispatched.

        Args:
            contract_status: Current status of the contract.
            contract_id: Unique identifier of the contract.
            threshold_days: Threshold interval in days (90, 60, 30).

        Returns:
            True if alert dispatch should be suppressed, False otherwise.
        """
        pass

    @abstractmethod
    def get_dispatched_alert(
        self,
        contract_id: uuid.UUID,
        threshold_days: int,
    ) -> Optional[RenewalAlertDomain]:
        """Retrieve a previously dispatched renewal alert domain record if it exists.

        Args:
            contract_id: Unique identifier of the contract.
            threshold_days: Threshold interval in days (90, 60, 30).

        Returns:
            RenewalAlertDomain instance if previously dispatched, None otherwise.
        """
        pass

    @abstractmethod
    def list_dispatched_alerts(
        self,
        contract_id: Optional[uuid.UUID] = None,
        threshold_days: Optional[int] = None,
    ) -> List[RenewalAlertDomain]:
        """List dispatched renewal alert records filtered by contract or threshold interval.

        Args:
            contract_id: Optional filter for contract ID.
            threshold_days: Optional filter for threshold interval in days.

        Returns:
            List of RenewalAlertDomain instances matching the filter criteria.
        """
        pass

    @abstractmethod
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
        pass
