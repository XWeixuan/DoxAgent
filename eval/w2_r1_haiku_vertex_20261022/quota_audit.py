"""Read the three Haiku global quota metrics. Never changes quotas or prints a token."""

import json
from pathlib import Path
from urllib.parse import quote

import google.auth
from google.auth.transport.requests import AuthorizedSession


def main():
    credentials, project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    metrics = [
        "global_online_prediction_requests_per_base_model",
        "global_online_prediction_input_tokens_per_base_model",
        "global_online_prediction_output_tokens_per_base_model",
    ]
    records = []
    for metric in metrics:
        encoded = quote("aiplatform.googleapis.com/" + metric, safe="")
        url = (
            f"https://serviceusage.googleapis.com/v1beta1/projects/{project}/services/"
            f"aiplatform.googleapis.com/consumerQuotaMetrics/{encoded}"
        )
        response = session.get(url, params={"view": "FULL"}, timeout=30)
        try:
            payload = response.json()
        except ValueError:
            payload = {"unexpected_response": response.text[:500]}
        records.append(dict(metric=metric, http_status=response.status_code, response=payload))
    Path(__file__).with_name("quota_audit.json").write_text(json.dumps(records, indent=2))
    for record in records:
        payload = record["response"]
        relevant = []
        for limit in payload.get("consumerQuotaLimits", []):
            for bucket in limit.get("quotaBuckets", []):
                if bucket.get("dimensions", {}).get("base_model") == "anthropic-claude-haiku":
                    relevant.append(dict(unit=limit.get("unit"), bucket=bucket))
        print(
            json.dumps(
                dict(
                    metric=record["metric"],
                    http_status=record["http_status"],
                    haiku_buckets=relevant,
                    error=payload.get("error"),
                )
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
