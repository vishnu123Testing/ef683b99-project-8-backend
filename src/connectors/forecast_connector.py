"""Forecast provider connector adapter for AI deal completion timelines and confidence scores.

Implements the forecast provider port (`ForecastProviderPort`) to compute AI-driven estimated deal
completion dates and percentage-based confidence scores from CRM history and real-time activity (FR-8, FR-9, NFR-5).
Provides explicit CRM data provider injection, configurable timeouts, fallback handling for sparse data,
and test simulation capabilities.
"""

from datetime import datetime, timedelta, timezone, date as date_type
from typing import Dict, List, Optional, Any, Union
import json
import logging

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
    CRMConnectorPort,
    CRMDealRecord,
    CRMNotFoundError,
    CRMStageHistoryItem,
)
from src.services.forecasting.forecast_port import (
    ForecastConnectionError,
    ForecastError,
    ForecastProviderError,
    ForecastProviderPort,
    ForecastResult,
    ForecastTimeoutError,
    InsufficientDataError,
)

logger = logging.getLogger(__name__)


class ForecastConnector(ForecastProviderPort):
    """Adapter for interacting with external AI forecasting provider or performing local estimation."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        crm_provider: Optional[CRMConnectorPort] = None,
        confidence_threshold_pct: Optional[float] = None,
        min_history_records: Optional[int] = None,
        simulate_failure: bool = False,
        simulate_timeout: bool = False,
        simulate_insufficient_data: bool = False,
        simulate_low_confidence: bool = False,
    ) -> None:
        """Initialize the forecast provider connector adapter.

        Args:
            api_url: Optional upstream endpoint URL for external AI forecasting service.
            api_key: Optional bearer authorization token for external service.
            timeout: Execution timeout limit in seconds.
            retries: Retry attempts for transient failures.
            crm_provider: Explicitly injected CRM data provider (NFR-5).
            confidence_threshold_pct: Minimum confidence threshold percentage.
            min_history_records: Minimum required stage history and activity records.
            simulate_failure: Test flag simulating upstream provider failure.
            simulate_timeout: Test flag simulating provider timeout boundary.
            simulate_insufficient_data: Test flag simulating sparse CRM data fallback.
            simulate_low_confidence: Test flag simulating low confidence estimation below threshold.
        """
        self.api_url = api_url
        self.api_key = api_key
        self.timeout = (
            timeout
            if timeout is not None
            else float(settings.forecast_provider_timeout_seconds)
        )
        self.retries = (
            retries
            if retries is not None
            else settings.dashboard_connector_retries
        )
        self.default_crm_provider = crm_provider
        self.confidence_threshold_pct = (
            confidence_threshold_pct
            if confidence_threshold_pct is not None
            else settings.forecast_confidence_threshold_pct
        )
        self.min_history_records = (
            min_history_records
            if min_history_records is not None
            else settings.forecast_min_history_records
        )
        self.simulate_failure = simulate_failure
        self.simulate_timeout = simulate_timeout
        self.simulate_insufficient_data = simulate_insufficient_data
        self.simulate_low_confidence = simulate_low_confidence

    def calculate_forecast(
        self,
        deal_id: str,
        crm_provider: Optional[CRMConnectorPort] = None,
        deal_record: Optional[CRMDealRecord] = None,
    ) -> ForecastResult:
        """Calculate estimated completion date and confidence score for a deal.

        Uses explicitly injected CRM data provider or deal record parameter (NFR-5).
        Returns a ForecastResult with status_code='READY' or 'INSUFFICIENT_DATA'.
        """
        self._check_simulation_triggers(deal_id)

        now_time = datetime.now(timezone.utc)

        if self.simulate_insufficient_data:
            return ForecastResult(
                deal_id=deal_id,
                estimated_completion_date=None,
                confidence_score_pct=None,
                status_code="INSUFFICIENT_DATA",
                calculated_on=now_time,
                error_detail="Simulated insufficient CRM history for AI forecasting",
            )

        # Resolve effective CRM provider
        effective_crm = crm_provider or self.default_crm_provider

        # Fetch deal record from CRM provider if not directly provided
        target_deal = deal_record
        if target_deal is None and effective_crm is not None:
            try:
                target_deal = effective_crm.get_deal_record(deal_id)
            except CRMNotFoundError:
                logger.warning("Deal '%s' not found in CRM provider", deal_id)
                return ForecastResult(
                    deal_id=deal_id,
                    estimated_completion_date=None,
                    confidence_score_pct=None,
                    status_code="INSUFFICIENT_DATA",
                    calculated_on=now_time,
                    error_detail=f"Deal '{deal_id}' not found in CRM provider",
                )
            except Exception as err:
                logger.error("Error retrieving CRM deal record for '%s': %s", deal_id, err)
                raise ForecastProviderError(f"CRM provider error: {str(err)}") from err

        if self.api_url:
            return self._fetch_forecast_from_external_api(deal_id, target_deal)

        return self._compute_local_forecast(deal_id, target_deal, now_time)

    def evaluate_batch_forecasts(
        self,
        deal_ids: List[str],
        crm_provider: Optional[CRMConnectorPort] = None,
    ) -> List[ForecastResult]:
        """Calculate forecasts for a batch of deals."""
        results: List[ForecastResult] = []
        for d_id in deal_ids:
            try:
                res = self.calculate_forecast(d_id, crm_provider=crm_provider)
                results.append(res)
            except ForecastError as err:
                results.append(
                    ForecastResult(
                        deal_id=d_id,
                        estimated_completion_date=None,
                        confidence_score_pct=None,
                        status_code="INSUFFICIENT_DATA",
                        calculated_on=datetime.now(timezone.utc),
                        error_detail=str(err),
                    )
                )
        return results

    def _check_simulation_triggers(self, deal_id: str) -> None:
        """Verify simulation testing triggers and raise simulated exceptions if requested."""
        if self.simulate_timeout:
            raise ForecastTimeoutError(
                f"Simulated execution timeout boundary reached for forecast provider call on deal '{deal_id}'"
            )
        if self.simulate_failure:
            raise ForecastProviderError(
                f"Simulated upstream AI forecasting provider connection failure for deal '{deal_id}'"
            )

    def _compute_local_forecast(
        self,
        deal_id: str,
        deal_record: Optional[CRMDealRecord],
        now_time: datetime,
    ) -> ForecastResult:
        """Compute algorithmic completion estimation and confidence score from deal history."""
        if deal_record is None:
            return ForecastResult(
                deal_id=deal_id,
                estimated_completion_date=None,
                confidence_score_pct=None,
                status_code="INSUFFICIENT_DATA",
                calculated_on=now_time,
                error_detail="No CRM deal record or stage history available for forecasting",
            )

        history_count = len(deal_record.stage_history)
        activity_count = len(deal_record.activities)
        total_data_points = history_count + activity_count

        if total_data_points < self.min_history_records:
            return ForecastResult(
                deal_id=deal_id,
                estimated_completion_date=None,
                confidence_score_pct=None,
                status_code="INSUFFICIENT_DATA",
                calculated_on=now_time,
                error_detail=(
                    f"Insufficient CRM data points ({total_data_points} < "
                    f"minimum required {self.min_history_records})"
                ),
            )

        # Baseline stage remaining days estimation map
        stage_remaining_days_map: Dict[str, float] = {
            "Lead Qualification": 60.0,
            "Prospecting": 60.0,
            "Qualification": 45.0,
            "Needs Analysis": 40.0,
            "Value Proposition": 30.0,
            "Proposal": 25.0,
            "Negotiation": 15.0,
            "Review": 10.0,
            "Closed Won": 0.0,
            "Closed Lost": 0.0,
        }

        current_stage = deal_record.milestone_stage
        base_remaining_days = stage_remaining_days_map.get(current_stage, 30.0)

        # Refine estimation using stage velocity if stage history exists
        if history_count > 1:
            total_history_duration = sum(h.duration_days for h in deal_record.stage_history)
            avg_stage_duration = total_history_duration / history_count
            if avg_stage_duration > 0:
                # Adjust remaining days based on deal's velocity relative to standard 10-day stage avg
                velocity_multiplier = min(max(avg_stage_duration / 10.0, 0.5), 2.0)
                base_remaining_days = base_remaining_days * velocity_multiplier

        # Calculate estimated completion date
        completion_timestamp = now_time + timedelta(days=base_remaining_days)

        # Calculate confidence score percentage
        stage_confidence_map: Dict[str, float] = {
            "Lead Qualification": 40.0,
            "Prospecting": 45.0,
            "Qualification": 55.0,
            "Needs Analysis": 60.0,
            "Value Proposition": 68.0,
            "Proposal": 75.0,
            "Negotiation": 85.0,
            "Review": 90.0,
            "Closed Won": 100.0,
            "Closed Lost": 0.0,
        }
        base_confidence = stage_confidence_map.get(current_stage, 50.0)

        # Adjust confidence with data richness bonus
        data_richness_bonus = min(total_data_points * 2.0, 15.0)
        prob_adjustment = (deal_record.win_probability - 0.5) * 20.0

        computed_confidence = base_confidence + data_richness_bonus + prob_adjustment

        if self.simulate_low_confidence:
            computed_confidence = min(computed_confidence, self.confidence_threshold_pct - 10.0)

        computed_confidence = round(max(min(computed_confidence, 99.0), 5.0), 2)

        if computed_confidence < self.confidence_threshold_pct:
            return ForecastResult(
                deal_id=deal_id,
                estimated_completion_date=None,
                confidence_score_pct=computed_confidence,
                status_code="INSUFFICIENT_DATA",
                calculated_on=now_time,
                error_detail=(
                    f"Computed confidence ({computed_confidence}%) below threshold "
                    f"({self.confidence_threshold_pct}%)"
                ),
            )

        metadata_dict = {
            "model_type": "HeuristicVelocityEstimator_v1",
            "milestone_stage": current_stage,
            "estimated_remaining_days": round(base_remaining_days, 1),
            "historical_data_points": total_data_points,
            "win_probability": deal_record.win_probability,
            "base_confidence": base_confidence,
        }

        return ForecastResult(
            deal_id=deal_id,
            estimated_completion_date=completion_timestamp,
            confidence_score_pct=computed_confidence,
            status_code="READY",
            calculated_on=now_time,
            model_metadata=json.dumps(metadata_dict),
        )

    def _fetch_forecast_from_external_api(
        self,
        deal_id: str,
        deal_record: Optional[CRMDealRecord],
    ) -> ForecastResult:
        """Fetch forecast calculations from external AI provider API endpoint."""
        now_time = datetime.now(timezone.utc)
        payload = {
            "deal_id": deal_id,
            "deal_title": deal_record.title if deal_record else None,
            "milestone_stage": deal_record.milestone_stage if deal_record else None,
            "win_probability": deal_record.win_probability if deal_record else None,
            "stage_history_length": len(deal_record.stage_history) if deal_record else 0,
        }
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.api_url}/v1/forecast",
                    json=payload,
                    headers=headers,
                )
                if response.status_code == 404:
                    return ForecastResult(
                        deal_id=deal_id,
                        estimated_completion_date=None,
                        confidence_score_pct=None,
                        status_code="INSUFFICIENT_DATA",
                        calculated_on=now_time,
                        error_detail="Deal not found in external AI forecasting service",
                    )
                response.raise_for_status()
                data = response.json()

                date_str = data.get("estimated_completion_date")
                parsed_date = datetime.fromisoformat(date_str) if date_str else None

                return ForecastResult(
                    deal_id=deal_id,
                    estimated_completion_date=parsed_date,
                    confidence_score_pct=data.get("confidence_score_pct"),
                    status_code=data.get("status_code", "READY"),
                    calculated_on=now_time,
                    model_metadata=json.dumps(data.get("metadata", {})),
                )
        except httpx.TimeoutException as err:
            raise ForecastTimeoutError(
                f"Timeout calling forecast API for deal '{deal_id}': {err}"
            ) from err
        except httpx.RequestError as err:
            raise ForecastConnectionError(
                f"Connection error calling forecast API for deal '{deal_id}': {err}"
            ) from err
        except Exception as err:
            raise ForecastProviderError(
                f"Error calling forecast API for deal '{deal_id}': {err}"
            ) from err


# Alias for explicit class naming consistency
ForecastProviderConnector = ForecastConnector
