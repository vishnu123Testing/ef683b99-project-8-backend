"""Lead Generation domain connector adapter.

Provides fault isolation, timeout resilience, and metric retrieval for Lead Generation KPIs
(lead volume, conversion rates, and acquisition channel breakdowns).
"""

import time
from typing import List, Optional

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
from src.services.dashboard.domain_ports import (
    BreakdownItem,
    DomainConnectorPort,
    DomainMetricsResult,
    DomainStatus,
    MetricResult,
    MetricStatus,
    MetricUnit,
)


class LeadGenConnector(DomainConnectorPort):
    """Domain connector adapter for the Lead Generation operational domain."""

    ALLOWED_TIMEFRAMES = {"7d", "30d", "90d"}

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        simulate_failure: bool = False,
        simulate_timeout: bool = False,
        simulate_zero: bool = False,
    ) -> None:
        """Initialize the Lead Generation domain connector adapter.

        Args:
            api_url: Optional upstream API endpoint URL for lead generation service.
            api_key: Optional authorization key for upstream service.
            timeout: Execution timeout limit in seconds (defaults to settings configuration).
            retries: Retry attempts for transient upstream failures.
            simulate_failure: Development/testing toggle to trigger failure isolation.
            simulate_timeout: Development/testing toggle to trigger timeout handling.
            simulate_zero: Development/testing toggle to return zero metrics.
        """
        self.api_url = api_url
        self.api_key = api_key
        self.timeout = timeout if timeout is not None else float(settings.dashboard_connector_timeout)
        self.retries = retries if retries is not None else settings.dashboard_connector_retries
        self.simulate_failure = simulate_failure
        self.simulate_timeout = simulate_timeout
        self.simulate_zero = simulate_zero

    @property
    def domain_id(self) -> str:
        """Return the unique domain identifier string for Lead Generation."""
        return "lead_gen"

    def fetch_metrics(self, timeframe: str = "30d") -> DomainMetricsResult:
        """Retrieve operational metrics and distribution items for Lead Generation.

        Enforces fault isolation so upstream errors or timeouts return an UNAVAILABLE
        DomainMetricsResult rather than raising exceptions to the caller.
        """
        try:
            if timeframe not in self.ALLOWED_TIMEFRAMES:
                raise ValueError(
                    f"Invalid timeframe '{timeframe}'. Allowed timeframes: {sorted(self.ALLOWED_TIMEFRAMES)}"
                )

            if self.simulate_timeout:
                raise TimeoutError(f"Lead Gen connector timed out after {self.timeout}s")

            if self.simulate_failure:
                raise RuntimeError("Lead Gen upstream service endpoint returned HTTP 503 Service Unavailable")

            if self.api_url:
                return self._fetch_from_external_api(timeframe)

            return self._fetch_simulated_metrics(timeframe)

        except Exception as exc:
            return self._build_unavailable_result(str(exc))

    def _fetch_from_external_api(self, timeframe: str) -> DomainMetricsResult:
        """Fetch metrics from external upstream HTTP API with retries and timeout boundaries."""
        last_exception: Optional[Exception] = None
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        for attempt in range(1 + self.retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.get(
                        f"{self.api_url.rstrip('/')}/metrics",
                        params={"timeframe": timeframe},
                        headers=headers,
                    )
                    response.raise_for_status()
                    data = response.json()
                    return self._parse_api_response(data)
            except (httpx.TimeoutException, TimeoutError) as exc:
                last_exception = TimeoutError(f"Lead Gen connector timed out after {self.timeout}s")
            except Exception as exc:
                last_exception = exc

            if attempt < self.retries:
                time.sleep(0.01 * (attempt + 1))

        raise last_exception or RuntimeError("Failed to fetch lead gen metrics from external API")

    def _parse_api_response(self, data: dict) -> DomainMetricsResult:
        """Parse raw JSON dict response into DomainMetricsResult DTO."""
        metrics_list: List[MetricResult] = []
        for m in data.get("metrics", []):
            raw_val = m.get("raw_value")
            baseline_val = m.get("baseline_value")
            key = m.get("metric_key", "")
            unit_str = str(m.get("unit", "count")).lower()
            
            unit = MetricUnit.COUNT
            for member in MetricUnit:
                if member.value == unit_str:
                    unit = member
                    break

            metric = self._build_metric_result(
                key=key,
                display_name=m.get("display_name", key.replace("_", " ").title()),
                raw_val=raw_val,
                baseline_val=baseline_val,
                unit=unit,
            )
            metrics_list.append(metric)

        breakdown = [
            BreakdownItem(label=b.get("label", ""), value=float(b.get("value", 0.0)))
            for b in data.get("breakdown_items", [])
        ]

        return DomainMetricsResult(
            domain=self.domain_id,
            status=DomainStatus.COMPLETE,
            metrics=metrics_list,
            breakdown_items=breakdown,
        )

    def _fetch_simulated_metrics(self, timeframe: str) -> DomainMetricsResult:
        """Construct operational metrics using baseline dataset for Lead Generation."""
        if self.simulate_zero:
            volume_metric = self._build_metric_result(
                key="lead_volume",
                display_name="Lead Volume",
                raw_val=0.0,
                baseline_val=1200.0,
                unit=MetricUnit.COUNT,
            )
            conversion_metric = self._build_metric_result(
                key="conversion_rate",
                display_name="Conversion Rate",
                raw_val=0.0,
                baseline_val=3.5,
                unit=MetricUnit.PERCENTAGE,
            )
            breakdown = [
                BreakdownItem(label="Lead Source: Organic", value=0.0),
                BreakdownItem(label="Lead Source: Paid Search", value=0.0),
                BreakdownItem(label="Lead Source: Referrals", value=0.0),
                BreakdownItem(label="Lead Source: Social", value=0.0),
            ]
            return DomainMetricsResult(
                domain=self.domain_id,
                status=DomainStatus.COMPLETE,
                metrics=[volume_metric, conversion_metric],
                breakdown_items=breakdown,
            )

        if timeframe == "7d":
            raw_vol, base_vol = 340.0, 300.0
            raw_conv, base_conv = 4.5, 3.8
            channel_dist = {
                "Lead Source: Organic": 136.0,
                "Lead Source: Paid Search": 102.0,
                "Lead Source: Referrals": 68.0,
                "Lead Source: Social": 34.0,
            }
        elif timeframe == "90d":
            raw_vol, base_vol = 4350.0, 3800.0
            raw_conv, base_conv = 4.0, 3.6
            channel_dist = {
                "Lead Source: Organic": 1740.0,
                "Lead Source: Paid Search": 1305.0,
                "Lead Source: Referrals": 870.0,
                "Lead Source: Social": 435.0,
            }
        else:  # "30d"
            raw_vol, base_vol = 1450.0, 1200.0
            raw_conv, base_conv = 4.2, 3.5
            channel_dist = {
                "Lead Source: Organic": 580.0,
                "Lead Source: Paid Search": 435.0,
                "Lead Source: Referrals": 290.0,
                "Lead Source: Social": 145.0,
            }

        volume_metric = self._build_metric_result(
            key="lead_volume",
            display_name="Lead Volume",
            raw_val=raw_vol,
            baseline_val=base_vol,
            unit=MetricUnit.COUNT,
        )
        conversion_metric = self._build_metric_result(
            key="conversion_rate",
            display_name="Conversion Rate",
            raw_val=raw_conv,
            baseline_val=base_conv,
            unit=MetricUnit.PERCENTAGE,
        )

        breakdown = [
            BreakdownItem(label=label, value=val)
            for label, val in channel_dist.items()
        ]

        return DomainMetricsResult(
            domain=self.domain_id,
            status=DomainStatus.COMPLETE,
            metrics=[volume_metric, conversion_metric],
            breakdown_items=breakdown,
        )

    def _build_metric_result(
        self,
        key: str,
        display_name: str,
        raw_val: Optional[float],
        baseline_val: Optional[float],
        unit: MetricUnit,
    ) -> MetricResult:
        """Helper method to format raw numerical values and calculate percentage changes."""
        if raw_val is None:
            return MetricResult(
                domain=self.domain_id,
                metric_key=key,
                display_name=display_name,
                raw_value=None,
                baseline_value=baseline_val,
                formatted_value="Unavailable",
                percentage_change=None,
                status=MetricStatus.UNAVAILABLE,
                unit=unit,
            )

        if raw_val == 0.0:
            formatted = "0" if unit == MetricUnit.COUNT else "0.0%"
            pct_change: Optional[float] = None
            if baseline_val is not None and baseline_val > 0:
                pct_change = round(((0.0 - baseline_val) / baseline_val) * 100.0, 1)
            elif baseline_val == 0.0:
                pct_change = 0.0

            return MetricResult(
                domain=self.domain_id,
                metric_key=key,
                display_name=display_name,
                raw_value=0.0,
                baseline_value=baseline_val,
                formatted_value=formatted,
                percentage_change=pct_change,
                status=MetricStatus.ZERO,
                unit=unit,
            )

        if unit == MetricUnit.COUNT:
            formatted = f"{int(raw_val):,}"
        elif unit == MetricUnit.PERCENTAGE:
            formatted = f"{raw_val:.1f}%"
        elif unit == MetricUnit.CURRENCY:
            formatted = f"${raw_val:,.2f}"
        else:
            formatted = str(raw_val)

        pct_change = None
        if baseline_val is not None and baseline_val > 0:
            pct_change = round(((raw_val - baseline_val) / baseline_val) * 100.0, 1)

        return MetricResult(
            domain=self.domain_id,
            metric_key=key,
            display_name=display_name,
            raw_value=raw_val,
            baseline_value=baseline_val,
            formatted_value=formatted,
            percentage_change=pct_change,
            status=MetricStatus.VALID,
            unit=unit,
        )

    def _build_unavailable_result(self, error_message: str) -> DomainMetricsResult:
        """Construct DomainMetricsResult representing connector fault / outage."""
        vol_unavailable = MetricResult(
            domain=self.domain_id,
            metric_key="lead_volume",
            display_name="Lead Volume",
            raw_value=None,
            baseline_value=None,
            formatted_value="Unavailable",
            percentage_change=None,
            status=MetricStatus.UNAVAILABLE,
            unit=MetricUnit.COUNT,
            error_detail=error_message,
        )
        conv_unavailable = MetricResult(
            domain=self.domain_id,
            metric_key="conversion_rate",
            display_name="Conversion Rate",
            raw_value=None,
            baseline_value=None,
            formatted_value="Unavailable",
            percentage_change=None,
            status=MetricStatus.UNAVAILABLE,
            unit=MetricUnit.PERCENTAGE,
            error_detail=error_message,
        )

        return DomainMetricsResult(
            domain=self.domain_id,
            status=DomainStatus.UNAVAILABLE,
            metrics=[vol_unavailable, conv_unavailable],
            breakdown_items=[],
            error_detail=error_message,
        )
