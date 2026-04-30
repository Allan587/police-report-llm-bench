# Police Report LLM Benchmark

## Overview
This project benchmarks multiple large language models (LLMs) for generating structured police reports with minimal hallucinations.

The goal is to evaluate which models are most suitable before applying fine-tuning.

## Models Evaluated
- LLaMA 2
- Mistral 7B
- GPT-NeoX
- Gamma 4

## Evaluation Criteria
Models are evaluated based on:
- Factual consistency
- Structure adherence
- Hallucination rate
- Clarity and coherence

## Project Structure

project/
│── models/
│── data/
│── src/
│ ├── data_loader.py
│ ├── model_runner.py
│ ├── prompt_builder.py
│ ├── rubric_evaluator.py
│ └── run_evaluation.py
│ └── utils.py
│── main.py


## Installation
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

