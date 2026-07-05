import argparse
import json
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


SYSTEM_PROMPT = (
    "You are a coding-agent critic. Diagnose agent failures from task, trajectory, "
    "test output, and diff. Return only valid JSON. Separate diagnosis "
    "(failure_type, reason, evidence, confidence) from decision "
    "(next_action, target_file, allowed_tools, risk_level, abstain, suggestion)."
)


def read_jsonl(path):
    rows = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def prompt_from_text(tokenizer, text):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    if hasattr(tokenizer, "apply_chat_template") and tokenizer.chat_template:
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    return f"<|system|>\n{SYSTEM_PROMPT}\n<|user|>\n{text}\n<|assistant|>\n"


def load_prompt(args, tokenizer):
    if args.input:
        return prompt_from_text(tokenizer, args.input)
    if args.input_file:
        return prompt_from_text(tokenizer, Path(args.input_file).read_text(encoding="utf-8"))
    rows = read_jsonl(args.data)
    row = rows[args.index]
    text = row["instruction"].strip() + "\n\n" + row["input"].strip()
    return prompt_from_text(tokenizer, text)


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Evaluate a trained Critic LoRA adapter.")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-Coder-7B-Instruct")
    parser.add_argument("--adapter", default="outputs/qwen2.5-coder-critic-lora")
    parser.add_argument("--data", default="datasets/critic_sft.jsonl")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--input", default=None)
    parser.add_argument("--input-file", default=None)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    tokenizer = AutoTokenizer.from_pretrained(args.adapter, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        device_map="auto",
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(model, args.adapter)
    model.eval()

    prompt = load_prompt(args, tokenizer)
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=args.max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    generated = output[0][inputs["input_ids"].shape[-1] :]
    print(tokenizer.decode(generated, skip_special_tokens=True).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
