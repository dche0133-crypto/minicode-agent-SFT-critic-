# Cloud Critic LoRA Training

Minimal files needed on the cloud machine:

```text
datasets/critic_sft.jsonl
cloud/requirements-train.txt
cloud/train_critic_lora.py
cloud/eval_critic_lora.py
```

Install dependencies:

```bash
pip install -r cloud/requirements-train.txt
```

Train a Qwen2.5-Coder Critic LoRA adapter:

```bash
python cloud/train_critic_lora.py \
  --model Qwen/Qwen2.5-Coder-7B-Instruct \
  --data datasets/critic_sft.jsonl \
  --out outputs/qwen2.5-coder-critic-lora
```

Evaluate the adapter on one SFT row:

```bash
python cloud/eval_critic_lora.py \
  --base-model Qwen/Qwen2.5-Coder-7B-Instruct \
  --adapter outputs/qwen2.5-coder-critic-lora \
  --data datasets/critic_sft.jsonl \
  --index 0
```

The adapter is trained to output critic JSON, not to directly write code.
