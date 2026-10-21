"""Check ADC without exposing its token, then start the serial replay."""

import json
from pathlib import Path

import google.auth
from google.auth.transport.requests import Request

ROOT = Path(__file__).parent


def main():
    try:
        credentials, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        assert project == "gen-lang-client-0328762493"
        credentials.refresh(Request())
        assert credentials.valid
        record = dict(
            adc_valid=True,
            project=project,
            credential_type=type(credentials).__name__,
            expiry=credentials.expiry.isoformat() if credentials.expiry else None,
        )
    except Exception as exc:
        record = dict(adc_valid=False, error_type=type(exc).__name__)
        (ROOT / "adc_preflight.json").write_text(json.dumps(record, indent=2))
        print(json.dumps(record), flush=True)
        raise SystemExit(2) from None
    (ROOT / "adc_preflight.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
    from run_eval import main as replay

    replay()


if __name__ == "__main__":
    main()
