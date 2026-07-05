import argparse
import inspect
import json
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainingArguments
from trl import SFTTrainer

try:
    from trl import SFTConfig
except ImportError:
    SFTConfig = None


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


def format_example(row, tokenizer):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": row["instruction"].strip() + "\n\n" + row["input"].strip(),
        },
        {
            "role": "assistant",
            "content": json.dumps(row["output"], ensure_ascii=False),
        },
    ]
    if hasattr(tokenizer, "apply_chat_template") and tokenizer.chat_template:
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
    return (
        f"<|system|>\n{messages[0]['content']}\n"
        f"<|user|>\n{messages[1]['content']}\n"
        f"<|assistant|>\n{messages[2]['content']}"
    )


def build_dataset(path, tokenizer):
    rows = read_jsonl(path)
    texts = [format_example(row, tokenizer) for row in rows]
    return Dataset.from_dict({"text": texts})


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Train a Qwen Critic LoRA adapter.")
    parser.add_argument("--model", default="Qwen/Qwen2.5-Coder-7B-Instruct")
    parser.add_argument("--data", default="datasets/critic_sft.jsonl")
    parser.add_argument("--out", default="outputs/qwen2.5-coder-critic-lora")
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--no-4bit", action="store_true", help="Disable 4-bit QLoRA loading.")
    return parser


def build_training_args(args):
    return {
        "output_dir": args.out,
        "num_train_epochs": args.epochs,
        "per_device_train_batch_size": args.batch_size,
        "gradient_accumulation_steps": args.grad_accum,
        "learning_rate": args.lr,
        "logging_steps": 10,
        "save_strategy": "epoch",
        "bf16": True,
        "optim": "paged_adamw_8bit" if not args.no_4bit else "adamw_torch",
        "report_to": "none",
    }


def build_sft_args(args):
    training_args = build_training_args(args)
    if SFTConfig is None:
        return TrainingArguments(**training_args)

    signature = inspect.signature(SFTConfig.__init__)
    sft_args = dict(training_args)
    if "dataset_text_field" in signature.parameters:
        sft_args["dataset_text_field"] = "text"
    if "max_seq_length" in signature.parameters:
        sft_args["max_seq_length"] = args.max_seq_length
    if "max_length" in signature.parameters:
        sft_args["max_length"] = args.max_seq_length
    return SFTConfig(**sft_args)


def build_trainer(model, tokenizer, dataset, lora_config, args):
    trainer_signature = inspect.signature(SFTTrainer.__init__)
    trainer_args = {
        "model": model,
        "train_dataset": dataset,
        "peft_config": lora_config,
        "args": build_sft_args(args),
    }

    if "tokenizer" in trainer_signature.parameters:
        trainer_args["tokenizer"] = tokenizer
        trainer_args["dataset_text_field"] = "text"
        trainer_args["max_seq_length"] = args.max_seq_length
    elif "processing_class" in trainer_signature.parameters:
        trainer_args["processing_class"] = tokenizer

    return SFTTrainer(**trainer_args)


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quantization_config = None
    if not args.no_4bit:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        quantization_config=quantization_config,
        device_map="auto",
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
    )
    if not args.no_4bit:
        model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )

    dataset = build_dataset(args.data, tokenizer)
    trainer = build_trainer(model, tokenizer, dataset, lora_config, args)
    trainer.train()
    trainer.save_model(args.out)
    tokenizer.save_pretrained(args.out)
    print(f"saved LoRA adapter to {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())
