"""Unit tests for renewal alert notification port interface, exceptions, and DTOs [FR-9]."""

from datetime import date as date_type, datetime, timezone
from typing import Dict, List, Optional, Set, Tuple
import uuid

import pytest

from src.services.contract.models import ContractStatus, RenewalAlertDomain, RenewalThreshold
from src.services.contract.notification_port import (
    AlertSuppressedError,
    InvalidAlertThresholdError,
    NotificationDispatchError,
    NotificationPort,
    NotificationPortError,
    RenewalAlertDispatchResult,
    RenewalAlertPayload,
)


class DummyNotificationAdapter(NotificationPort):
    """In-memory dummy implementation of NotificationPort for verifying interface contracts."""

    VALID_THRESHOLDS = {90, 60, 30}
    SUPPRESSED_STATUSES = {ContractStatus.TERMINATED, ContractStatus.EXPIRED}

    def __init__(self) -> None:
        self._alerts: Dict[uuid.UUID, RenewalAlertDomain] = {}
        self._dispatched_keys: Set[Tuple[uuid.UUID, int]] = set()
        self._cancelled_contracts: Set[uuid.UUID] = set()

    def should_suppress_alert(
        self,
        contract_status: str | ContractStatus,
        contract_id: uuid.UUID,
        threshold_days: int,
    ) -> bool:
        status_enum = (
            contract_status
            if isinstance(contract_status, ContractStatus)
            else ContractStatus(contract_status)
        )
        if status_enum in self.SUPPRESSED_STATUSES:
            return True
        if contract_id in self._cancelled_contracts:
            return True
        if (contract_id, threshold_days) in self._dispatched_keys:
            return True
        return False

    def dispatch_renewal_alert(
        self,
        payload: RenewalAlertPayload,
    ) -> RenewalAlertDispatchResult:
        threshold_val = (
            payload.threshold_days.value
            if isinstance(payload.threshold_days, RenewalThreshold)
            else int(payload.threshold_days)
        )
        if threshold_val not in self.VALID_THRESHOLDS:
            raise InvalidAlertThresholdError(
                f"Invalid threshold days {threshold_val}. Must be one of {self.VALID_THRESHOLDS}."
            )

        if self.should_suppress_alert(payload.contract_status, payload.contract_id, threshold_val):
            raise AlertSuppressedError(
                f"Alert for contract {payload.contract_id} at threshold {threshold_val} is suppressed."
            )

        alert_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        domain_alert = RenewalAlertDomain(
            id=alert_id,
            contract_id=payload.contract_id,
            threshold_days=threshold_val,
            dispatched_at=now,
        )
        self._alerts[alert_id] = domain_alert
        self._dispatched_keys.add((payload.contract_id, threshold_val))

        return RenewalAlertDispatchResult(
            alert_id=alert_id,
            contract_id=payload.contract_id,
            threshold_days=threshold_val,
            dispatched_at=now,
            channel="IN_APP",
            is_suppressed=False,
            recipients=payload.recipients,
        )

    def get_dispatched_alert(
        self,
        contract_id: uuid.UUID,
        threshold_days: int,
    ) -> Optional[RenewalAlertDomain]:
        for alert in self._alerts.values():
            if alert.contract_id == contract_id and alert.threshold_days == threshold_days:
                return alert
        return None

    def list_dispatched_alerts(
        self,
        contract_id: Optional[uuid.UUID] = None,
        threshold_days: Optional[int] = None,
    ) -> List[RenewalAlertDomain]:
        results = list(self._alerts.values())
        if contract_id:
            results = [a for a in results if a.contract_id == contract_id]
        if threshold_days:
            results = [a for a in results if a.threshold_days == threshold_days]
        return results

    def cancel_pending_alerts(
        self,
        contract_id: uuid.UUID,
        reason: Optional[str] = None,
    ) -> int:
        self._cancelled_contracts.add(contract_id)
        return len([a for a in self._alerts.values() if a.contract_id == contract_id])


def test_notification_exceptions():
    """Verify custom notification exception hierarchy."""
    assert issubclass(NotificationDispatchError, NotificationPortError)
    assert issubclass(AlertSuppressedError, NotificationPortError)
    assert issubclass(InvalidAlertThresholdError, NotificationPortError)


def test_renewal_alert_payload_serialization():
    """Verify RenewalAlertPayload initialization and serialization."""
    contract_id = uuid.uuid4()
    exp_date = date_type(2026, 12, 31)

    payload = RenewalAlertPayload(
        contract_id=contract_id,
        threshold_days=RenewalThreshold.DAYS_90,
        contract_title="Master Services Agreement",
        counterparty="Acme Corp",
        expires_on=exp_date,
        contract_status=ContractStatus.ACTIVE,
        recipients=["legal@acme.com"],
        metadata={"priority": "HIGH"},
    )

    d = payload.to_dict()
    assert d["contract_id"] == str(contract_id)
    assert d["threshold_days"] == 90
    assert d["contract_title"] == "Master Services Agreement"
    assert d["counterparty"] == "Acme Corp"
    assert d["expires_on"] == "2026-12-31"
    assert d["contract_status"] == "ACTIVE"
    assert d["recipients"] == ["legal@acme.com"]
    assert d["metadata"] == {"priority": "HIGH"}


def test_renewal_alert_dispatch_result_serialization():
    """Verify RenewalAlertDispatchResult initialization and serialization."""
    alert_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    result = RenewalAlertDispatchResult(
        alert_id=alert_id,
        contract_id=contract_id,
        threshold_days=60,
        dispatched_at=now,
        channel="EMAIL",
        is_suppressed=False,
        recipients=["ops@partner.com"],
    )

    d = result.to_dict()
    assert d["alert_id"] == str(alert_id)
    assert d["contract_id"] == str(contract_id)
    assert d["threshold_days"] == 60
    assert d["dispatched_at"] == now.isoformat()
    assert d["channel"] == "EMAIL"
    assert d["is_suppressed"] is False
    assert d["recipients"] == ["ops@partner.com"]


def test_dummy_adapter_dispatch_happy_path():
    """Verify dispatching alerts for valid thresholds on active contracts."""
    adapter = DummyNotificationAdapter()
    contract_id = uuid.uuid4()
    exp_date = date_type(2026, 12, 31)

    for threshold in [RenewalThreshold.DAYS_90, RenewalThreshold.DAYS_60, RenewalThreshold.DAYS_30]:
        payload = RenewalAlertPayload(
            contract_id=contract_id,
            threshold_days=threshold,
            contract_title="Vendor Agreement",
            counterparty="Global Tech",
            expires_on=exp_date,
            contract_status=ContractStatus.ACTIVE,
        )
        res = adapter.dispatch_renewal_alert(payload)
        assert res.contract_id == contract_id
        assert res.threshold_days == threshold.value
        assert res.is_suppressed is False

    alerts = adapter.list_dispatched_alerts(contract_id=contract_id)
    assert len(alerts) == 3


def test_dummy_adapter_suppresses_terminated_contract_alerts():
    """Verify that terminated or expired contract alerts are suppressed [FR-9]."""
    adapter = DummyNotificationAdapter()
    contract_id = uuid.uuid4()
    exp_date = date_type(2026, 12, 31)

    for status in [ContractStatus.TERMINATED, ContractStatus.EXPIRED]:
        payload = RenewalAlertPayload(
            contract_id=contract_id,
            threshold_days=90,
            contract_title="Terminated Contract",
            counterparty="Legacy Corp",
            expires_on=exp_date,
            contract_status=status,
        )
        assert adapter.should_suppress_alert(status, contract_id, 90) is True
        with pytest.raises(AlertSuppressedError):
            adapter.dispatch_renewal_alert(payload)


def test_dummy_adapter_suppresses_duplicate_threshold_alerts():
    """Verify that duplicate alert dispatch for the same contract and threshold is suppressed."""
    adapter = DummyNotificationAdapter()
    contract_id = uuid.uuid4()
    exp_date = date_type(2026, 12, 31)

    payload = RenewalAlertPayload(
        contract_id=contract_id,
        threshold_days=60,
        contract_title="SLA Contract",
        counterparty="Cloud Systems",
        expires_on=exp_date,
        contract_status=ContractStatus.ACTIVE,
    )

    res = adapter.dispatch_renewal_alert(payload)
    assert res.threshold_days == 60

    # Second dispatch attempt for the same threshold should raise AlertSuppressedError
    with pytest.raises(AlertSuppressedError):
        adapter.dispatch_renewal_alert(payload)


def test_dummy_adapter_invalid_threshold():
    """Verify that unsupported threshold days raise InvalidAlertThresholdError."""
    adapter = DummyNotificationAdapter()
    contract_id = uuid.uuid4()
    exp_date = date_type(2026, 12, 31)

    payload = RenewalAlertPayload(
        contract_id=contract_id,
        threshold_days=15,  # Invalid threshold (not 90, 60, or 30)
        contract_title="Invalid Threshold Contract",
        counterparty="Test Corp",
        expires_on=exp_date,
    )

    with pytest.raises(InvalidAlertThresholdError):
        adapter.dispatch_renewal_alert(payload)


def test_dummy_adapter_get_and_list_dispatched_alerts():
    """Verify get_dispatched_alert and filtering in list_dispatched_alerts."""
    adapter = DummyNotificationAdapter()
    c1 = uuid.uuid4()
    c2 = uuid.uuid4()
    exp_date = date_type(2026, 12, 31)

    adapter.dispatch_renewal_alert(
        RenewalAlertPayload(c1, 90, "Contract 1", "Party 1", exp_date)
    )
    adapter.dispatch_renewal_alert(
        RenewalAlertPayload(c1, 60, "Contract 1", "Party 1", exp_date)
    )
    adapter.dispatch_renewal_alert(
        RenewalAlertPayload(c2, 90, "Contract 2", "Party 2", exp_date)
    )

    assert adapter.get_dispatched_alert(c1, 90) is not None
    assert adapter.get_dispatched_alert(c1, 30) is None

    assert len(adapter.list_dispatched_alerts(contract_id=c1)) == 2
    assert len(adapter.list_dispatched_alerts(threshold_days=90)) == 2
    assert len(adapter.list_dispatched_alerts(contract_id=c2, threshold_days=90)) == 1


def test_dummy_adapter_cancel_pending_alerts():
    """Verify cancelling/suppressing pending alerts for a contract."""
    adapter = DummyNotificationAdapter()
    c1 = uuid.uuid4()
    exp_date = date_type(2026, 12, 31)

    adapter.cancel_pending_alerts(c1, reason="Contract Renewed")
    assert adapter.should_suppress_alert(ContractStatus.ACTIVE, c1, 90) is True

    payload = RenewalAlertPayload(c1, 90, "Contract 1", "Party 1", exp_date)
    with pytest.raises(AlertSuppressedError):
        adapter.dispatch_renewal_alert(payload)
