"""Provider options applied only to documented compatible model endpoints."""

from urllib.parse import urlsplit

_NVIDIA_THINKING_MODELS = frozenset(
    {"nvidia/nemotron-3.5-super-vl-120b-a12b", "nvidia/nemotron-3-super-120b-a12b"}
)


def thinking_options(model: str, endpoint: str | None, mode: str = "auto") -> dict:
    """Auto disables optional NVIDIA thinking; other providers receive no extra fields.

    https://docs.nvidia.com/nim/large-language-models/latest/get-started/advanced/
    get-started-nemotron-3.5-super-vl.html
    """
    if mode not in {"auto", "enabled", "disabled"}:
        raise ValueError("thinking mode must be auto, enabled or disabled")
    compatible = (
        urlsplit(endpoint or "").hostname == "integrate.api.nvidia.com"
        and model in _NVIDIA_THINKING_MODELS
    )
    if not compatible:
        if mode != "auto":
            raise ValueError("explicit thinking mode requires a supported NVIDIA model endpoint")
        return {}
    return {"chat_template_kwargs": {"enable_thinking": mode == "enabled"}}
