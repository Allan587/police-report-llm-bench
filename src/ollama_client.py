import requests


OLLAMA_URL = "http://localhost:11434/api/generate"


def generate_with_ollama(model_name: str, prompt: str, temperature: float = 0.2, max_tokens: int = 350):
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens
        }
    }

    response = requests.post(OLLAMA_URL, json=payload, timeout=300)
    response.raise_for_status()

    data = response.json()

    return data.get("response", "")