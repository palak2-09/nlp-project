import os
from app.core.config import get_settings, PROJECT_ROOT

settings = get_settings()

print("PROJECT ROOT:", PROJECT_ROOT)
print("ENV PROVIDER:", os.getenv("PAPERGRAPH_LLM_PROVIDER"))
print("CONFIG PROVIDER:", settings.llm_provider)
print("CONFIG MODEL:", settings.llm_model)
print("API KEY PRESENT:", bool(settings.llm_api_key))
print("API KEY LENGTH:", len(settings.llm_api_key))