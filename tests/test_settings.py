from clarifytrial.settings import load_settings


def test_default_settings_are_explicit() -> None:
    settings = load_settings("configs/default.toml")

    assert settings.model.provider == "unconfigured"
    assert settings.model.model_alias == "claude-sonnet-5"
    assert settings.episode.max_external_actions == 3
    assert settings.episode.max_selective_reviews == 1
