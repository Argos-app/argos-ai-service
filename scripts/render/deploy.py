"""Deploy a pinned source commit through Render's API, without uploading images."""

import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://api.render.com/v1"
PENDING = {"created", "queued", "build_in_progress", "pre_deploy_in_progress", "update_in_progress"}


def request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(
        f"{API}/{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    # Only reads are retried. Retrying an ambiguous POST could create duplicate deploys.
    attempts = 3 if method == "GET" else 1
    for attempt in range(attempts):
        try:
            with urlopen(req, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise RuntimeError(f"Render {method} failed: HTTP {error.code}.") from None
        except URLError, TimeoutError:
            if attempt == attempts - 1:
                raise RuntimeError(f"Render {method} failed: network error or timeout.") from None
        time.sleep(5 * (attempt + 1))
    raise AssertionError("Unreachable")


def validate_service(service: dict, branch: str) -> None:
    details = service.get("serviceDetails", {})
    if service.get("branch") != branch:
        raise ValueError("Render service branch does not match the deployment branch.")
    if details.get("runtime", details.get("env")) != "python":
        raise ValueError("Render service must use the native Python runtime and Git source.")
    if service.get("rootDir", "") not in {"", "."}:
        raise ValueError("Render Root Directory must be empty to include packages/.")
    if service.get("autoDeployTrigger", "off") != "off" or service.get("autoDeploy", "no") != "no":
        raise ValueError("Disable Render Auto-Deploy; GitHub Actions owns deployments.")


def deploy(service_id: str, token: str, sha: str, branch: str, timeout: int = 1200) -> str:
    if branch not in {"develop", "main"} or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a develop/main branch and a full Git commit SHA.")
    if not token or not re.fullmatch(r"srv-[a-zA-Z0-9]+", service_id):
        raise ValueError("Set RENDER_API_KEY and the RENDER_SERVICE_ID_* environment secret.")
    path = f"services/{service_id}"
    validate_service(request("GET", path, token), branch)
    result = request("POST", f"{path}/deploys", token, {"commitId": sha})
    deploy_id = result.get("id", "")
    if not re.fullmatch(r"dep-[a-zA-Z0-9]+", deploy_id):
        raise RuntimeError("Render did not return a valid deployment ID.")
    url = f"https://dashboard.render.com/web/{service_id}/deploys/{deploy_id}"
    print(f"Deployment: {url}", flush=True)
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        result = request("GET", f"{path}/deploys/{deploy_id}", token)
        status = result.get("status")
        if status != previous:
            print(f"Render status: {status}", flush=True)
            previous = status
        if status == "live":
            if result.get("commit", {}).get("id") != sha:
                raise RuntimeError("Live deployment commit does not match the requested SHA.")
            return url
        if status not in PENDING:
            raise RuntimeError(f"Deployment did not become live: {status}. See {url}")
        time.sleep(15)
    raise RuntimeError(f"Timed out waiting for deployment; check Render before retrying: {url}")


def main() -> None:
    try:
        url = deploy(
            os.environ.get("RENDER_SERVICE_ID", ""),
            os.environ.get("RENDER_API_KEY", ""),
            os.environ["DEPLOY_SHA"],
            os.environ["DEPLOY_BRANCH"],
        )
    except (ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(f"Deployed `{os.environ['DEPLOY_SHA']}`: [Render deployment]({url})\n")


if __name__ == "__main__":
    main()
