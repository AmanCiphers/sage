from app.inference.providers.config import ProviderConfig


def test_provider_config():
    config = ProviderConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
        timeout=30.0,
    )

    assert config.api_key == "test-key"
    assert config.base_url == "https://example.com/v1"
    assert config.timeout == 30.0
