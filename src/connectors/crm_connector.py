"""External CRM connector adapter.

Implements the CRM data port contract (`CRMConnectorPort`) to retrieve deal stage history,
stakeholder activity logs, and CRM deal records [FR-8, NFR-5]. Provides fault isolation,
configurable retries, timeout boundaries, and simulated fallback execution.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

try:
    import httpx
except ModuleNotFoundError:
    import json as _json
    import urllib.error
    import urllib.request

    class _HttpxResponse:
        def __init__(self, status_code=200, content=b""):
            self.status_code = status_code
            self._content = content

        @property
        def text(self):
            return self._content.decode("utf-8", errors="replace")

        def json(self):
            return _json.loads(self.text) if self._content else {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise _HTTPStatusError(f"HTTP Error {self.status_code}", response=self)

    class _TimeoutException(Exception):
        pass

    class _HTTPStatusError(Exception):
        def __init__(self, msg, response=None):
            super().__init__(msg)
            self.response = response

    class _RequestError(Exception):
        pass

    class _HttpxClient:
        def __init__(self, timeout=None, *args, **kwargs):
            self.timeout = timeout if isinstance(timeout, (int, float)) else 10.0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, method, url, params=None, headers=None, json=None, data=None, **kwargs):
            if params:
                from urllib.parse import urlencode
                url = f"{url}?{urlencode(params)}"
            req_headers = headers or {}
            req_data = None
            if json is not None:
                req_data = _json.dumps(json).encode("utf-8")
                if "Content-Type" not in req_headers:
                    req_headers["Content-Type"] = "application/json"
            elif data is not None:
                if isinstance(data, str):
                    req_data = data.encode("utf-8")
                elif isinstance(data, bytes):
                    req_data = data

            req = urllib.request.Request(url, data=req_data, headers=req_headers, method=method.upper())
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    resp_data = resp.read()
                    return _HttpxResponse(status_code=resp.status, content=resp_data)
            except urllib.error.HTTPError as err:
                resp_data = err.read()
                return _HttpxResponse(status_code=err.code, content=resp_data)
            except urllib.error.URLError as err:
                if isinstance(err.reason, TimeoutError) or "timed out" in str(err.reason).lower():
                    raise _TimeoutException(str(err))
                raise _RequestError(str(err))
            except TimeoutError as err:
                raise _TimeoutException(str(err))
            except Exception as err:
                raise _RequestError(str(err))

        def get(self, url, **kwargs):
            return self.request("GET", url, **kwargs)

        def post(self, url, **kwargs):
            return self.request("POST", url, **kwargs)

    class _HttpxModule:
        Client = _HttpxClient
        TimeoutException = _TimeoutException
        HTTPStatusError = _HTTPStatusError
        RequestError = _RequestError

    httpx = _HttpxModule()

from src.lib.config import settings
from src.services.deals.crm_port import (
    CRMActivityItem,
    CRMConnectionError,
    CRMConnectorError,
    CRMConnectorPort,
    CRMDealRecord,
    CRMNotFoundError,
    CRMStageHistoryItem,
    CRMTimeoutError,
)


class CRMConnector(CRMConnectorPort):
    """Adapter for interacting with external CRM endpoints or providing simulated CRM data."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        simulate_failure: bool = False,
        simulate_timeout: bool = False,
        simulate_sparse_data: bool = False,
        simulate_not_found: bool = False,
    ) -> None:
        """Initialize the CRM connector adapter.

        Args:
            api_url: Optional upstream API endpoint URL for external CRM service.
            api_key: Optional bearer authorization token for upstream service.
            timeout: Execution timeout limit in seconds.
            retries: Retry attempts for transient upstream network/HTTP failures.
            simulate_failure: Testing flag to simulate upstream connection failures.
            simulate_timeout: Testing flag to simulate execution timeout limits.
            simulate_sparse_data: Testing flag to return sparse or empty CRM records.
            simulate_not_found: Testing flag to simulate missing resource responses.
        """
        self.api_url = api_url
        self.api_key = api_key
        self.timeout = (
            timeout
            if timeout is not None
            else float(settings.crm_connector_timeout_seconds)
        )
        self.retries = (
            retries
            if retries is not None
            else settings.dashboard_connector_retries
        )
        self.simulate_failure = simulate_failure
        self.simulate_timeout = simulate_timeout
        self.simulate_sparse_data = simulate_sparse_data
        self.simulate_not_found = simulate_not_found

        self._simulated_deals: Dict[str, CRMDealRecord] = {}
        self._seed_default_deals()

    def add_simulated_deal(self, deal: CRMDealRecord) -> None:
        """Register or overwrite a simulated CRM deal record in memory."""
        self._simulated_deals[deal.deal_id] = deal

    def clear_simulated_deals(self) -> None:
        """Clear all in-memory simulated CRM deal records."""
        self._simulated_deals.clear()

    def get_deal_stage_history(self, deal_id: str) -> List[CRMStageHistoryItem]:
        """Retrieve chronological stage transition history for a specific deal."""
        self._check_simulation_triggers(deal_id)

        if self.api_url:
            return self._fetch_stage_history_from_api(deal_id)

        if deal_id not in self._simulated_deals:
            raise CRMNotFoundError(f"Deal '{deal_id}' not found in CRM system")

        if self.simulate_sparse_data:
            return []

        return self._simulated_deals[deal_id].stage_history

    def get_deal_activities(
        self, deal_id: str, limit: int = 50
    ) -> List[CRMActivityItem]:
        """Retrieve recent interaction activity records for a specific deal."""
        self._check_simulation_triggers(deal_id)

        if self.api_url:
            return self._fetch_activities_from_api(deal_id, limit)

        if deal_id not in self._simulated_deals:
            raise CRMNotFoundError(f"Deal '{deal_id}' not found in CRM system")

        if self.simulate_sparse_data:
            return []

        activities = self._simulated_deals[deal_id].activities
        return sorted(activities, key=lambda a: a.occurred_on, reverse=True)[:limit]

    def get_deal_record(self, deal_id: str) -> Optional[CRMDealRecord]:
        """Retrieve full CRM deal record including history and activity feed."""
        self._check_simulation_triggers(deal_id)

        if self.api_url:
            try:
                return self._fetch_deal_record_from_api(deal_id)
            except CRMNotFoundError:
                return None

        record = self._simulated_deals.get(deal_id)
        if record is None:
            return None

        if self.simulate_sparse_data:
            return CRMDealRecord(
                deal_id=record.deal_id,
                title=record.title,
                deal_value=record.deal_value,
                milestone_stage=record.milestone_stage,
                win_probability=record.win_probability,
                created_on=record.created_on,
                updated_on=record.updated_on,
                stage_history=[],
                activities=[],
            )

        return record

    def get_historical_deals_dataset(
        self, min_records: int = 1
    ) -> List[CRMDealRecord]:
        """Retrieve dataset of historical deals for AI completion model forecasting baselines."""
        self._check_simulation_triggers("historical_dataset")

        if self.api_url:
            return self._fetch_historical_dataset_from_api(min_records)

        if self.simulate_sparse_data:
            return []

        return list(self._simulated_deals.values())

    def _check_simulation_triggers(self, context_id: str) -> None:
        """Check simulation flags and raise configured test exceptions."""
        if self.simulate_timeout:
            raise CRMTimeoutError(
                f"CRM connector call for '{context_id}' timed out after {self.timeout}s"
            )

        if self.simulate_failure:
            raise CRMConnectionError(
                f"CRM connector call for '{context_id}' failed with upstream error"
            )

        if self.simulate_not_found:
            raise CRMNotFoundError(
                f"CRM resource for '{context_id}' was not found"
            )

    def _execute_http_request(
        self, method: str, path: str, params: Optional[dict] = None
    ) -> dict:
        """Execute HTTP request against external CRM API with retries and timeout enforcement."""
        if not self.api_url:
            raise CRMConnectionError("Upstream API URL is not configured")

        url = f"{self.api_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Optional[Exception] = None
        for attempt in range(1 + self.retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    res = client.request(method, url, headers=headers, params=params)
                    if res.status_code == 404:
                        raise CRMNotFoundError(
                            f"Resource not found at upstream CRM URL '{url}'"
                        )
                    res.raise_for_status()
                    return res.json()
            except CRMNotFoundError:
                raise
            except httpx.TimeoutException as exc:
                last_error = CRMTimeoutError(
                    f"CRM API timeout on attempt {attempt + 1} at '{url}': {str(exc)}"
                )
            except httpx.HTTPStatusError as exc:
                last_error = CRMConnectionError(
                    f"CRM API HTTP error {exc.response.status_code} at '{url}'"
                )
            except Exception as exc:
                last_error = CRMConnectionError(
                    f"CRM API connection error at '{url}': {str(exc)}"
                )

        if last_error:
            raise last_error
        raise CRMConnectionError(f"CRM request failed after {self.retries} retries")

    def _fetch_stage_history_from_api(
        self, deal_id: str
    ) -> List[CRMStageHistoryItem]:
        """Fetch deal stage transition history from external API."""
        data = self._execute_http_request(
            "GET", f"deals/{deal_id}/stage-history"
        )
        items = []
        for raw in data.get("items", []):
            items.append(
                CRMStageHistoryItem(
                    deal_id=raw["deal_id"],
                    milestone_stage=raw["milestone_stage"],
                    entered_at=datetime.fromisoformat(raw["entered_at"]),
                    exited_at=(
                        datetime.fromisoformat(raw["exited_at"])
                        if raw.get("exited_at")
                        else None
                    ),
                    duration_days=float(raw.get("duration_days", 0.0)),
                    win_probability=float(raw.get("win_probability", 0.0)),
                    deal_value=float(raw.get("deal_value", 0.0)),
                )
            )
        return items

    def _fetch_activities_from_api(
        self, deal_id: str, limit: int
    ) -> List[CRMActivityItem]:
        """Fetch stakeholder activity logs from external API."""
        data = self._execute_http_request(
            "GET", f"deals/{deal_id}/activities", params={"limit": limit}
        )
        items = []
        for raw in data.get("items", []):
            items.append(
                CRMActivityItem(
                    activity_id=raw["activity_id"],
                    deal_id=raw["deal_id"],
                    activity_type=raw["activity_type"],
                    summary=raw["summary"],
                    occurred_on=datetime.fromisoformat(raw["occurred_on"]),
                    stakeholder_id=raw.get("stakeholder_id"),
                    stakeholder_email=raw.get("stakeholder_email"),
                )
            )
        return items

    def _fetch_deal_record_from_api(self, deal_id: str) -> CRMDealRecord:
        """Fetch single deal record from external API."""
        raw = self._execute_http_request("GET", f"deals/{deal_id}")
        stage_history = [
            CRMStageHistoryItem(
                deal_id=sh["deal_id"],
                milestone_stage=sh["milestone_stage"],
                entered_at=datetime.fromisoformat(sh["entered_at"]),
                exited_at=(
                    datetime.fromisoformat(sh["exited_at"])
                    if sh.get("exited_at")
                    else None
                ),
                duration_days=float(sh.get("duration_days", 0.0)),
                win_probability=float(sh.get("win_probability", 0.0)),
                deal_value=float(sh.get("deal_value", 0.0)),
            )
            for sh in raw.get("stage_history", [])
        ]
        activities = [
            CRMActivityItem(
                activity_id=act["activity_id"],
                deal_id=act["deal_id"],
                activity_type=act["activity_type"],
                summary=act["summary"],
                occurred_on=datetime.fromisoformat(act["occurred_on"]),
                stakeholder_id=act.get("stakeholder_id"),
                stakeholder_email=act.get("stakeholder_email"),
            )
            for act in raw.get("activities", [])
        ]
        return CRMDealRecord(
            deal_id=raw["deal_id"],
            title=raw["title"],
            deal_value=float(raw["deal_value"]),
            milestone_stage=raw["milestone_stage"],
            win_probability=float(raw["win_probability"]),
            created_on=datetime.fromisoformat(raw["created_on"]),
            updated_on=datetime.fromisoformat(raw["updated_on"]),
            stage_history=stage_history,
            activities=activities,
        )

    def _fetch_historical_dataset_from_api(
        self, min_records: int
    ) -> List[CRMDealRecord]:
        """Fetch historical dataset of deals from external API."""
        data = self._execute_http_request(
            "GET", "deals/historical", params={"min_records": min_records}
        )
        records = []
        for raw in data.get("items", []):
            stage_history = [
                CRMStageHistoryItem(
                    deal_id=sh["deal_id"],
                    milestone_stage=sh["milestone_stage"],
                    entered_at=datetime.fromisoformat(sh["entered_at"]),
                    exited_at=(
                        datetime.fromisoformat(sh["exited_at"])
                        if sh.get("exited_at")
                        else None
                    ),
                    duration_days=float(sh.get("duration_days", 0.0)),
                    win_probability=float(sh.get("win_probability", 0.0)),
                    deal_value=float(sh.get("deal_value", 0.0)),
                )
                for sh in raw.get("stage_history", [])
            ]
            activities = [
                CRMActivityItem(
                    activity_id=act["activity_id"],
                    deal_id=act["deal_id"],
                    activity_type=act["activity_type"],
                    summary=act["summary"],
                    occurred_on=datetime.fromisoformat(act["occurred_on"]),
                    stakeholder_id=act.get("stakeholder_id"),
                    stakeholder_email=act.get("stakeholder_email"),
                )
                for act in raw.get("activities", [])
            ]
            records.append(
                CRMDealRecord(
                    deal_id=raw["deal_id"],
                    title=raw["title"],
                    deal_value=float(raw["deal_value"]),
                    milestone_stage=raw["milestone_stage"],
                    win_probability=float(raw["win_probability"]),
                    created_on=datetime.fromisoformat(raw["created_on"]),
                    updated_on=datetime.fromisoformat(raw["updated_on"]),
                    stage_history=stage_history,
                    activities=activities,
                )
            )
        return records

    def _seed_default_deals(self) -> None:
        """Seed default realistic CRM deal records into the in-memory simulated dataset."""
        now = datetime.now(timezone.utc)

        deal_101 = CRMDealRecord(
            deal_id="deal-101",
            title="Enterprise SaaS Expansion - Acme Corp",
            deal_value=150000.0,
            milestone_stage="Negotiation",
            win_probability=0.85,
            created_on=now - timedelta(days=60),
            updated_on=now - timedelta(days=2),
            stage_history=[
                CRMStageHistoryItem(
                    deal_id="deal-101",
                    milestone_stage="Lead Qualification",
                    entered_at=now - timedelta(days=60),
                    exited_at=now - timedelta(days=50),
                    duration_days=10.0,
                    win_probability=0.20,
                    deal_value=150000.0,
                ),
                CRMStageHistoryItem(
                    deal_id="deal-101",
                    milestone_stage="Discovery Call",
                    entered_at=now - timedelta(days=50),
                    exited_at=now - timedelta(days=35),
                    duration_days=15.0,
                    win_probability=0.40,
                    deal_value=150000.0,
                ),
                CRMStageHistoryItem(
                    deal_id="deal-101",
                    milestone_stage="Solution Proposal",
                    entered_at=now - timedelta(days=35),
                    exited_at=now - timedelta(days=15),
                    duration_days=20.0,
                    win_probability=0.65,
                    deal_value=150000.0,
                ),
                CRMStageHistoryItem(
                    deal_id="deal-101",
                    milestone_stage="Negotiation",
                    entered_at=now - timedelta(days=15),
                    exited_at=None,
                    duration_days=15.0,
                    win_probability=0.85,
                    deal_value=150000.0,
                ),
            ],
            activities=[
                CRMActivityItem(
                    activity_id="act-101-1",
                    deal_id="deal-101",
                    activity_type="Email",
                    summary="Sent revised SaaS proposal draft to VP of IT",
                    occurred_on=now - timedelta(days=10),
                    stakeholder_id="sh-acme-1",
                    stakeholder_email="john.smith@acme.com",
                ),
                CRMActivityItem(
                    activity_id="act-101-2",
                    deal_id="deal-101",
                    activity_type="Meeting",
                    summary="Executive alignment meeting with CFO",
                    occurred_on=now - timedelta(days=5),
                    stakeholder_id="sh-acme-2",
                    stakeholder_email="jane.doe@acme.com",
                ),
                CRMActivityItem(
                    activity_id="act-101-3",
                    deal_id="deal-101",
                    activity_type="Call",
                    summary="Security and compliance review call with CISO",
                    occurred_on=now - timedelta(days=2),
                    stakeholder_id="sh-acme-3",
                    stakeholder_email="security@acme.com",
                ),
            ],
        )

        deal_102 = CRMDealRecord(
            deal_id="deal-102",
            title="Cloud Migration Services - TechCorp",
            deal_value=85000.0,
            milestone_stage="Solution Proposal",
            win_probability=0.60,
            created_on=now - timedelta(days=40),
            updated_on=now - timedelta(days=8),
            stage_history=[
                CRMStageHistoryItem(
                    deal_id="deal-102",
                    milestone_stage="Lead Qualification",
                    entered_at=now - timedelta(days=40),
                    exited_at=now - timedelta(days=30),
                    duration_days=10.0,
                    win_probability=0.20,
                    deal_value=85000.0,
                ),
                CRMStageHistoryItem(
                    deal_id="deal-102",
                    milestone_stage="Discovery Call",
                    entered_at=now - timedelta(days=30),
                    exited_at=now - timedelta(days=18),
                    duration_days=12.0,
                    win_probability=0.40,
                    deal_value=85000.0,
                ),
                CRMStageHistoryItem(
                    deal_id="deal-102",
                    milestone_stage="Solution Proposal",
                    entered_at=now - timedelta(days=18),
                    exited_at=None,
                    duration_days=18.0,
                    win_probability=0.60,
                    deal_value=85000.0,
                ),
            ],
            activities=[
                CRMActivityItem(
                    activity_id="act-102-1",
                    deal_id="deal-102",
                    activity_type="Meeting",
                    summary="Technical architecture discovery session",
                    occurred_on=now - timedelta(days=12),
                    stakeholder_id="sh-tech-1",
                    stakeholder_email="cto@techcorp.com",
                ),
                CRMActivityItem(
                    activity_id="act-102-2",
                    deal_id="deal-102",
                    activity_type="Note",
                    summary="Client requested hybrid deployment option in proposal",
                    occurred_on=now - timedelta(days=8),
                    stakeholder_id="sh-tech-1",
                    stakeholder_email="cto@techcorp.com",
                ),
            ],
        )

        deal_sparse = CRMDealRecord(
            deal_id="deal-sparse-1",
            title="Early Stage Discovery - StartupX",
            deal_value=25000.0,
            milestone_stage="Lead Qualification",
            win_probability=0.15,
            created_on=now - timedelta(days=3),
            updated_on=now - timedelta(days=3),
            stage_history=[
                CRMStageHistoryItem(
                    deal_id="deal-sparse-1",
                    milestone_stage="Lead Qualification",
                    entered_at=now - timedelta(days=3),
                    exited_at=None,
                    duration_days=3.0,
                    win_probability=0.15,
                    deal_value=25000.0,
                )
            ],
            activities=[],
        )

        self._simulated_deals[deal_101.deal_id] = deal_101
        self._simulated_deals[deal_102.deal_id] = deal_102
        self._simulated_deals[deal_sparse.deal_id] = deal_sparse
