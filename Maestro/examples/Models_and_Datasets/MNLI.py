import dataclasses
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, Optional
import datasets

import numpy as np

from transformers import (
    AutoConfig,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    EvalPrediction,
    GlueDataset,
)
from transformers import GlueDataTrainingArguments as DataTrainingArguments
from transformers import (
    HfArgumentParser,
    Trainer,
    TrainingArguments,
    glue_compute_metrics,
    glue_output_modes,
    glue_tasks_num_labels,
    set_seed,
)

model_name_or_path = "bert-base-uncased"
logging.basicConfig(level=logging.INFO)

data_args = DataTrainingArguments(task_name="mnli", data_dir="./glue_data/MNLI")
training_args = TrainingArguments(
    output_dir="models_temp/model_name",
    overwrite_output_dir=True,
    do_train=True,
    do_eval=True,
    per_gpu_train_batch_size=32,
    per_gpu_eval_batch_size=128,
    num_train_epochs=1,
    logging_steps=500,
    logging_first_step=True,
    save_steps=1000,
    evaluate_during_training=True,
)
num_labels = glue_tasks_num_labels[data_args.task_name]
print(num_labels)
config = AutoConfig.from_pretrained(
    model_name_or_path, num_labels=num_labels, finetuning_task=data_args.task_name,
)
tokenizer = AutoTokenizer.from_pretrained(model_name_or_path,)
model = AutoModelForSequenceClassification.from_pretrained(
    model_name_or_path, config=config,
)
# Get datasets
# train_dataset = GlueDataset(data_args, tokenizer=tokenizer, limit_length=100_000)
# eval_dataset = GlueDataset(data_args, tokenizer=tokenizer, mode="dev")
train_dataset = datasets.load_dataset("glue", "mnli", split="train")
eval_dataset = datasets.load_dataset("glue", "mnli", split="validation_matched")
# another = datasets.load_dataset("imdb", split="train")
train_dataset = train_dataset.map(
    lambda e: tokenizer(
        e["premise"],
        e["hypothesis"],
        max_length=128,
        truncation=True,
        padding="max_length",
    ),
    batched=True,
)
train_dataset.set_format(
    type="torch", columns=["input_ids", "token_type_ids", "attention_mask", "label"]
)
eval_dataset = eval_dataset.map(
    lambda e: tokenizer(
        e["premise"],
        e["hypothesis"],
        max_length=128,
        truncation=True,
        padding="max_length",
    ),
    batched=True,
)
eval_dataset.set_format(
    type="torch", columns=["input_ids", "token_type_ids", "attention_mask", "label"]
)
print(train_dataset[0])
# print(another[0])


def compute_metrics(p: EvalPrediction) -> Dict:
    preds = np.argmax(p.predictions, axis=1)
    return glue_compute_metrics(data_args.task_name, preds, p.label_ids)


trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset,
    eval_dataset=eval_dataset,
    compute_metrics=compute_metrics,
)
trainer.train()
