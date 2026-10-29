"""Publica um commit fixado pela API do Render, sem enviar imagens."""

import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://api.render.com/v1"
PENDING = {"created", "queued", "build_in_progress", "pre_deploy_in_progress", "update_in_progress"}


def request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    """Somente leituras são repetidas. Repetir um POST ambíguo pode criar deploys duplicados."""
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

    attempts = 3 if method == "GET" else 1
    for attempt in range(attempts):
        try:
            with urlopen(req, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise RuntimeError(
                    f"Falha no Render durante {method}: HTTP {error.code}."
                ) from None
        except URLError, TimeoutError:
            if attempt == attempts - 1:
                raise RuntimeError(
                    f"Falha no Render durante {method}: erro de rede ou tempo limite."
                ) from None
        time.sleep(5 * (attempt + 1))
    raise AssertionError("Trecho inalcançável")


def validate_service(service: dict, branch: str) -> None:
    details = service.get("serviceDetails", {})
    if service.get("branch") != branch:
        raise ValueError("A branch do serviço no Render não corresponde à branch do deploy.")
    if details.get("runtime", details.get("env")) != "python":
        raise ValueError(
            "O serviço do Render deve usar o runtime nativo de Python e uma origem Git."
        )
    if service.get("rootDir", "") not in {"", "."}:
        raise ValueError("O diretório raiz do Render deve estar vazio para incluir packages/.")
    if service.get("autoDeployTrigger", "off") != "off" or service.get("autoDeploy", "no") != "no":
        raise ValueError(
            "Desative a implantação automática do Render; "
            "o GitHub Actions controla as implantações."
        )


def deploy(service_id: str, token: str, sha: str, branch: str, timeout: int = 1200) -> str:
    if branch not in {"develop", "main"} or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Informe uma branch develop/main e o SHA completo do commit Git.")
    if not token or not re.fullmatch(r"srv-[a-zA-Z0-9]+", service_id):
        raise ValueError("Configure a secret de ambiente RENDER_API_KEY e RENDER_SERVICE_ID_*.")
    path = f"services/{service_id}"
    validate_service(request("GET", path, token), branch)
    result = request("POST", f"{path}/deploys", token, {"commitId": sha})
    deploy_id = result.get("id", "")
    if not re.fullmatch(r"dep-[a-zA-Z0-9]+", deploy_id):
        raise RuntimeError("O Render não retornou um ID de deploy válido.")
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
                raise RuntimeError(
                    "O commit da implantação ativa não corresponde ao SHA solicitado."
                )
            return url
        if status not in PENDING:
            raise RuntimeError(f"A implantação não ficou ativa: {status}. Consulte {url}")
        time.sleep(15)
    raise RuntimeError(
        "Tempo limite aguardando a implantação; "
        f"verifique o Render antes de tentar novamente: {url}"
    )


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
