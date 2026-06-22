import gc
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM


_loaded_models = {}


MODEL_GENERATION_CONFIGS = {
    "mistralai/Mistral-7B-Instruct-v0.3": {
        "temperature": 0.30,
        "top_p": 0.85,
        "repetition_penalty": 1.08,
    },
    "meta-llama/Llama-3.2-3B-Instruct": {
        "temperature": 0.25,
        "top_p": 0.80,
        "repetition_penalty": 1.08,
    },
    "google/gemma-2-2b-it": {
        "temperature": 0.20,
        "top_p": 0.80,
        "repetition_penalty": 1.05,
    },
}


def get_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def clear_gpu_memory() -> None:
    gc.collect()

    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def load_model(model_id: str):
    if model_id in _loaded_models:
        return _loaded_models[model_id]

    print(f"Cargando modelo: {model_id}")

    tokenizer = AutoTokenizer.from_pretrained(model_id)

    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map={"": "cuda"} if torch.cuda.is_available() else None,
        low_cpu_mem_usage=True,
    )

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model.eval()

    device = next(model.parameters()).device

    print(f"Modelo cargado en dispositivo: {device}")

    if torch.cuda.is_available():
        print(
            f"GPU: {torch.cuda.get_device_name(0)} | "
            f"VRAM usada: "
            f"{torch.cuda.memory_allocated() / 1024**3:.2f} GB"
        )

    _loaded_models[model_id] = {
        "tokenizer": tokenizer,
        "model": model,
    }

    return _loaded_models[model_id]


def unload_model(model_id: str) -> None:
    if model_id in _loaded_models:
        del _loaded_models[model_id]
        clear_gpu_memory()


def unload_all_models() -> None:
    _loaded_models.clear()
    clear_gpu_memory()


def build_model_input(tokenizer, prompt: str) -> str:
    if getattr(tokenizer, "chat_template", None):
        messages = [
            {
                "role": "user",
                "content": prompt,
            }
        ]

        return tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

    return prompt


def get_generation_config(
    model_id: str,
    temperature: float | None = None,
    top_p: float | None = None,
    repetition_penalty: float | None = None,
) -> dict:
    config = MODEL_GENERATION_CONFIGS.get(model_id, {
        "temperature": 0.25,
        "top_p": 0.85,
        "repetition_penalty": 1.1,
    }).copy()

    if temperature is not None:
        config["temperature"] = temperature

    if top_p is not None:
        config["top_p"] = top_p

    if repetition_penalty is not None:
        config["repetition_penalty"] = repetition_penalty

    return config


def clean_model_output(text: str) -> str:
    text = text.strip()

    markers = [
        "Narrativa:",
        "**Narrativa**",
        "### Narrativa",
    ]

    for marker in markers:
        index = text.find(marker)
        if index != -1:
            text = text[index:].strip()
            break

    text = text.replace("**Narrativa**", "Narrativa:")
    text = text.replace("**Cierre**", "Cierre:")

    return text.strip()


def run_model(
    model_id: str,
    prompt: str,
    max_new_tokens: int = 600,
    temperature: float | None = None,
    top_p: float | None = None,
    repetition_penalty: float | None = None,
) -> str:
    loaded = load_model(model_id)

    tokenizer = loaded["tokenizer"]
    model = loaded["model"]

    input_text = build_model_input(tokenizer, prompt)

    model_max_length = getattr(model.config, "max_position_embeddings", 1024)
    safe_input_length = max(128, model_max_length - max_new_tokens)

    inputs = tokenizer(
        input_text,
        return_tensors="pt",
        truncation=True,
        max_length=safe_input_length,
    )

    device = next(model.parameters()).device
    inputs = {key: value.to(device) for key, value in inputs.items()}

    config = get_generation_config(
        model_id=model_id,
        temperature=temperature,
        top_p=top_p,
        repetition_penalty=repetition_penalty,
    )

    do_sample = config["temperature"] > 0

    generation_kwargs = {
        "max_new_tokens": max_new_tokens,
        "do_sample": do_sample,
        "pad_token_id": tokenizer.pad_token_id,
        "eos_token_id": tokenizer.eos_token_id,
        "repetition_penalty": config["repetition_penalty"],
    }

    if do_sample:
        generation_kwargs["temperature"] = config["temperature"]
        generation_kwargs["top_p"] = config["top_p"]

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            **generation_kwargs,
        )

    input_length = inputs["input_ids"].shape[-1]
    generated_ids = output_ids[0][input_length:]

    generated_text = tokenizer.decode(
        generated_ids,
        skip_special_tokens=True,
    )

    return clean_model_output(generated_text)


def run_multiple_models(
    model_ids: list[str],
    prompt: str,
    max_new_tokens: int = 600,
    temperature: float | None = None,
    unload_after_each: bool = True,
) -> dict:
    results = {}

    for model_id in model_ids:
        print(f"Ejecutando modelo: {model_id}")

        try:
            response = run_model(
                model_id=model_id,
                prompt=prompt,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
            )

            results[model_id] = {
                "success": True,
                "response": response,
                "error": None,
            }

        except Exception as error:
            results[model_id] = {
                "success": False,
                "response": None,
                "error": str(error),
            }

        finally:
            if unload_after_each:
                unload_model(model_id)

    return results