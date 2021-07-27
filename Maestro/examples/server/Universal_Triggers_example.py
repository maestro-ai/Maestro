from copy import deepcopy
from typing import List, Iterator, Dict, Tuple, Any, Type
import numpy as np
import heapq
import requests
import os
from flask import jsonify
import json
from operator import itemgetter
import pickle

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from transformers.data.data_collator import default_data_collator
from torch.utils.data.sampler import BatchSampler, RandomSampler
from torch.utils.data import DataLoader

from Maestro.models import build_model
from Maestro.utils import move_to_device, get_embedding
from Maestro.data import get_dataset, process_data
from Maestro.pipeline import (
    AutoPipelineForNLP,
    Pipeline,
    Scenario,
    Attacker,
    model_wrapper,
)
from Maestro.data.HuggingFaceDataset import make_text_dataloader, HuggingFaceDataset

from transformers import (
    Trainer,
    TrainingArguments,
    EvalPrediction,
    glue_compute_metrics,
)

# from allennlp.data.dataset_readers.stanford_sentiment_tree_bank import (
#     StanfordSentimentTreeBankDatasetReader,
# )

# from allennlp.models import Model
# from allennlp.data.vocabulary import Vocabulary
# from allennlp.data.token_indexers import SingleIdTokenIndexer
# from allennlp.training.trainer import Trainer, GradientDescentTrainer
# from allennlp.training.metrics import CategoricalAccuracy
# from allennlp.data.samplers import BucketBatchSampler
# from allennlp.data import PyTorchDataLoader as AllenDataLoader
# from allennlp.modules.token_embedders.embedding import _read_pretrained_embeddings_file
# from allennlp.modules.token_embedders import Embedding
# from allennlp.modules.text_field_embedders import BasicTextFieldEmbedder
# from allennlp.modules.seq2vec_encoders import PytorchSeq2VecWrapper
# from allennlp.nn.util import get_text_field_mask

# from allennlp.nn.util import move_to_device
# from allennlp.common.util import lazy_groups_of

# from torchvision import datasets, transforms
import sys


def get_accuracy(
    url: str, dev_data, trigger_token_ids, triggers=False, batch=False,
) -> None:
    # model_wrapper.model.get_metrics(reset=True)
    # model_wrapper.model.eval()  # model should be in eval() already, but just in case

    # model_wrapper.model.to(1)
    if batch:
        if triggers:
            print_string = ""
            for idx in trigger_token_ids:
                print_string = (
                    print_string + str(convert_ids_to_tokens(url, int(idx))) + ", "
                )
            print("triggers:", print_string)
            outputs = eval_with_triggers(url, dev_data, trigger_token_ids, False)
            logits = outputs[1]
            preds = np.argmax(logits, axis=1)
            term = preds == dev_data["labels"].cpu().detach().numpy()
            print("accuracy: ", np.array(term).mean())
        else:
            outputs = process_batch(url, dev_data, "validation")
            logits = outputs[1]
            preds = np.argmax(logits, axis=1)
            term = preds == dev_data["labels"].cpu().detach().numpy()
            print("accuracy: ", np.array(term).mean())
    else:
        train_sampler = RandomSampler(dev_data, replacement=False)
        train_dataloader = DataLoader(
            dev_data,
            batch_size=64,
            sampler=train_sampler,
            collate_fn=default_data_collator,
        )
        if triggers:
            print_string = ""
            for idx in trigger_token_ids:
                print_string = (
                    print_string + str(convert_ids_to_tokens(url, int(idx))) + ", "
                )
            print("triggers:", print_string)
            with torch.no_grad():
                all_vals = []
                for batch in train_dataloader:
                    outputs = eval_with_triggers(url, batch, trigger_token_ids, False)
                    logits = outputs[1]
                    preds = np.argmax(logits.cpu().detach().numpy(), axis=1)
                    term = preds == batch["labels"].cpu().detach().numpy()
                    all_vals.extend(term)
                print("accuracy: ", np.array(all_vals).mean())
        else:
            with torch.no_grad():
                all_vals = []
                for batch in train_dataloader:
                    # print("test1")
                    # print(batch["uid"])
                    # print(dev_data[0])
                    # print(process_batch(url, dev_data[0], "validation"))
                    # print(dev_data[0]["label"])
                    # print("test2")
                    outputs = process_batch(url, batch, "validation")
                    logits = outputs[1]
                    # print(logits)
                    preds = np.argmax(logits, axis=1)
                    # print(sum(preds))
                    # print(sum(batch["labels"].cpu().detach().numpy()))
                    term = preds == batch["labels"].cpu().detach().numpy()

                    all_vals.extend(term)

            print("accuracy: ", np.array(all_vals).mean())


def eval_with_triggers(
    url: nn.Module, batch, trigger_token_ids: List[int], gradient=True
) -> Dict[str, Any]:
    """ 
        Evaluate the batch with triggers appended to them. 
        If gradient is true, this function returns the gradient of the input with the appended trigger tokens.
        Can choose whether to return gradient or model output.
    """
    trigger_sequence_tensor = torch.LongTensor(deepcopy(trigger_token_ids))
    attention_mask_tensor = torch.LongTensor([1, 1, 1])
    token_type_ids = torch.LongTensor([0, 0, 0])
    with torch.cuda.device(1):
        trigger_sequence_tensor = trigger_sequence_tensor.repeat(
            len(batch["labels"]), 1
        ).to(1)
        original_tokens = batch["input_ids"].clone().to(1)

        attention_mask_tensor = attention_mask_tensor.repeat(
            len(batch["labels"]), 1
        ).to(1)
        original_attention_mask = batch["attention_mask"].clone().to(1)

        token_type_ids_tensor = token_type_ids.repeat(len(batch["labels"]), 1).to(1)
        original_token_type_ids = batch["token_type_ids"].clone().to(1)

    def hook_add_trigger_tokens(x):

        x["input_ids"] = torch.cat(
            (original_tokens[:, :1], trigger_sequence_tensor, original_tokens[:, 1:]), 1
        )
        x["attention_mask"] = torch.cat(
            (
                original_attention_mask[:, :1],
                attention_mask_tensor,
                original_attention_mask[:, 1:],
            ),
            1,
        )
        x["token_type_ids"] = torch.cat(
            (
                original_token_type_ids[:, :1],
                token_type_ids_tensor,
                original_token_type_ids[:, 1:],
            ),
            1,
        )
        return x

    if gradient:
        print("evaluate:", batch["input_ids"])
        data_grad = process_batch(
            url, batch, "validation", hook_add_trigger_tokens, gradient=True
        )
        # batch["input_ids"] = original_tokens
        # batch["attention_mask"] = original_attention_mask
        # batch["token_type_ids"] = original_token_type_ids
        return data_grad
    else:
        outputs = process_batch(url, batch, "validation", hook_add_trigger_tokens)
        # batch["input_ids"] = original_tokens
        # batch["attention_mask"] = original_attention_mask
        # batch["token_type_ids"] = original_token_type_ids
        return outputs


def hotflip_attack(
    averaged_grad,
    embedding_matrix,
    trigger_token_ids,
    increase_loss=False,
    num_candidates=1,
) -> List[List[int]]:
    """
    The "Hotflip" attack described in Equation (2) of the paper. This code is heavily inspired by
    the nice code of Paul Michel here https://github.com/pmichel31415/translate/blob/paul/
    pytorch_translate/research/adversarial/adversaries/brute_force_adversary.py
    This function takes in the model's average_grad over a batch of examples, the model's
    token embedding matrix, and the current trigger token IDs. It returns the top token
    candidates for each position.
    If increase_loss=True, then the attack reverses the sign of the gradient and tries to increase
    the loss (decrease the model's probability of the true class). For targeted attacks, you want
    to decrease the loss of the target class (increase_loss=False).
    """
    averaged_grad = torch.FloatTensor(averaged_grad)
    embedding_matrix = torch.FloatTensor(embedding_matrix)
    print(embedding_matrix.shape)
    # trigger_token_embeds = (
    #     torch.nn.functional.embedding(
    #         torch.LongTensor(trigger_token_ids), embedding_matrix
    #     )
    #     .detach()
    #     .unsqueeze(0)
    # )
    averaged_grad = averaged_grad.unsqueeze(0)
    gradient_dot_embedding_matrix = torch.einsum(
        "bij,kj->bik", (averaged_grad, embedding_matrix)
    )
    if not increase_loss:
        gradient_dot_embedding_matrix *= (
            -1
        )  # lower versus increase the class probability.
    if num_candidates > 1:  # get top k options
        _, best_k_ids = torch.topk(gradient_dot_embedding_matrix, num_candidates, dim=2)
        return best_k_ids.detach().cpu().numpy()[0]
    _, best_at_each_step = gradient_dot_embedding_matrix.max(2)

    return best_at_each_step[0].detach().cpu().numpy()


def get_loss_per_candidate(
    index, url, batch, trigger_token_ids, cand_trigger_token_ids, snli=False
):
    """
    For a particular index, the function tries all of the candidate tokens for that index.
    The function returns a list containing the candidate triggers it tried, along with their loss.
    """
    if isinstance(cand_trigger_token_ids[0], (np.int64, int)):
        print("Only 1 candidate for index detected, not searching")
        return trigger_token_ids
    loss_per_candidate = []
    # loss for the trigger without trying the candidates
    curr_loss = eval_with_triggers(url, batch, trigger_token_ids, False)[0]
    loss_per_candidate.append((deepcopy(trigger_token_ids), curr_loss))
    for cand_id in range(len(cand_trigger_token_ids[0])):
        trigger_token_ids_one_replaced = deepcopy(trigger_token_ids)  # copy trigger
        trigger_token_ids_one_replaced[index] = cand_trigger_token_ids[index][
            cand_id
        ]  # replace one token
        loss = eval_with_triggers(url, batch, trigger_token_ids_one_replaced, False)[0]
        loss_per_candidate.append((deepcopy(trigger_token_ids_one_replaced), loss))
    return loss_per_candidate


def get_best_candidates(
    url, batch, trigger_token_ids, cand_trigger_token_ids, snli=False, beam_size=1,
) -> List[int]:
    """"
    Given the list of candidate trigger token ids (of number of trigger words by number of candidates
    per word), it finds the best new candidate trigger.
    This performs beam search in a left to right fashion.
    """
    # first round, no beams, just get the loss for each of the candidates in index 0.
    # (indices 1-end are just the old trigger)
    loss_per_candidate = get_loss_per_candidate(
        0, url, batch, trigger_token_ids, cand_trigger_token_ids, snli
    )
    # maximize the loss
    top_candidates = heapq.nlargest(beam_size, loss_per_candidate, key=itemgetter(1))

    # top_candidates now contains beam_size trigger sequences, each with a different 0th token
    for idx in range(
        1, len(trigger_token_ids)
    ):  # for all trigger tokens, skipping the 0th (we did it above)
        loss_per_candidate = []
        for (
            cand,
            _,
        ) in top_candidates:  # for all the beams, try all the candidates at idx
            loss_per_candidate.extend(
                get_loss_per_candidate(
                    idx, url, batch, cand, cand_trigger_token_ids, snli
                )
            )
        top_candidates = heapq.nlargest(
            beam_size, loss_per_candidate, key=itemgetter(1)
        )
    return max(top_candidates, key=itemgetter(1))[0]


def convert_tokens_to_ids(url, text):
    data = {"Application_Name": "Universal_Attack", "text": text}
    final_url = "{0}/convert_tokens_to_ids".format(url)
    response = requests.post(final_url, data=data)
    retruned_json = response.json()
    return retruned_json["data"]


def convert_ids_to_tokens(url, id):
    data = {"Application_Name": "Universal_Attack", "text": id}
    final_url = "{0}/convert_ids_to_tokens".format(url)
    response = requests.post(final_url, data=data)
    retruned_json = response.json()
    return retruned_json["data"]


def process_batch(url, batch, data_type, hook=lambda x: x, gradient=False):
    method_class = pred_hook(hook)
    pickled_method = method_class.as_pickle()  # pickle.dumps(method_class, protocol=2)
    if isinstance(batch["uid"], int):
        uids = [batch["uid"]]
    else:
        uids = batch["uid"].numpy().tolist()

    payload = {
        "Application_Name": "Universal_Attack",
        "uids": uids,
        "data_type": data_type,
    }
    final_url = url + "/get_batch_output"
    if gradient:
        final_url = url + "/get_batch_input_gradient"
    response = requests.post(
        final_url, data=payload, files={"file": ("holder", pickled_method)}
    )

    outputs = json.loads(response.json()["outputs"])
    return outputs


def test(url, device):
    dataset_label_filter = 0
    # dev_data = model_wrapper.validation_data.get_write_data()
    ### TODO make this code more abstract
    data = {"Application_Name": "Universal_Attack", "data_type": "validation"}
    final_url = "{0}/get_data".format(url)
    response = requests.post(final_url, data=data)
    retruned_json = response.json()
    dev_data = []
    for instance in retruned_json["data"]:
        new_instance = {}
        for field in instance:
            if isinstance(instance[field], List):
                # new_instance[field] = torch.Tensor(instance[field])
                new_instance[field] = instance[field]
            else:
                # print(field)
                # print(instance[field])
                # print(type(instance[field]))
                # print(torch.Tensor(instance[field]))
                new_instance[field] = instance[field]
        # print(new_instance)

        dev_data.append(new_instance)

    targeted_dev_data = []
    for idx, instance in enumerate(dev_data):
        # print(instance)
        if instance["label"] == dataset_label_filter:
            targeted_dev_data.append(instance)
    # print(targeted_dev_data[0])
    universal_perturb_batch_size = 64
    num_trigger_tokens = 3

    # data = {"Application_Name": "Universal_Attack"}
    # final_url = "{0}/get_tokenizer".format(url)
    # response = requests.post(final_url, data=data)
    # retruned_json = response.json()

    trigger_token_ids = [convert_tokens_to_ids(url, "the")] * num_trigger_tokens
    iterator_dataloader = DataLoader(
        targeted_dev_data,
        batch_size=universal_perturb_batch_size,
        shuffle=True,
        collate_fn=default_data_collator,
    )

    print("started the process")
    get_accuracy(url, dev_data, trigger_token_ids, False, False)
    # best_triggers = [22775, 17950, 17087]
    # trigger_token_ids = best_triggers
    # get_accuracy(model_wrapper, dev_data, tokenizer, best_triggers, True, False)
    # print(torch.cuda.memory_summary(device=1, abbreviated=True))
    for batch in iterator_dataloader:
        # get accuracy with current triggers
        print("start_batch")
        # print(batch)
        get_accuracy(url, batch, trigger_token_ids, False, True)
        # print(torch.cuda.memory_summary(device=1, abbreviated=True))
        # model.train() # rnn cannot do backwards in train mode
        # get gradient w.r.t. trigger embeddings for current batch
        data_grad = eval_with_triggers(url, batch, trigger_token_ids)
        # print(torch.cuda.memory_summary(device=1, abbreviated=True))
        averaged_grad = np.sum(data_grad, axis=0)
        averaged_grad = averaged_grad[1 : len(trigger_token_ids) + 1]
        # pass the gradients to a particular attack to generate token candidates for each token.
        data = {"Application_Name": "Universal_Attack"}
        final_url = "{0}/get_model_embedding".format(url)
        response = requests.post(final_url, data=data)
        retruned_json = json.loads(response.json()["data"])
        embedding = retruned_json
        print("embedding", len(embedding))
        print(len(embedding[0]))
        embedding_weight = embedding
        cand_trigger_token_ids = hotflip_attack(
            averaged_grad,
            embedding_weight,
            trigger_token_ids,
            num_candidates=40,
            increase_loss=True,
        )
        trigger_token_ids = get_best_candidates(
            url, batch, trigger_token_ids, cand_trigger_token_ids
        )
        print("after:")
        get_accuracy(url, batch, trigger_token_ids, True, True)
    get_accuracy(url, targeted_dev_data, trigger_token_ids, True, False)


class pred_hook:
    def __init__(self, fn):
        self._fn = fn

    def __call__(self, *args, **kwargs):
        return self._fun(*args, **kwargs)

    def as_pickle(self):
        import dill as pickle

        return pickle.dumps(self._fn, protocol=2)


def main():
    # test the server
    url = "http://127.0.0.1:5000"
    a = lambda x: x
    method_class = pred_hook(a)
    pickled_method = method_class.as_pickle()  # pickle.dumps(method_class, protocol=2)
    payload = {
        "Application_Name": "Universal_Attack",
        "uids": [1, 2, 3],
        "data_type": "train",
    }
    final_url = url + "/get_batch_output"
    # print(json.dumps(payload))
    # headers = {"Content-type": "multipart/form-data"}
    # headers = {"Content-type": "application/json", "Accept": "text/plain"}
    response = requests.post(
        final_url, data=payload, files={"file": ("holder", pickled_method)}
    )
    print(response.json())

    test(
        url, 1,
    )


if __name__ == "__main__":
    main()
