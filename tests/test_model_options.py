import pytest

from packages.model_options import thinking_options

MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
ENDPOINT = "https://integrate.api.nvidia.com/v1"


@pytest.mark.parametrize("mode,enabled", [("auto", False), ("disabled", False), ("enabled", True)])
def test_nvidia_thinking_is_controlled_only_for_supported_models(mode, enabled):
    assert thinking_options(MODEL, ENDPOINT, mode) == {
        "chat_template_kwargs": {"enable_thinking": enabled}
    }


@pytest.mark.parametrize(
    "model,url", [(MODEL, "https://openrouter.ai/api/v1"), ("other-model", ENDPOINT), (MODEL, None)]
)
def test_auto_does_not_send_vendor_options_to_other_endpoints(model, url):
    assert thinking_options(model, url) == {}
    with pytest.raises(ValueError):
        thinking_options(model, url, "disabled")


def test_unknown_thinking_mode_is_rejected():
    with pytest.raises(ValueError):
        thinking_options(MODEL, ENDPOINT, "low")
