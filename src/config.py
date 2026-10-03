import os

from dotenv import load_dotenv


load_dotenv()


PROVIDERS = {
    "gemini": {
        "adapter": "openai_compatible",
        "model": "gemini-3.6-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
    },
}

LLM_PROVIDER = "gemini"


def get_provider_config(provider: str = LLM_PROVIDER) -> dict:
    """Return the configuration for the selected LLM provider."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown LLM provider: {provider}")

    config = PROVIDERS[provider].copy()

    api_key_env = config["api_key_env"]
    api_key = os.getenv(api_key_env)

    if not api_key:
        raise RuntimeError(
            f"Missing API key. Set {api_key_env} in the environment or .env file."
        )

    config["api_key"] = api_key
    return config