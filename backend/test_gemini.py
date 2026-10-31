import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

api_key = os.getenv("PAPERGRAPH_LLM_API_KEY")

if not api_key:
    print("API KEY NOT FOUND")
    exit()

try:
    print("Connecting to Gemini API...", flush=True)

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=30000
        )
    )

    print("Sending test request...", flush=True)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="Reply with exactly: GEMINI WORKING"
    )

    print("\nSUCCESS!")
    print(response.text)

    client.close()

except Exception as e:
    print("\nFAILED!")
    print(type(e).__name__)
    print(str(e))