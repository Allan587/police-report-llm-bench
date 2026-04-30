import torch
from transformers import AutoTokenizer, AutoModelForCausalLM


_loaded_models = {}


def load_model(model_id: str):
    if model_id in _loaded_models:
        return _loaded_models[model_id]

    print(f"Cargando modelo: {model_id}")

    tokenizer = AutoTokenizer.from_pretrained(model_id)

    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map="auto"
    )

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    _loaded_models[model_id] = {
        "tokenizer": tokenizer,
        "model": model
    }

    return _loaded_models[model_id]


def run_model(
    model_id: str,
    prompt: str,
    max_new_tokens: int = 350,
    temperature: float = 0.2
) -> str:
    loaded = load_model(model_id)

    tokenizer = loaded["tokenizer"]
    model = loaded["model"]

    inputs = tokenizer(
        prompt,
        return_tensors="pt",
        truncation=True,
        max_length=2048
    )

    inputs = {k: v.to(model.device) for k, v in inputs.items()}

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            do_sample=temperature > 0,
            pad_token_id=tokenizer.eos_token_id
        )

    generated_text = tokenizer.decode(
        output_ids[0],
        skip_special_tokens=True
    )

    return clean_model_output(prompt, generated_text)


def clean_model_output(prompt: str, generated_text: str) -> str:
    if generated_text.startswith(prompt):
        return generated_text[len(prompt):].strip()

    return generated_text.strip()