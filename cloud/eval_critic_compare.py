import argparse
import gc
import json
import re
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig


SYSTEM_PROMPT = (
    "You are a coding-agent critic. Diagnose agent failures from task, trajectory, "
    "test output, and diff. Return only valid JSON. Separate diagnosis "
    "(failure_type, reason, evidence, confidence) from decision "
    "(next_action, target_file, allowed_tools, risk_level, abstain, suggestion)."
)


def read_jsonl(path, limit=None):
    rows = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                rows.append(json.loads(line))
            if limit and len(rows) >= limit:
                break
    return rows


def build_prompt(tokenizer, row):
    text = row["instruction"].strip() + "\n\n" + row["input"].strip()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    if hasattr(tokenizer, "apply_chat_template") and tokenizer.chat_template:
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    return f"<|system|>\n{SYSTEM_PROMPT}\n<|user|>\n{text}\n<|assistant|>\n"


def extract_json(text):
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def normalize_fields(parsed):
    if not parsed:
        return {}
    diagnosis = parsed.get("diagnosis", {})
    decision = parsed.get("decision", {})
    return {
        "failure_type": parsed.get("failure_type", diagnosis.get("failure_type")),
        "next_action": parsed.get("next_action", decision.get("next_action")),
        "target_file": parsed.get("target_file", decision.get("target_file")),
    }


def load_model(base_model, adapter=None, use_4bit=True):
    tokenizer_path = adapter or base_model
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quantization_config = None
    if use_4bit:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        device_map="auto",
        torch_dtype=torch.bfloat16,
        quantization_config=quantization_config,
        trust_remote_code=True,
    )
    if adapter:
        model = PeftModel.from_pretrained(model, adapter)
    model.eval()
    return tokenizer, model


def evaluate(name, rows, base_model, adapter, max_new_tokens, use_4bit):
    tokenizer, model = load_model(base_model, adapter=adapter, use_4bit=use_4bit)
    records = []

    for index, row in enumerate(rows):
        expected = row.get("output", {})
        prompt = build_prompt(tokenizer, row)
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        with torch.no_grad():
            output = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        generated = output[0][inputs["input_ids"].shape[-1] :]
        raw = tokenizer.decode(generated, skip_special_tokens=True).strip()
        parsed = extract_json(raw)
        normalized = normalize_fields(parsed)

        records.append(
            {
                "index": index,
                "expected": expected,
                "raw": raw,
                "parsed": parsed,
                "json_valid": parsed is not None,
                "failure_type_ok": bool(parsed)
                and normalized.get("failure_type") == expected.get("failure_type"),
                "next_action_ok": bool(parsed)
                and normalized.get("next_action") == expected.get("next_action"),
                "target_file_ok": bool(parsed)
                and normalized.get("target_file") == expected.get("target_file"),
            }
        )

    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return {"name": name, "summary": summarize(records), "records": records}


def rate(records, key):
    if not records:
        return 0.0
    return sum(1 for record in records if record[key]) / len(records)


def summarize(records):
    return {
        "total": len(records),
        "json_valid_rate": rate(records, "json_valid"),
        "failure_type_acc": rate(records, "failure_type_ok"),
        "next_action_acc": rate(records, "next_action_ok"),
        "target_file_acc": rate(records, "target_file_ok"),
    }


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Compare base critic and LoRA critic on SFT labels.")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-Coder-7B-Instruct")
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--data", default="datasets/critic_sft.jsonl")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--out", default="critic_eval_compare.json")
    parser.add_argument("--no-4bit", action="store_true")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    rows = read_jsonl(args.data, limit=args.limit)
    use_4bit = not args.no_4bit

    results = {
        "data": str(args.data),
        "limit": args.limit,
        "base_model": args.base_model,
        "adapter": args.adapter,
        "base": evaluate("base", rows, args.base_model, None, args.max_new_tokens, use_4bit),
        "lora": evaluate("lora", rows, args.base_model, args.adapter, args.max_new_tokens, use_4bit),
    }

    Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"base": results["base"]["summary"], "lora": results["lora"]["summary"]}, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
