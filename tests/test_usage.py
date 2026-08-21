from clarifytrial.llm.base import ModelUsage
from clarifytrial.usage import summarize_usage


def test_sonnet_cost_uses_reported_input_output_and_cache_tokens() -> None:
    summary = summarize_usage(
        [
            ModelUsage(
                model_id="claude-sonnet-5",
                input_tokens=1_000_000,
                output_tokens=100_000,
                cache_creation_input_tokens=10_000,
                cache_read_input_tokens=20_000,
            )
        ]
    )

    assert summary.input_cost_usd == 2.0
    assert summary.output_cost_usd == 1.0
    assert summary.cache_write_5m_cost_usd == 0.025
    assert summary.cache_read_cost_usd == 0.004
    assert summary.total_cost_usd == 3.029
